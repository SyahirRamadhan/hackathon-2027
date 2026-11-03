"""Local frame-by-frame viewer for the tactical ATD tracking data."""

import argparse
import csv
import json
import math
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from games import GAME, GAMES, require, source_frames  # noqa: E402

FRAME_COUNT = source_frames()
FPS = GAMES[GAME]["fps"]
PUBLIC_LABELS_PATH = ROOT / "evaluation" / "gt" / "BAR-ATH_possession_gt_public.csv"
RESULTS_DIR = ROOT / "evaluation" / "results"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
LABEL_NAMES = {
    "H": "Home possession",
    "A": "Away possession",
    "D": "Dead ball",
    "X": "Not labeled",
}


def load_teams(meta_path):
    root = ET.parse(meta_path).getroot()
    teams = list(root.iter("Team"))
    if len(teams) != 2:
        raise ValueError("{}: expected exactly two teams".format(meta_path))

    home_team_id = teams[0].get("id")
    team_slots = [player.get("teamId") == home_team_id for player in root.iter("Player")]
    if not team_slots:
        raise ValueError("{}: no tracking slots found".format(meta_path))
    return [team.findtext("Name") or "Home" for team in teams], team_slots


def _point(value):
    coordinates = value.split(",")
    if len(coordinates) < 2 or coordinates[0].startswith("N") or coordinates[1].startswith("N"):
        return None
    x, y = float(coordinates[0]), float(coordinates[1])
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return {"x": x, "y": y}


def parse_atd_record(line, team_slots):
    """Convert one ``frame:players:ball`` ATD line to JSON-ready coordinates."""
    parts = line.rstrip("\r\n").split(":", 2)
    if len(parts) != 3:
        raise ValueError("Malformed ATD record: expected frame, players and ball")

    frame = int(parts[0])
    players = []
    for slot, raw_point in enumerate(parts[1].split(";")):
        point = _point(raw_point)
        if point is None:
            continue
        if slot >= len(team_slots):
            raise ValueError("ATD record has more player slots than its metadata")
        players.append({
            "team": "home" if team_slots[slot] else "away",
            "slot": slot + 1,
            "x": point["x"],
            "y": point["y"],
        })
    return {"frame": frame - 1, "players": players, "ball": _point(parts[2])}


def index_atd(raw_path, expected_frames):
    """Build a compact byte-offset table so playback can seek directly to any frame."""
    offsets = []
    with open(raw_path, "rb") as tracking:
        for expected_id, line in enumerate(tracking, start=1):
            separator = line.find(b":")
            if separator < 1:
                raise ValueError("Malformed ATD record at line {}".format(expected_id))
            frame_id = int(line[:separator])
            if frame_id != expected_id:
                raise ValueError(
                    "ATD frame sequence breaks at line {} (found frame {})".format(
                        expected_id, frame_id))
            offsets.append(tracking.tell() - len(line))
    if len(offsets) != expected_frames:
        raise ValueError(
            "Expected {} ATD frames, found {}".format(expected_frames, len(offsets)))
    return offsets


def load_labels(path, frame_count):
    """Load one frame,label CSV into a compact one-byte-per-frame lookup."""
    labels = bytearray(b"X" * frame_count)
    seen = set()
    with open(path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not {"frame", "label"}.issubset(reader.fieldnames):
            raise ValueError("{}: expected frame,label columns".format(path))
        for row in reader:
            frame = int(row["frame"])
            label = row["label"].strip().upper()
            if frame < 0 or frame >= frame_count:
                raise ValueError("{}: frame {} is outside the match".format(path, frame))
            if label not in LABEL_NAMES:
                raise ValueError("{}: invalid label {!r}".format(path, label))
            if frame in seen:
                raise ValueError("{}: duplicate label for frame {}".format(path, frame))
            labels[frame] = ord(label)
            seen.add(frame)
    return bytes(labels)


def load_label_sources(frame_count):
    sources = {}
    options = []
    for path in sorted(RESULTS_DIR.glob("baseline_*.csv")):
        source_id = path.stem
        sources[source_id] = load_labels(path, frame_count)
        options.append({
            "id": source_id,
            "name": source_id.replace("_", " "),
            "description": "Estimate; D can indicate a missing ball track",
        })

    if PUBLIC_LABELS_PATH.is_file():
        sources["public_gt"] = load_labels(PUBLIC_LABELS_PATH, frame_count)
        options.append({
            "id": "public_gt",
            "name": "Public ground truth",
            "description": "Released labels cover only part of the match",
        })
    if not options:
        raise FileNotFoundError("No baseline or public ground-truth label files were found")
    return sources, options


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, raw_path, meta_path):
        self.frame_offsets = index_atd(raw_path, FRAME_COUNT)
        self.team_names, self.team_slots = load_teams(meta_path)
        self.label_sources, self.label_options = load_label_sources(FRAME_COUNT)
        super().__init__(address, handler)


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "TacticalViewer/1.0"

    def do_GET(self):
        request = urlparse(self.path)
        if request.path == "/api/meta":
            self._send_json({
                "frameCount": FRAME_COUNT,
                "fps": FPS,
                "teamNames": self.server.team_names,
                "labelSources": self.server.label_options,
            })
        elif request.path == "/api/frames":
            self._send_frames(parse_qs(request.query))
        elif request.path in STATIC_FILES:
            self._send_static(*STATIC_FILES[request.path])
        else:
            self.send_error(404, "Not found")

    def _send_static(self, filename, content_type):
        try:
            content = (Path(__file__).resolve().parent / filename).read_bytes()
        except OSError as error:
            self.send_error(500, "Could not read {}: {}".format(filename, error))
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload, status=200):
        content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_frames(self, query):
        try:
            start = int(query.get("start", [""])[0])
            count = int(query.get("count", ["50"])[0])
            label_source = query.get("labels", ["baseline_smooth50"])[0]
            if count < 1 or count > 100:
                raise ValueError("count must be between 1 and 100")
            if start < 0 or start >= FRAME_COUNT:
                raise ValueError("start is outside the match")
            if label_source not in self.server.label_sources:
                raise ValueError("unknown label source")

            end = min(start + count, FRAME_COUNT)
            records = []
            raw_path = require(["atd_raw"])[0]
            with open(raw_path, "rb") as tracking:
                for frame in range(start, end):
                    tracking.seek(self.server.frame_offsets[frame])
                    record = parse_atd_record(
                        tracking.readline().decode("ascii"), self.server.team_slots)
                    record["label"] = chr(self.server.label_sources[label_source][frame])
                    records.append(record)
            self._send_json({"frames": records})
        except (ValueError, KeyError, OSError, UnicodeError) as error:
            self._send_json({"error": str(error)}, status=400)

    def log_message(self, format_string, *args):
        print("{} - {}".format(self.address_string(), format_string % args))


def main():
    parser = argparse.ArgumentParser(description="Play back tactical tracking frame by frame.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    args = parser.parse_args()

    raw_path, meta_path = require(["atd_raw", "atd_meta"])
    print("Indexing {} tracking frames...".format(FRAME_COUNT), flush=True)
    server = ViewerServer((args.host, args.port), ViewerHandler, raw_path, meta_path)
    print("Viewer ready at http://{}:{}/".format(args.host, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping viewer.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
