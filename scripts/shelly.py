import os
import time

import requests
from dotenv import load_dotenv
from requests.auth import HTTPDigestAuth


load_dotenv()


def get_auth(config: dict):
    if not config.get("shelly_auth_enabled", False):
        return None

    username = os.getenv("SHELLY_USERNAME")
    password = os.getenv("SHELLY_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "Shelly Auth ist aktiviert, aber SHELLY_USERNAME oder SHELLY_PASSWORD fehlt in .env."
        )

    return HTTPDigestAuth(username, password)


def set_shelly(config: dict, on: bool, timeout: float = 5.0) -> dict:
    shelly_ip = config["shelly_ip"]
    url = f"http://{shelly_ip}/rpc/Switch.Set"

    response = requests.get(
        url,
        params={"id": 0, "on": "true" if on else "false"},
        timeout=timeout,
        auth=get_auth(config),
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError:
        return {"raw_response": response.text}


def get_shelly_status(config: dict, timeout: float = 5.0) -> dict:
    shelly_ip = config["shelly_ip"]
    url = f"http://{shelly_ip}/rpc/Switch.GetStatus"

    response = requests.get(
        url,
        params={"id": 0},
        timeout=timeout,
        auth=get_auth(config),
    )

    response.raise_for_status()
    return response.json()


def wait_for_shelly_output(
    config: dict,
    expected_output: bool,
    timeout_seconds: float = 2.0,
    poll_interval: float = 0.2,
) -> bool:
    deadline = time.perf_counter() + timeout_seconds

    while time.perf_counter() < deadline:
        status = get_shelly_status(config)
        current_output = bool(status.get("output", False))

        if current_output == expected_output:
            return True

        time.sleep(poll_interval)

    return False


def trigger_shelly_pulse(config: dict, timeout: float = 5.0) -> None:
    pulse_seconds = float(config.get("shelly_pulse_seconds", 3.0))

    print("Sende Shelly ON...")
    set_shelly(config, True, timeout=timeout)

    if not wait_for_shelly_output(config, True, timeout_seconds=2.0):
        raise RuntimeError("Shelly wurde nach ON-Befehl nicht als ON bestätigt.")

    print(f"Shelly ON bestätigt. Warte {pulse_seconds:.1f}s...")

    try:
        time.sleep(pulse_seconds)
    finally:
        print("Sende Shelly OFF...")
        set_shelly(config, False, timeout=timeout)

    if not wait_for_shelly_output(config, False, timeout_seconds=2.0):
        print("Shelly war nach OFF-Befehl noch ON. Sende zweiten OFF-Befehl...")
        set_shelly(config, False, timeout=timeout)

        if not wait_for_shelly_output(config, False, timeout_seconds=2.0):
            raise RuntimeError("Shelly konnte nach OFF-Befehl nicht als OFF bestätigt werden.")

    print("Shelly OFF aktiv bestätigt.")