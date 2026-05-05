from shelly import trigger_shelly_pulse


def run(action_config: dict, context: dict) -> str:
    shelly_ip = action_config["shelly_ip"]
    pulse_seconds = float(action_config.get("shelly_pulse_seconds", 3.0))

    print(f"Schalte Shelly unter {shelly_ip} für {pulse_seconds} Sekunden...")

    trigger_shelly_pulse(action_config)

    return "shelly_pulse_sent"