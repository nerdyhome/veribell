import json
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.json"
FRAMES_DIR = BASE_DIR / "frames"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def recognize_frame(frame_path: Path, config: dict) -> Optional[Dict[str, Any]]:
    url = config["compreface_url"].rstrip("/") + "/api/v1/recognition/recognize"
    api_key = config["compreface_api_key"]

    params = {
        "limit": 1,
        "prediction_count": 1,
        "det_prob_threshold": 0.8
    }

    headers = {
        "x-api-key": api_key
    }

    with open(frame_path, "rb") as image_file:
        response = requests.post(
            url,
            headers=headers,
            params=params,
            files={"file": image_file},
            timeout=60
        )

    response.raise_for_status()
    data = response.json()

    result = data.get("result", [])

    if not result:
        return None

    face = result[0]
    subjects = face.get("subjects", [])

    if not subjects:
        return None

    best = subjects[0]

    subject = best.get("subject")
    similarity = best.get("similarity", 0)

    if not subject:
        return None

    return {
        "frame": frame_path.name,
        "subject": subject,
        "similarity": float(similarity),
        "raw": data
    }


def recognize_all_frames() -> dict:
    config = load_config()
    min_similarity = float(config.get("min_similarity", 0.85))
    min_hits = int(config.get("min_hits", 2))

    frames = sorted(FRAMES_DIR.glob("frame_*.jpg"))

    if not frames:
        raise RuntimeError("Keine Frames gefunden. Erst capture_frames.py ausführen.")

    hits: Dict[str, List[float]] = {}
    checked = 0
    no_face = 0

    for frame in frames:
        checked += 1
        print(f"Prüfe {frame.name}...")

        try:
            match = recognize_frame(frame, config)
        except Exception as error:
            print(f"Fehler bei {frame.name}: {error}")
            continue

        if match is None:
            no_face += 1
            print("  Kein Gesicht / keine Erkennung")
            continue

        subject = match["subject"]
        similarity = match["similarity"]

        print(f"  Treffer: {subject} similarity={similarity:.3f}")

        if similarity >= min_similarity:
            hits.setdefault(subject, []).append(similarity)

    print("\nZusammenfassung:")
    print(f"Frames geprüft: {checked}")
    print(f"Frames ohne Gesicht/Erkennung: {no_face}")
    print(f"Hits: {hits}")

    best_subject = None
    best_count = 0
    best_avg = 0.0

    for subject, similarities in hits.items():
        count = len(similarities)
        avg = sum(similarities) / count

        if count > best_count or (count == best_count and avg > best_avg):
            best_subject = subject
            best_count = count
            best_avg = avg

    if best_subject and best_count >= min_hits:
        return {
            "status": "known",
            "subject": best_subject,
            "hits": best_count,
            "avg_similarity": best_avg,
            "all_hits": hits
        }

    return {
        "status": "unknown",
        "subject": None,
        "hits": best_count,
        "avg_similarity": best_avg,
        "all_hits": hits
    }


if __name__ == "__main__":
    result = recognize_all_frames()
    print("\nERGEBNIS:")
    print(json.dumps(result, indent=2, ensure_ascii=False))