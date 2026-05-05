import json
from datetime import datetime
import paho.mqtt.client as mqtt


MQTT_HOST = "localhost"
MQTT_PORT = 1883


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"Verbunden mit MQTT. reason_code={reason_code}")
    client.subscribe("#")
    print("Lausche auf alle Topics. Drück jetzt einmal die Ring-Klingel.")


def on_message(client, userdata, msg):
    topic = msg.topic

    try:
        payload = msg.payload.decode("utf-8", errors="replace")
    except Exception:
        payload = str(msg.payload)

    # Filter, damit nicht alles zugemüllt wird
    interesting_words = [
        "ring",
        "door",
        "ding",
        "motion",
        "button",
        "event",
        "state",
        "binary_sensor",
        "camera"
    ]

    combined = f"{topic} {payload}".lower()

    if any(word in combined for word in interesting_words):
        now = datetime.now().strftime("%H:%M:%S")
        print("\n" + "=" * 80)
        print(f"{now}")
        print(f"TOPIC: {topic}")
        print(f"PAYLOAD: {payload[:1000]}")


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()