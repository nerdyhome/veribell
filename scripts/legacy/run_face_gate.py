import json
import sys
from pathlib import Path

from capture_frames import capture_frames
from save_unknown import save_unknown
from log_visit import log_visit


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.json"


DISPLAY_NAMES = {
    "markus": "Markus",
    "karen": "Karen",
    "christiane": "Christiane"
}


ROLES = {
    "markus": {
        "role": "owner",
        "open_gate": True
    },
    "karen": {
        "role": "owner",
        "open_gate": True
    },
    "christiane": {
        "role": "friend",
        "open_gate": False
    }
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def build_announcement(subject: str | None, status: str) -> str:
    if status == "known" and subject:
        name = DISPLAY_NAMES.get(subject, subject)
        return f"{name} ist da."

    return "Unbekannte Person an der Tür."


def main() -> int:
    import time

    config = load_config()

    print("=== Ring Face Gate PoC ===")

    total_start = time.perf_counter()

    try:
        capture_start = time.perf_counter()
        capture_frames()
        capture_end = time.perf_counter()
        print(f"Zeit Capture: {capture_end - capture_start:.2f} Sekunden")
    except Exception as error:
        print(f"Fehler beim Speichern der Frames: {error}")
        return 1

    try:
        recognition_start = time.perf_counter()

        # DeepFace / TensorFlow erst JETZT laden,
        # nachdem die Frames bereits gespeichert wurden.
        from recognize_frames import recognize_all_frames

        result = recognize_all_frames()

        recognition_end = time.perf_counter()
        print(f"Zeit Recognition: {recognition_end - recognition_start:.2f} Sekunden")
    except Exception as error:
        print(f"Fehler bei der Gesichtserkennung: {error}")
        return 1

    status = result["status"]
    subject = result.get("subject")
    hits = int(result.get("hits", 0))
    avg_similarity = float(result.get("avg_similarity", 0.0))

    announcement = build_announcement(subject, status)

    print("\n=== Entscheidung ===")
    print(f"Status: {status}")
    print(f"Person: {subject}")
    print(f"Hits: {hits}")
    print(f"Avg Similarity: {avg_similarity:.3f}")
    print(f"Alexa-Text wäre: {announcement}")

    action = "announce_only"

    if status == "unknown":
        save_unknown(result)
        action = "saved_unknown"

    if status == "known" and subject:
        role_config = ROLES.get(subject, {"role": "unknown", "open_gate": False})
        can_open_gate = bool(role_config.get("open_gate", False))

        if can_open_gate:
            if config.get("enable_shelly", False):
                print("Shelly wäre aktiv. Später hier Torimpuls.")
                action = "would_open_gate"
            else:
                print("Shelly ist deaktiviert. Kein Torimpuls.")
                action = "recognized_no_shelly"
        else:
            print("Person bekannt, aber nicht für Toröffnung freigegeben.")
            action = "recognized_friend_announce_only"

    log_visit(
        status=status,
        subject=subject,
        hits=hits,
        avg_similarity=avg_similarity,
        action=action
    )

    total_end = time.perf_counter()
    print(f"Gesamtzeit: {total_end - total_start:.2f} Sekunden")

    print("\nFertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())