const NS = "http://www.w3.org/2000/svg";
const BATCH_SIZE = 50;
const SKIP_FRAMES = 5 * 25;

const elements = {
  markers: document.getElementById("markers"),
  trackingStatus: document.getElementById("tracking-status"),
  stateIndicator: document.getElementById("state-indicator"),
  stateLabel: document.getElementById("state-label"),
  stateDetail: document.getElementById("state-detail"),
  labelSource: document.getElementById("label-source"),
  sourceNote: document.getElementById("source-note"),
  frameNumber: document.getElementById("frame-number"),
  timecode: document.getElementById("timecode"),
  trackedCount: document.getElementById("tracked-count"),
  homeName: document.getElementById("home-name"),
  awayName: document.getElementById("away-name"),
  timeline: document.getElementById("timeline"),
  timelineTime: document.getElementById("timeline-time"),
  duration: document.getElementById("duration"),
  frameCaption: document.getElementById("frame-caption"),
  playButton: document.getElementById("play-button"),
  error: document.getElementById("error-message"),
};

const state = {
  fps: 25,
  frameCount: 0,
  frame: 0,
  frames: new Map(),
  pending: new Map(),
  playing: false,
  lastTick: 0,
  fractionalFrames: 0,
};

function formatTime(frame) {
  const totalSeconds = Math.floor(frame / state.fps);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const centiseconds = Math.floor((frame % state.fps) * 100 / state.fps);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(centiseconds).padStart(2, "0")}`;
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.hidden = false;
}

async function fetchFrames(start, count = BATCH_SIZE) {
  if (count === BATCH_SIZE) start = Math.floor(start / BATCH_SIZE) * BATCH_SIZE;
  start = Math.max(0, Math.min(start, state.frameCount - 1));
  const source = elements.labelSource.value;
  const key = `${source}:${start}:${count}`;
  if (state.pending.has(key)) return state.pending.get(key);

  const request = fetch(`/api/frames?start=${start}&count=${count}&labels=${encodeURIComponent(source)}`)
    .then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Frame request failed (${response.status})`);
      payload.frames.forEach((frame) => state.frames.set(`${source}:${frame.frame}`, frame));
      const keepFrom = Math.max(0, start - 100);
      const keepTo = Math.min(state.frameCount - 1, start + count + 100);
      for (const cachedKey of state.frames.keys()) {
        const cachedFrame = Number(cachedKey.slice(cachedKey.lastIndexOf(":") + 1));
        if (cachedFrame < keepFrom || cachedFrame > keepTo) state.frames.delete(cachedKey);
      }
      return payload.frames;
    })
    .catch((error) => {
      showError(error.message);
      throw error;
    })
    .finally(() => state.pending.delete(key));
  state.pending.set(key, request);
  return request;
}

function currentFrameData() {
  return state.frames.get(`${elements.labelSource.value}:${state.frame}`);
}

function drawFrame(frame) {
  const dots = [];
  frame.players.forEach((player) => {
    const circle = document.createElementNS(NS, "circle");
    circle.setAttribute("class", `player-dot ${player.team}`);
    circle.setAttribute("cx", String(player.x * 1050));
    circle.setAttribute("cy", String(player.y * 680));
    circle.setAttribute("r", "10");
    circle.setAttribute("data-slot", String(player.slot));
    const title = document.createElementNS(NS, "title");
    title.textContent = `${player.team === "home" ? elements.homeName.textContent : elements.awayName.textContent} · track ${player.slot}`;
    circle.appendChild(title);
    dots.push(circle);
  });

  if (frame.ball) {
    const ball = document.createElementNS(NS, "circle");
    ball.setAttribute("class", "ball-dot");
    ball.setAttribute("cx", String(frame.ball.x * 1050));
    ball.setAttribute("cy", String(frame.ball.y * 680));
    ball.setAttribute("r", "7");
    const title = document.createElementNS(NS, "title");
    title.textContent = "Tracked ball";
    ball.appendChild(title);
    dots.push(ball);
  }
  elements.markers.replaceChildren(...dots);

  const labels = {
    H: { title: "Home possession", className: "home", detail: `${elements.homeName.textContent} in possession` },
    A: { title: "Away possession", className: "away", detail: `${elements.awayName.textContent} in possession` },
    D: {
      title: "Dead ball",
      className: "dead",
      detail: elements.labelSource.value === "public_gt"
        ? "Ball out of play"
        : "Estimate; ball tracking may be missing",
    },
    X: { title: "Not labeled", className: "unknown", detail: "No label for this frame" },
  };
  const label = labels[frame.label] || labels.X;
  elements.stateLabel.textContent = label.title;
  elements.stateIndicator.className = `state-indicator ${label.className}`;
  elements.stateDetail.textContent = label.detail;
  elements.trackingStatus.textContent = frame.ball ? "BALL TRACKED" : "BALL NOT TRACKED";
  elements.trackingStatus.classList.toggle("missing", !frame.ball);
  elements.trackedCount.textContent = `${frame.players.length} players tracked`;
  elements.frameNumber.textContent = String(frame.frame).padStart(6, "0");
  elements.timecode.textContent = formatTime(frame.frame);
  elements.timelineTime.textContent = formatTime(frame.frame);
  elements.frameCaption.textContent = `FRAME ${frame.frame.toLocaleString()} / ${(state.frameCount - 1).toLocaleString()}`;
}

function setFrame(frame) {
  state.frame = Math.max(0, Math.min(Math.round(frame), state.frameCount - 1));
  elements.timeline.value = String(state.frame);
  elements.timeline.style.setProperty(
    "--progress",
    `${state.frame / Math.max(1, state.frameCount - 1) * 100}%`,
  );
  const data = currentFrameData();
  if (data) {
    drawFrame(data);
    const nextBatch = Math.floor(state.frame / BATCH_SIZE) * BATCH_SIZE + BATCH_SIZE;
    if (state.frame >= nextBatch - 12) {
      fetchFrames(nextBatch).catch(() => {});
    }
    return;
  }
  fetchFrames(state.frame).then(() => {
    const loaded = currentFrameData();
    if (loaded) drawFrame(loaded);
  }).catch(() => {});
}

function setPlaying(playing) {
  state.playing = playing;
  state.lastTick = 0;
  elements.playButton.classList.toggle("is-playing", playing);
  elements.playButton.setAttribute("aria-label", playing ? "Pause" : "Play");
  if (playing) requestAnimationFrame(tick);
}

function tick(timestamp) {
  if (!state.playing) return;
  if (!state.lastTick) state.lastTick = timestamp;
  state.fractionalFrames += (timestamp - state.lastTick) * state.fps / 1000;
  state.lastTick = timestamp;

  const advance = Math.floor(state.fractionalFrames);
  if (advance > 0) {
    const target = Math.min(state.frame + advance, state.frameCount - 1);
    state.fractionalFrames -= advance;
    const cached = state.frames.get(`${elements.labelSource.value}:${target}`);
    if (cached) {
      state.frame = target;
      elements.timeline.value = String(target);
      drawFrame(cached);
    } else {
      fetchFrames(target, BATCH_SIZE).catch(() => {});
    }
    if (target >= state.frameCount - 1) setPlaying(false);
  }
  requestAnimationFrame(tick);
}

function onLabelSourceChange() {
  state.frames.clear();
  setFrame(state.frame);
}

async function initialize() {
  try {
    const response = await fetch("/api/meta");
    const meta = await response.json();
    if (!response.ok) throw new Error(meta.error || "Could not load match metadata");

    state.fps = meta.fps;
    state.frameCount = meta.frameCount;
    elements.timeline.max = String(meta.frameCount - 1);
    elements.homeName.textContent = meta.teamNames[0];
    elements.awayName.textContent = meta.teamNames[1];
    elements.duration.textContent = formatTime(meta.frameCount);
    meta.labelSources.forEach((source) => {
      const option = document.createElement("option");
      option.value = source.id;
      option.textContent = source.name;
      option.dataset.description = source.description;
      elements.labelSource.appendChild(option);
    });
    const preferred = meta.labelSources.find((source) => source.id === "baseline_smooth50");
    if (preferred) elements.labelSource.value = preferred.id;
    elements.sourceNote.textContent = elements.labelSource.selectedOptions[0].dataset.description || "";

    const kickoff = 7 * 60 * state.fps + 27 * state.fps + 11;
    state.frame = Math.min(kickoff, meta.frameCount - 1);
    elements.timeline.value = String(state.frame);
    await fetchFrames(state.frame, BATCH_SIZE);
    setFrame(state.frame);
    elements.labelSource.addEventListener("change", () => {
      elements.sourceNote.textContent = elements.labelSource.selectedOptions[0]?.dataset.description || "";
      onLabelSourceChange();
    });
  } catch (error) {
    showError(error.message);
  }
}

elements.playButton.addEventListener("click", () => setPlaying(!state.playing));
document.getElementById("back-button").addEventListener("click", () => setFrame(state.frame - SKIP_FRAMES));
document.getElementById("forward-button").addEventListener("click", () => setFrame(state.frame + SKIP_FRAMES));
document.getElementById("start-button").addEventListener("click", () => setFrame(0));
document.getElementById("end-button").addEventListener("click", () => setFrame(state.frameCount - 1));
elements.timeline.addEventListener("input", () => setFrame(Number(elements.timeline.value)));

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, select, textarea, button")) return;
  if (event.code === "Space") {
    event.preventDefault();
    setPlaying(!state.playing);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    setFrame(state.frame + (event.shiftKey ? -SKIP_FRAMES : -1));
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    setFrame(state.frame + (event.shiftKey ? SKIP_FRAMES : 1));
  }
});

initialize();
