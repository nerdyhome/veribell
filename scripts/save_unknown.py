import json
import shutil
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
FRAMES_DIR = BASE_DIR / "frames"
UNKNOWN_DIR = BASE_DIR / "unknown_faces"


def save_unknown(metadata: dict) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    source_id = metadata.get("source_id", "unknown_source")

    data_dir = BASE_DIR / "data"
    target_dir = data_dir / "unknown_faces" / source_id / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)

    frames_dir = Path(metadata["frames_dir"]) if metadata.get("frames_dir") else data_dir / "frames" / source_id

    frames = sorted(frames_dir.glob("frame_*.jpg"))

    for frame in frames:
        shutil.copy2(frame, target_dir / frame.name)

    with open(target_dir / "metadata.json", "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2, ensure_ascii=False)

    print(f"Unknown gespeichert unter: {target_dir}")
    return target_dir


if __name__ == "__main__":
    save_unknown({"manual_test": True})