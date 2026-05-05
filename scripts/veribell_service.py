import signal
import sys
import threading
import time
from pathlib import Path

import uvicorn


BASE_DIR = Path(__file__).resolve().parent.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


STOP_EVENT = threading.Event()


def run_gui_server() -> None:
    from scripts.gui_server import app

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )

    server = uvicorn.Server(config)

    # Für späteres sauberes Beenden erreichbar machen.
    run_gui_server.server = server

    server.run()


def run_mqtt_listener() -> None:
    from scripts.mqtt_trigger import start_mqtt_listener

    # Kein eigener Warmup hier.
    # Der zentrale VeriBell-Service hat den Recognizer bereits vorgeladen.
    start_mqtt_listener(run_warmup=False)


def warmup_recognizer_once() -> bool:
    try:
        from scripts.recognize_frames import warmup_recognizer

        print("VeriBell Recognizer Warmup startet...")
        warmup_recognizer()
        print("VeriBell Recognizer Warmup fertig.")
        return True

    except Exception as error:
        print(f"Fehler beim VeriBell Recognizer Warmup: {error}")
        return False


def stop_service() -> None:
    STOP_EVENT.set()

    server = getattr(run_gui_server, "server", None)
    if server is not None:
        server.should_exit = True


def handle_shutdown(signum, frame) -> None:
    print("Stoppe VeriBell...")
    stop_service()


def main() -> int:
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    print("VeriBell Service gestartet.")
    print(f"Projektpfad: {BASE_DIR}")
    print("Beenden mit STRG+C.")

    # 1. GUI sofort starten, damit die Oberfläche erreichbar ist.
    gui_thread = threading.Thread(
        target=run_gui_server,
        name="VeriBell-GUI",
        daemon=True,
    )
    gui_thread.start()

    # 2. Recognizer genau einmal zentral warmen.
    recognizer_ok = warmup_recognizer_once()

    if not recognizer_ok:
        print("Recognizer konnte nicht vorgeladen werden. MQTT Listener wird nicht gestartet.")
        print("GUI bleibt erreichbar, damit Konfiguration/Logs geprüft werden können.")

        while not STOP_EVENT.is_set():
            time.sleep(1)

        return 1

    # 3. MQTT erst nach erfolgreichem Warmup starten.
    mqtt_thread = threading.Thread(
        target=run_mqtt_listener,
        name="VeriBell-MQTT",
        daemon=True,
    )
    mqtt_thread.start()

    print("VeriBell läuft.")
    print("GUI:  http://localhost:8080")
    print("MQTT: aktiv")

    try:
        while not STOP_EVENT.is_set():
            if not gui_thread.is_alive():
                print("GUI-Thread wurde beendet.")
                stop_service()
                return 1

            if not mqtt_thread.is_alive():
                print("MQTT-Thread wurde beendet.")
                stop_service()
                return 1

            time.sleep(1)

    finally:
        stop_service()

    return 0


if __name__ == "__main__":
    sys.exit(main())