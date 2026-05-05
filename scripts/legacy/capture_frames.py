import json
import subprocess
import time
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.json"
FRAMES_DIR = BASE_DIR / "frames"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def clean_frames() -> None:
    FRAMES_DIR.mkdir(exist_ok=True)

    for file in FRAMES_DIR.glob("frame_*.jpg"):
        file.unlink()

    test_file = FRAMES_DIR / "test.jpg"
    if test_file.exists():
        test_file.unlink()


def run_ffmpeg_once(config: dict) -> bool:
    ffmpeg_path = config["ffmpeg_path"]
    rtsp_url = config["rtsp_url"]

    fps = str(config.get("frames_per_second", 2))
    max_frames = str(config.get("max_frames", 6))

    output_pattern = str(FRAMES_DIR / "frame_%03d.jpg")

    command = [
        ffmpeg_path,
        "-y",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-an",
        "-vf", f"fps={fps},scale=640:-1",
        "-frames:v", max_frames,
        output_pattern,
    ]

    print("Starte schnellen Frame-Capture...")
    print(" ".join(command))

    try:
        print(datetime.now().strftime("%H:%M:%S"), "ffmpeg startet jetzt...")
        result = subprocess.run(command, timeout=25)
        print(datetime.now().strftime("%H:%M:%S"), "ffmpeg beendet.")
    except subprocess.TimeoutExpired:
        print("ffmpeg Timeout nach 25 Sekunden.")
        return False

    if result.returncode != 0:
        print(f"ffmpeg Fehlercode: {result.returncode}")
        return False

    frames = sorted(FRAMES_DIR.glob("frame_*.jpg"))
    print(f"Gespeicherte Frames: {len(frames)}")

    return len(frames) > 0


def capture_frames() -> None:
    config = load_config()
    clean_frames()

    # Ring braucht nach dem Ding-Event oft kurz, bis der Stream wirklich verfügbar ist.
    initial_delay = float(config.get("initial_stream_delay_seconds", 3))
    retries = int(config.get("stream_retries", 3))
    retry_delay = float(config.get("stream_retry_delay_seconds", 2))

    print(datetime.now().strftime("%H:%M:%S"), f"Warte {initial_delay} Sekunden auf Ring-Stream...")
    time.sleep(initial_delay)

    for attempt in range(1, retries + 1):
        print(f"Capture-Versuch {attempt}/{retries}")

        if run_ffmpeg_once(config):
            return

        if attempt < retries:
            print(f"Warte {retry_delay} Sekunden und versuche erneut...")
            time.sleep(retry_delay)

    raise RuntimeError("Keine Frames gespeichert. Ring-Stream war nicht rechtzeitig verfügbar.")


if __name__ == "__main__":
    capture_frames()