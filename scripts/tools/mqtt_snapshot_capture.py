from pathlib import Path
from datetime import datetime
import paho.mqtt.client as mqtt


BASE_DIR = Path(__file__).resolve().parent.parent
FRAMES_DIR = BASE_DIR / "frames"

MQTT_HOST = "localhost"
MQTT_PORT = 1883

SNAPSHOT_TOPIC = "ring/cdc879b8-844c-4ed5-a313-7a6e2e363b1b/camera/90486c0fface/snapshot/image"


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"Verbunden mit MQTT. reason_code={reason_code}")
    print(f"Warte auf Snapshot: {SNAPSHOT_TOPIC}")
    client.subscribe(SNAPSHOT_TOPIC)


def on_message(client, userdata, msg):
    FRAMES_DIR.mkdir(exist_ok=True)

    output_path = FRAMES_DIR / "frame_001.jpg"

    with open(output_path, "wb") as file:
        file.write(msg.payload)

    print(f"{datetime.now().strftime('%H:%M:%S')} Snapshot gespeichert: {output_path}")
    print(f"Bytes: {len(msg.payload)}")

    client.disconnect()


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()