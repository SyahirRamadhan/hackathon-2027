from games import asset_paths, half_video_blocks
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as patches

paths = asset_paths()                    # where are the files?
blocks = half_video_blocks("BAR-ATH")    # which frames count?

def main_function():
    print("Path is: ", paths.get('atd_raw'))
    txt_path = paths.get('atd_raw')
    txt_path = Path(txt_path)

    output_dir = Path("frame_plots")
    output_dir.mkdir(exist_ok=True)

    i = 0
    with open(txt_path, "r") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue # Skip empty lines
                
            if ":" in line:
                frame_id, data = line.split(":", 1)
                i = i + 1

                x_coords = []
                y_coords = []
                single_data = data.split(";")

                # --- STEP 1: Loop only to COLLECT coordinates ---
                for s_data in single_data:
                    parts = s_data.split(",")
                    if len(parts) >= 2:
                        x_str, y_str = parts[0], parts[1]
                        
                        if "NaN" in x_str or "NaN" in y_str:
                            continue
                            
                        x_coords.append(float(x_str))
                        y_coords.append(float(y_str))

                # --- STEP 2: PLOT ALL PLAYERS AT ONCE (Out of the inner loop) ---
                if x_coords: # Only create a plot if we successfully found active coordinates
                    fig, ax = plt.subplots(figsize=(10, 7))

                    # Create a bounding rectangle (Field dimensions)
                    field = patches.Rectangle((0, 0), 1, 1, linewidth=2, edgecolor='black', facecolor='none')
                    ax.add_patch(field)

                    # Plot all collected coordinates as dots inside the box at the same time
                    ax.scatter(x_coords, y_coords, color='blue', marker='o', s=50, label='Players')

                    # Clean up graph boundaries
                    ax.set_xlim(-0.05, 1.05)
                    ax.set_ylim(-0.05, 1.05)
                    ax.set_aspect('equal') 
                    plt.title(f"Player Positions - Frame {frame_id}")
                    plt.legend()

                    # Save the interactive plot window
                    image_path = output_dir / f"frame_{frame_id}.png"
                    plt.savefig(image_path)
                    print(f"Saved: {image_path}")
                    
                    # Close the figure memory so your computer doesn't crash
                    plt.close(fig)

            if i > 200:
                break

if __name__ == "__main__":
    main_function()
