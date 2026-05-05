import csv
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
VISITS_CSV = LOGS_DIR / "visits.csv"


def log_visit(event: dict, result: dict, person: dict, action: str) -> None:
    LOGS_DIR.mkdir(exist_ok=True)

    file_exists = VISITS_CSV.exists()

    source_id = event.get("source_id", "")
    source_name = event.get("source_name", "")
    event_type = event.get("event_type", "")

    status = result.get("status", "")
    subject = result.get("subject") or ""
    hits = int(result.get("hits", 0))
    avg_similarity = float(result.get("avg_similarity", 0.0))
    frames_checked = int(result.get("frames_checked", 0))
    frames_dir = result.get("frames_dir", "")
    trigger_id = event.get("trigger_id", "")
    trigger_name = event.get("trigger_name", "")
    trigger_type = event.get("trigger_type", "")

    role = person.get("role", "unknown")
    open_gate = bool(person.get("open_gate", False))

    file_exists = VISITS_CSV.exists()

    with open(VISITS_CSV, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")

        if not file_exists:
            writer.writerow([
                "timestamp",
                "trigger_id",
                "trigger_name",
                "trigger_type",
                "source_id",
                "source_name",
                "event_type",
                "status",
                "subject",
                "role",
                "open_gate",
                "hits",
                "avg_similarity",
                "frames_checked",
                "frames_dir",
                "action"
            ])

        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            trigger_id,
            trigger_name,
            trigger_type,
            source_id,
            source_name,
            event_type,
            status,
            subject,
            role,
            str(open_gate).lower(),
            hits,
            f"{avg_similarity:.3f}",
            frames_checked,
            frames_dir,
            action
        ])