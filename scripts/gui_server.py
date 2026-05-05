import csv
import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
import threading

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Body
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import subprocess
import time

from scripts.run_veribell_parallel import run_parallel_gate
from action_runner import run_actions


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.json"
PEOPLE_PATH = BASE_DIR / "config" / "people.json"
LOGS_PATH = BASE_DIR / "logs" / "visits.csv"
WEB_DIR = BASE_DIR / "web"

ADMIN_USER = os.getenv("VERIBELL_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("VERIBELL_ADMIN_PASSWORD")

security = HTTPBasic()
app = FastAPI(title="VeriBell Admin")

RUN_LOCK = threading.Lock()
LAST_RUN_STATUS: dict[str, Any] = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_result": None,
    "last_error": None,
}

def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="VERIBELL_ADMIN_PASSWORD fehlt in .env."
        )

    if credentials.username != ADMIN_USER or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=401,
            detail="Ungültige Zugangsdaten",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json_with_backup(path: Path, data: dict) -> None:
    backup_dir = BASE_DIR / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = backup_dir / f"{path.stem}_{timestamp}.json"

    if path.exists():
        shutil.copy2(path, backup_path)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def get_config() -> dict:
    return load_json(CONFIG_PATH)


def get_people() -> dict:
    return load_json(PEOPLE_PATH)


def read_visits(limit: int = 50) -> list[dict]:
    if not LOGS_PATH.exists():
        return []

    with open(LOGS_PATH, "r", encoding="utf-8") as file:
        rows = list(csv.DictReader(file, delimiter=";"))

    return rows[-limit:][::-1]

def build_crop_test_filter(frame_width: int, crop: dict) -> str:
    filters = [f"scale={frame_width}:-1"]

    if crop.get("enabled", False):
        width_pct = float(crop.get("width_pct", 1.0))
        height_pct = float(crop.get("height_pct", 1.0))
        x_pct = float(crop.get("x_pct", 0.0))
        y_pct = float(crop.get("y_pct", 0.0))

        if x_pct < 0 or y_pct < 0:
            raise HTTPException(status_code=400, detail="Crop x/y darf nicht negativ sein.")

        if width_pct <= 0 or height_pct <= 0:
            raise HTTPException(status_code=400, detail="Crop Breite/Höhe muss größer 0 sein.")

        if x_pct + width_pct > 1:
            raise HTTPException(status_code=400, detail="Crop liegt rechts außerhalb des Bildes.")

        if y_pct + height_pct > 1:
            raise HTTPException(status_code=400, detail="Crop liegt unten außerhalb des Bildes.")

        filters.append(
            f"crop=iw*{width_pct}:ih*{height_pct}:iw*{x_pct}:ih*{y_pct}"
        )

    return ",".join(filters)

@app.post("/api/crop-recognition-test/{source_id}")
def api_crop_recognition_test(
    source_id: str,
    payload: dict = Body(...),
    _: str = Depends(require_auth)
):
    config = get_config()
    sources = config.get("sources", {})

    if source_id not in sources:
        raise HTTPException(status_code=404, detail="Source nicht gefunden.")

    source = sources[source_id]

    if source.get("type") != "rtsp":
        raise HTTPException(
            status_code=400,
            detail="Crop Recognition Test ist aktuell nur für RTSP-Sources umgesetzt."
        )

    rtsp_url = source.get("rtsp_url")
    if not rtsp_url:
        raise HTTPException(status_code=400, detail="Source hat keine rtsp_url.")

    ffmpeg_path = config.get("ffmpeg_path")
    if not ffmpeg_path:
        raise HTTPException(status_code=400, detail="ffmpeg_path fehlt in settings.json.")

    frame_width = int(payload.get("frame_width", source.get("frame_width", config.get("frame_width", 960))))
    crop = payload.get("crop", {})

    preview_dir = BASE_DIR / "data" / "crop_tests"
    preview_dir.mkdir(parents=True, exist_ok=True)

    test_path = preview_dir / f"{source_id}_crop_test_{int(time.time())}.jpg"

    vf_filter = build_crop_test_filter(frame_width, crop)

    command = [
        ffmpeg_path,
        "-y",
        "-rtsp_transport", "tcp",
        "-analyzeduration", "1000000",
        "-probesize", "1000000",
        "-i", rtsp_url,
        "-an",
        "-frames:v", "1",
        "-vf", vf_filter,
        str(test_path),
    ]

    try:
        subprocess.run(
            command,
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=35,
            check=True,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Crop-Test-Aufnahme hat zu lange gedauert.")
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="Crop-Test-Aufnahme mit ffmpeg fehlgeschlagen.")

    if not test_path.exists():
        raise HTTPException(status_code=500, detail="Crop-Test-Bild wurde nicht erzeugt.")

    try:
        from scripts.recognize_frames import recognize_single_frame

        result = recognize_single_frame(test_path, config)

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Recognition-Test fehlgeschlagen: {error}"
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Recognition-Test fehlgeschlagen: {error}"
        )

    return {
        "message": "Crop Recognition Test abgeschlossen.",
        "source_id": source_id,
        "frame_width": frame_width,
        "crop": crop,
        "test_image": str(test_path.relative_to(BASE_DIR)),
        "result": result,
    }

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/api/crop-preview/{source_id}")
def api_crop_preview(source_id: str, _: str = Depends(require_auth)):
    config = get_config()
    sources = config.get("sources", {})

    if source_id not in sources:
        raise HTTPException(status_code=404, detail="Source nicht gefunden.")

    source = sources[source_id]

    if source.get("type") != "rtsp":
        raise HTTPException(status_code=400, detail="Crop Preview ist aktuell nur für RTSP-Sources umgesetzt.")

    rtsp_url = source.get("rtsp_url")
    if not rtsp_url:
        raise HTTPException(status_code=400, detail="Source hat keine rtsp_url.")

    ffmpeg_path = config.get("ffmpeg_path")
    if not ffmpeg_path:
        raise HTTPException(status_code=400, detail="ffmpeg_path fehlt in settings.json.")

    preview_dir = BASE_DIR / "data" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    preview_path = preview_dir / f"{source_id}_preview.jpg"

    frame_width = int(source.get("frame_width", config.get("frame_width", 960)))

    command = [
        ffmpeg_path,
        "-y",
        "-rtsp_transport", "tcp",
        "-analyzeduration", "1000000",
        "-probesize", "1000000",
        "-i", rtsp_url,
        "-an",
        "-frames:v", "1",
        "-vf", f"scale={frame_width}:-1",
        str(preview_path),
    ]

    try:
        subprocess.run(
            command,
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=35,
            check=True,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Preview-Aufnahme hat zu lange gedauert.")
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="Preview-Aufnahme mit ffmpeg fehlgeschlagen.")

    if not preview_path.exists():
        raise HTTPException(status_code=500, detail="Preview-Bild wurde nicht erzeugt.")

    return FileResponse(preview_path)

@app.get("/api/status")
def api_status(_: str = Depends(require_auth)):
    config = get_config()

    return {
        "app": "VeriBell",
        "time": datetime.now().isoformat(timespec="seconds"),
        "sources_count": len(config.get("sources", {})),
        "triggers_count": len(config.get("triggers", {})),
        "actions_count": len(config.get("actions", {})),
        "rules_count": len(config.get("rules", [])),
        "last_run": LAST_RUN_STATUS,
    }


@app.get("/api/config")
def api_get_config(_: str = Depends(require_auth)):
    return get_config()


@app.post("/api/config")
def api_save_config(payload: dict, _: str = Depends(require_auth)):
    # Minimalvalidierung. Später bauen wir dafür Schemas.
    if "sources" not in payload:
        raise HTTPException(status_code=400, detail="settings.json braucht 'sources'.")
    if "triggers" not in payload:
        raise HTTPException(status_code=400, detail="settings.json braucht 'triggers'.")
    if "actions" not in payload:
        raise HTTPException(status_code=400, detail="settings.json braucht 'actions'.")
    if "rules" not in payload:
        raise HTTPException(status_code=400, detail="settings.json braucht 'rules'.")

    save_json_with_backup(CONFIG_PATH, payload)
    return {"ok": True, "message": "settings.json gespeichert. Backup wurde erstellt."}


@app.get("/api/people")
def api_get_people(_: str = Depends(require_auth)):
    return get_people()


@app.post("/api/people")
def api_save_people(payload: dict, _: str = Depends(require_auth)):
    save_json_with_backup(PEOPLE_PATH, payload)
    return {"ok": True, "message": "people.json gespeichert. Backup wurde erstellt."}


@app.get("/api/visits")
def api_visits(limit: int = 50, _: str = Depends(require_auth)):
    return {
        "items": read_visits(limit=limit)
    }


def manual_run_worker(source_id: str, trigger_id: str | None = None):
    with RUN_LOCK:
        LAST_RUN_STATUS["running"] = True
        LAST_RUN_STATUS["last_started"] = datetime.now().isoformat(timespec="seconds")
        LAST_RUN_STATUS["last_finished"] = None
        LAST_RUN_STATUS["last_result"] = None
        LAST_RUN_STATUS["last_error"] = None

        try:
            config = get_config()
            sources = config.get("sources", {})
            triggers = config.get("triggers", {})

            if source_id not in sources:
                raise RuntimeError(f"Unbekannte Source: {source_id}")

            source_config = sources[source_id]
            result = run_parallel_gate(source_id, source_config)

            trigger_config = triggers.get(trigger_id or "", {})

            event = {
                "trigger_id": trigger_id or "manual_gui_trigger",
                "trigger_name": trigger_config.get("display_name", "Manueller GUI-Test"),
                "trigger_type": trigger_config.get("type", "manual"),
                "source_id": source_id,
                "source_name": source_config.get("display_name", source_id),
                "source_type": source_config.get("type"),
                "event_type": trigger_config.get("event_type", "manual_test"),
            }

            run_actions(event, result, config)

            LAST_RUN_STATUS["last_result"] = {
                "event": event,
                "result": result,
            }

        except Exception as error:
            LAST_RUN_STATUS["last_error"] = str(error)

        finally:
            LAST_RUN_STATUS["running"] = False
            LAST_RUN_STATUS["last_finished"] = datetime.now().isoformat(timespec="seconds")


@app.post("/api/manual-run/{source_id}")
def api_manual_run(source_id: str, trigger_id: str | None = None, _: str = Depends(require_auth)):
    if RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="Ein Lauf ist bereits aktiv.")

    thread = threading.Thread(
        target=manual_run_worker,
        args=(source_id, trigger_id),
        daemon=True
    )
    thread.start()

    return {
        "ok": True,
        "message": f"Manueller Lauf für Source {source_id} gestartet."
    }


@app.get("/api/files/frame")
def api_latest_frame(source_id: str, _: str = Depends(require_auth)):
    config = get_config()
    source = config.get("sources", {}).get(source_id)

    if not source:
        raise HTTPException(status_code=404, detail="Source nicht gefunden.")

    frames_dir = source.get("frames_dir")
    if not frames_dir:
        frames_dir = str(BASE_DIR / config.get("data_dir", "data") / "frames" / source_id)

    path = BASE_DIR / frames_dir
    frames = sorted(path.glob("frame_*.jpg"))

    if not frames:
        raise HTTPException(status_code=404, detail="Kein Frame gefunden.")

    return FileResponse(frames[-1])


app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        reload=False
    )