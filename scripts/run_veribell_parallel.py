import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from action_runner import run_actions
import threading


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.json"

_RECOGNIZER_READY = threading.Event()
_RECOGNIZE_SINGLE_FRAME = None
_RECOGNIZER_IMPORT_ERROR = None

def get_data_dir(config: dict) -> Path:
    data_dir = config.get("data_dir", "data")
    return BASE_DIR / data_dir

def get_frames_dir(config: dict, source_id: str, source_config: dict) -> Path:
    frames_dir = source_config.get("frames_dir")

    if frames_dir:
        return BASE_DIR / frames_dir

    return get_data_dir(config) / "frames" / source_id


def preload_recognizer() -> None:
    global _RECOGNIZE_SINGLE_FRAME, _RECOGNIZER_IMPORT_ERROR

    try:
        from scripts.recognize_frames import recognize_single_frame
        _RECOGNIZE_SINGLE_FRAME = recognize_single_frame
    except Exception as error:
        _RECOGNIZER_IMPORT_ERROR = error
    finally:
        _RECOGNIZER_READY.set()


def recognize_frame_lazy(frame_path: Path, config: dict) -> dict:
    _RECOGNIZER_READY.wait()

    if _RECOGNIZER_IMPORT_ERROR is not None:
        return {
            "status": "error",
            "frame": frame_path.name,
            "subject": None,
            "similarity": 0.0,
            "error": f"Recognizer import failed: {_RECOGNIZER_IMPORT_ERROR}",
        }

    return _RECOGNIZE_SINGLE_FRAME(frame_path, config)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def clean_frames(frames_dir: Path) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)

    for file in frames_dir.glob("frame_*.jpg"):
        file.unlink()

    test_file = frames_dir / "test.jpg"
    if test_file.exists():
        test_file.unlink()


def is_file_ready(path: Path, wait_seconds: float = 0.15) -> bool:
    """
    Verhindert, dass wir ein Bild lesen, während ffmpeg noch schreibt.
    Prüft, ob die Dateigröße kurz stabil bleibt.
    """
    if not path.exists():
        return False

    size_1 = path.stat().st_size
    if size_1 <= 0:
        return False

    time.sleep(wait_seconds)

    if not path.exists():
        return False

    size_2 = path.stat().st_size

    return size_1 == size_2 and size_2 > 0

def build_video_filter(config: dict, source_config: dict) -> str:
    fps = str(source_config.get("frames_per_second", config.get("frames_per_second", 2)))
    frame_width = int(source_config.get("frame_width", config.get("frame_width", 960)))

    filters = [
        f"fps={fps}",
        f"scale={frame_width}:-1"
    ]

    crop = source_config.get("crop", {})

    if crop.get("enabled", False):
        width_pct = float(crop.get("width_pct", 1.0))
        height_pct = float(crop.get("height_pct", 1.0))
        x_pct = float(crop.get("x_pct", 0.0))
        y_pct = float(crop.get("y_pct", 0.0))

        filters.append(
            f"crop=iw*{width_pct}:ih*{height_pct}:iw*{x_pct}:ih*{y_pct}"
        )

    return ",".join(filters)

def build_ffmpeg_command(config: dict, source_id: str, source_config: dict, frames_dir: Path) -> list[str]:
    ffmpeg_path = config["ffmpeg_path"]
    rtsp_url = source_config.get("rtsp_url")

    if not rtsp_url:
        raise RuntimeError(f"Source {source_id} hat keine rtsp_url.")

    max_capture_seconds = str(
        source_config.get("max_capture_seconds", config.get("max_capture_seconds", 10))
    )

    video_filter = build_video_filter(config, source_config)

    output_pattern = str(frames_dir / "frame_%03d.jpg")

    return [
        ffmpeg_path,
        "-y",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-an",
        "-t", max_capture_seconds,
        "-vf", video_filter,
        output_pattern,
    ]


def start_ffmpeg(config: dict, source_id: str, source_config: dict, frames_dir: Path) -> subprocess.Popen:
    command = build_ffmpeg_command(config, source_id, source_config, frames_dir)

    print("Starte ffmpeg parallel...")
    print(" ".join(command))

    return subprocess.Popen(
        command,
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True
    )


def stop_ffmpeg(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    print("Stoppe ffmpeg")

    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run_parallel_gate(source_id: str = "default", source_config: dict | None = None) -> dict:
    config = load_config()

    if source_config is None:
        sources = config.get("sources", {})

        if source_id not in sources:
            raise RuntimeError(f"Source {source_id} nicht in settings.json gefunden.")

        source_config = sources[source_id]

    if not source_config.get("rtsp_url"):
        raise RuntimeError(f"Source {source_id} hat keine rtsp_url.")

    min_hits = int(config.get("min_hits", 2))
    frames_dir = get_frames_dir(config, source_id, source_config)

    clean_frames(frames_dir)

    process = start_ffmpeg(config, source_id, source_config, frames_dir)

    if not _RECOGNIZER_READY.is_set():
        recognizer_thread = threading.Thread(target=preload_recognizer, daemon=True)
        recognizer_thread.start()

    checked_frames: set[Path] = set()
    hits_by_subject: dict[str, list[float]] = defaultdict(list)
    unknown_seen = False
    no_face_count = 0
    error_count = 0

    started_at = time.perf_counter()
    max_total_seconds = float(config.get("max_total_seconds", 90))
    hard_deadline = started_at + max_total_seconds

    try:
        while time.perf_counter() < hard_deadline:
            # Neue Frames suchen
            frame_files = sorted(frames_dir.glob("frame_*.jpg"))

            for frame_path in frame_files:
                if frame_path in checked_frames:
                    continue

                if not is_file_ready(frame_path):
                    continue

                checked_frames.add(frame_path)

                elapsed = time.perf_counter() - started_at
                print(f"{datetime.now().strftime('%H:%M:%S')} Prüfe {frame_path.name} nach {elapsed:.2f}s...")

                frame_result = recognize_frame_lazy(frame_path, config)
                status = frame_result["status"]

                if status == "known":
                    subject = frame_result["subject"]
                    similarity = float(frame_result["similarity"])

                    hits_by_subject[subject].append(similarity)

                    print(f"  Treffer: {subject} similarity={similarity:.3f} "
                          f"({len(hits_by_subject[subject])}/{min_hits})")

                    if len(hits_by_subject[subject]) >= min_hits:
                        stop_ffmpeg(process)

                        avg = sum(hits_by_subject[subject]) / len(hits_by_subject[subject])

                        return {
                            "status": "known",
                            "subject": subject,
                            "hits": len(hits_by_subject[subject]),
                            "avg_similarity": avg,
                            "all_hits": dict(hits_by_subject),
                            "frames_checked": len(checked_frames),
                            "source_id": source_id,
                            "source_name": source_config.get("display_name", source_id),
                            "frames_dir": str(frames_dir),
                        }
                elif status == "unknown":
                    unknown_seen = True
                    print(f"  Gesicht erkannt, aber nicht sicher bekannt: "
                          f"{frame_result.get('subject')} similarity={frame_result.get('similarity'):.3f}")

                elif status == "no_face":
                    no_face_count += 1
                    print("  Kein Gesicht erkannt.")

                else:
                    error_count += 1
                    print(f"  Fehler: {frame_result.get('error')}")

            # Wenn ffmpeg fertig ist, prüfen wir trotzdem noch alle bereits erzeugten Frames.
            if process.poll() is not None:
                remaining = [
                    p for p in sorted(frames_dir.glob("frame_*.jpg"))
                    if p not in checked_frames
                ]

                ready_remaining = [p for p in remaining if is_file_ready(p)]

                if not remaining or not ready_remaining:
                    break

            time.sleep(0.1)

    finally:
        stop_ffmpeg(process)

    # Nach Ende: Ergebnis ohne known
    if unknown_seen:
        return {
            "status": "unknown",
            "subject": None,
            "hits": 0,
            "avg_similarity": 0.0,
            "all_hits": dict(hits_by_subject),
            "frames_checked": len(checked_frames),
            "no_face_count": no_face_count,
            "error_count": error_count,
            "source_id": source_id,
            "source_name": source_config.get("display_name", source_id),
            "frames_dir": str(frames_dir),
        }

    if no_face_count > 0 and len(checked_frames) > 0:
        return {
            "status": "no_face",
            "subject": None,
            "hits": 0,
            "avg_similarity": 0.0,
            "all_hits": dict(hits_by_subject),
            "frames_checked": len(checked_frames),
            "no_face_count": no_face_count,
            "error_count": error_count,
            "source_id": source_id,
            "source_name": source_config.get("display_name", source_id),
            "frames_dir": str(frames_dir),
        }

    if hits_by_subject:
        best_subject = max(
            hits_by_subject,
            key=lambda subject: len(hits_by_subject[subject])
        )
        similarities = hits_by_subject[best_subject]
        avg = sum(similarities) / len(similarities)

        return {
            "status": "unknown",
            "subject": None,
            "hits": len(similarities),
            "avg_similarity": avg,
            "all_hits": dict(hits_by_subject),
            "frames_checked": len(checked_frames),
            "no_face_count": no_face_count,
            "error_count": error_count,
            "note": f"Teiltreffer für {best_subject}, aber min_hits nicht erreicht.",
            "source_id": source_id,
            "source_name": source_config.get("display_name", source_id),
            "frames_dir": str(frames_dir),
        }
    
    return {
        "status": "error",
        "subject": None,
        "hits": 0,
        "avg_similarity": 0.0,
        "all_hits": dict(hits_by_subject),
        "frames_checked": len(checked_frames),
        "no_face_count": no_face_count,
        "error_count": error_count,
        "note": "Kein verwertbares Ergebnis im Parallel-Lauf.",
        "source_id": source_id,
        "source_name": source_config.get("display_name", source_id),
        "frames_dir": str(frames_dir),
    }


def main() -> int:

    print("=== Ring Face Gate Parallel PoC ===")

    total_start = time.perf_counter()
    config = load_config()

    try:
        sources = config.get("sources", {})
        source_id = next((sid for sid, src in sources.items() if src.get("enabled", True)), None)

        if not source_id:
            raise RuntimeError("Keine aktivierte Source in settings.json gefunden.")

        source_config = sources[source_id]
        result = run_parallel_gate(source_id, source_config)
    except Exception as error:
        print(f"Fehler im Parallel-Lauf: {error}")
        return 1

    total_end = time.perf_counter()
    print(f"\nGesamtzeit Parallel: {total_end - total_start:.2f} Sekunden")

    event = {
        "trigger_id": "manual_direct_test",
        "trigger_name": "Manueller Direkt-Test",
        "trigger_type": "manual",
        "source_id": result.get("source_id"),
        "source_name": result.get("source_name"),
        "source_type": source_config.get("type"),
        "event_type": "manual_test"
    }

    return run_actions(event, result, config)


if __name__ == "__main__":
    sys.exit(main())