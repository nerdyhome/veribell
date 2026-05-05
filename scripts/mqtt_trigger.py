import os
import threading
import time
from datetime import datetime

from dotenv import load_dotenv
import paho.mqtt.client as mqtt

from action_runner import run_actions
from scripts.run_veribell_parallel import run_parallel_gate, load_config
from scripts.recognize_frames import warmup_recognizer


load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

RECOGNIZER_WARMED_UP = threading.Event()
RECOGNIZER_WARMUP_ERROR = None

def warmup_wrapper():
    global RECOGNIZER_WARMUP_ERROR

    try:
        warmup_recognizer()
    except Exception as error:
        RECOGNIZER_WARMUP_ERROR = error
        print(f"Fehler beim Recognizer-Warmup: {error}")
    finally:
        RECOGNIZER_WARMED_UP.set()

def build_trigger_maps(config: dict) -> tuple[dict, dict, dict]:
    sources = config.get("sources", {})
    triggers = config.get("triggers", {})

    topic_to_trigger_id = {}

    for trigger_id, trigger_config in triggers.items():
        if not trigger_config.get("enabled", True):
            continue

        if trigger_config.get("type") != "mqtt":
            continue

        source_id = trigger_config.get("source")
        if not source_id:
            raise RuntimeError(f"MQTT-Trigger {trigger_id} hat keine source.")

        if source_id not in sources:
            raise RuntimeError(
                f"MQTT-Trigger {trigger_id} verweist auf unbekannte Source: {source_id}"
            )

        mqtt_topic = trigger_config.get("mqtt_topic")
        if not mqtt_topic:
            raise RuntimeError(f"MQTT-Trigger {trigger_id} hat kein mqtt_topic.")

        topic_to_trigger_id[mqtt_topic] = trigger_id

    if not topic_to_trigger_id:
        raise RuntimeError("Keine aktivierten MQTT-Trigger in settings.json gefunden.")

    return sources, triggers, topic_to_trigger_id


CONFIG = load_config()
SOURCES, TRIGGERS, TOPIC_TO_TRIGGER_ID = build_trigger_maps(CONFIG)

SOURCE_LOCKS = {
    source_id: threading.Lock()
    for source_id in SOURCES.keys()
}

LAST_TRIGGER_BY_TRIGGER: dict[str, float] = {}

MAX_PARALLEL_PIPELINES = int(CONFIG.get("max_parallel_pipelines", 1))
PIPELINE_WAIT_TIMEOUT_SECONDS = float(CONFIG.get("pipeline_wait_timeout_seconds", 0))

PIPELINE_SEMAPHORE = threading.Semaphore(MAX_PARALLEL_PIPELINES)


def get_matching_trigger_id(topic: str, payload: str) -> str | None:
    trigger_id = TOPIC_TO_TRIGGER_ID.get(topic)

    if not trigger_id:
        return None

    trigger_config = TRIGGERS[trigger_id]
    expected_payload = str(trigger_config.get("trigger_payload", "ON")).strip().upper()

    if payload.strip().upper() != expected_payload:
        return None

    return trigger_id


def try_mark_trigger(trigger_id: str) -> bool:
    now = time.time()
    trigger_config = TRIGGERS[trigger_id]

    cooldown = float(
        trigger_config.get(
            "cooldown_seconds",
            CONFIG.get("source_cooldown_seconds", 45)
        )
    )

    last_trigger = LAST_TRIGGER_BY_TRIGGER.get(trigger_id, 0)

    if now - last_trigger < cooldown:
        remaining = cooldown - (now - last_trigger)
        print(f"Ignoriere Trigger {trigger_id}: Cooldown noch {remaining:.1f}s")
        return False

    LAST_TRIGGER_BY_TRIGGER[trigger_id] = now
    return True


def acquire_pipeline_slot(source_id: str) -> bool:
    if PIPELINE_WAIT_TIMEOUT_SECONDS <= 0:
        acquired = PIPELINE_SEMAPHORE.acquire(blocking=False)
    else:
        acquired = PIPELINE_SEMAPHORE.acquire(
            blocking=True,
            timeout=PIPELINE_WAIT_TIMEOUT_SECONDS
        )

    if not acquired:
        print(
            f"Pipeline ist ausgelastet. Trigger für {source_id} wird ignoriert. "
            f"max_parallel_pipelines={MAX_PARALLEL_PIPELINES}"
        )
        return False

    return True


def run_face_gate_direct(trigger_id: str) -> None:
    if not RECOGNIZER_WARMED_UP.is_set():
        print("Recognizer ist noch im Warmup. Trigger wird ignoriert.")
        return

    if RECOGNIZER_WARMUP_ERROR is not None:
        print(f"Recognizer-Warmup ist fehlgeschlagen. Trigger wird ignoriert: {RECOGNIZER_WARMUP_ERROR}")
        return

    trigger_config = TRIGGERS[trigger_id]
    source_id = trigger_config["source"]

    source_config = SOURCES[source_id]
    source_name = source_config.get("display_name", source_id)
    trigger_name = trigger_config.get("display_name", trigger_id)

    source_lock = SOURCE_LOCKS[source_id]

    if not source_lock.acquire(blocking=False):
        print(f"Source {source_id} läuft bereits. Trigger {trigger_id} wird ignoriert.")
        return

    pipeline_acquired = False

    try:
        if not acquire_pipeline_slot(source_id):
            return

        pipeline_acquired = True

        if not try_mark_trigger(trigger_id):
            return

        print("\n" + "=" * 80)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Trigger erkannt: {trigger_name}")
        print(f"Trigger ID: {trigger_id}")
        print(f"Source ID: {source_id}")
        print(f"Event Type: {trigger_config.get('event_type')}")
        print("Starte run_parallel_gate()...")

        config = load_config()
        sources = config.get("sources", {})
        triggers = config.get("triggers", {})

        if trigger_id not in triggers:
            raise RuntimeError(f"Trigger {trigger_id} nicht mehr in settings.json gefunden.")

        current_trigger_config = triggers[trigger_id]
        current_source_id = current_trigger_config["source"]

        if current_source_id not in sources:
            raise RuntimeError(
                f"Trigger {trigger_id} verweist auf unbekannte Source: {current_source_id}"
            )

        current_source_config = sources[current_source_id]

        result = run_parallel_gate(current_source_id, current_source_config)

        event = {
            "trigger_id": trigger_id,
            "trigger_name": current_trigger_config.get("display_name", trigger_id),
            "trigger_type": current_trigger_config.get("type"),
            "source_id": current_source_id,
            "source_name": current_source_config.get("display_name", current_source_id),
            "source_type": current_source_config.get("type"),
            "event_type": current_trigger_config.get("event_type"),
        }

        run_actions(event, result, config)

    except Exception as error:
        print(f"Fehler im Face-Gate-Lauf für Trigger {trigger_id}: {error}")

    finally:
        if pipeline_acquired:
            PIPELINE_SEMAPHORE.release()

        source_lock.release()


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"Verbunden mit MQTT. reason_code={reason_code}")

    if getattr(reason_code, "value", 0) != 0:
        print("MQTT-Verbindung nicht erfolgreich. Prüfe Username/Passwort.")
        return

    for topic, trigger_id in TOPIC_TO_TRIGGER_ID.items():
        trigger_config = TRIGGERS[trigger_id]
        source_id = trigger_config["source"]
        source_name = SOURCES[source_id].get("display_name", source_id)
        trigger_name = trigger_config.get("display_name", trigger_id)

        print(f"Subscribe auf: {topic} ({trigger_name} → {source_name})")
        client.subscribe(topic)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    print(f"MQTT getrennt. reason_code={reason_code}")


def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="replace")

    trigger_id = get_matching_trigger_id(topic, payload)

    if not trigger_id:
        return

    trigger_config = TRIGGERS[trigger_id]
    source_id = trigger_config["source"]

    source_name = SOURCES[source_id].get("display_name", source_id)
    trigger_name = trigger_config.get("display_name", trigger_id)

    print(f"\nTrigger-Topic: {topic}")
    print(f"Payload: {payload}")
    print(f"Trigger: {trigger_id} ({trigger_name})")
    print(f"Source: {source_id} ({source_name})")

    thread = threading.Thread(
        target=run_face_gate_direct,
        args=(trigger_id,),
        daemon=True
    )
    thread.start()


def start_mqtt_listener(run_warmup: bool = True) -> None:
    print("MQTT Ring Parallel Trigger gestartet.")
    print(f"MQTT Host: {MQTT_HOST}:{MQTT_PORT}")
    print(f"MQTT User gesetzt: {'ja' if MQTT_USERNAME else 'nein'}")
    print(f"Max parallele Pipelines: {MAX_PARALLEL_PIPELINES}")

    print("Aktive MQTT-Trigger:")
    for topic, trigger_id in TOPIC_TO_TRIGGER_ID.items():
        trigger_config = TRIGGERS[trigger_id]
        source_id = trigger_config["source"]

        trigger_name = trigger_config.get("display_name", trigger_id)
        source_name = SOURCES[source_id].get("display_name", source_id)

        print(f"- {trigger_id}: {trigger_name}")
        print(f"  Source: {source_id} ({source_name})")
        print(f"  Topic: {topic}")

    if run_warmup:
        print("DeepFace wird jetzt vorgeladen. Das kann beim ersten Start dauern...")

        warmup_thread = threading.Thread(target=warmup_wrapper, daemon=True)
        warmup_thread.start()
    else:
        # Wird vom zentralen VeriBell-Service gesetzt,
        # nachdem der gemeinsame Recognizer bereits vorgeladen wurde.
        RECOGNIZER_WARMED_UP.set()

    print("MQTT Listener läuft. Beenden über VeriBell Service.")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


def main():
    start_mqtt_listener(run_warmup=True)


if __name__ == "__main__":
    main()