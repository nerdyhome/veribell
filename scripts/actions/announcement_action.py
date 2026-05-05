import os
from copy import deepcopy
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

def get_announcement_language(action_config: dict, context: dict) -> str:
    config = context.get("config", {})
    return action_config.get(
        "language",
        config.get("announcement_language", "de-DE")
    )


def get_template_for_status(action_config: dict, status: str, language: str) -> str:
    templates = action_config.get("message_templates", {})

    language_templates = templates.get(language)

    if not language_templates:
        # Fallback auf Deutsch
        language_templates = templates.get("de-DE", {})

    if language_templates and status in language_templates:
        return language_templates[status]
    
    # Rückwärtskompatibilität zu alten Feldern
    if status == "known":
        return action_config.get("message_template_known", "{display_name} ist da.")
    if status == "unknown":
        return action_config.get("message_template_unknown", "Unbekannte Person an der Tür.")
    if status == "no_face":
        return action_config.get("message_template_no_face", "Es hat geklingelt.")
    return action_config.get("message_template_error", "Fehler bei der Türerkennung.")


def build_values(context: dict, message: str | None = None) -> dict:
    result = context.get("result", {})
    event = context.get("event", {})
    person = context.get("person", {})

    values = {
        "message": message or "",
        "status": result.get("status", ""),
        "subject": result.get("subject") or "",
        "display_name": person.get("display_name") or result.get("subject") or "",
        "role": person.get("role", ""),
        "source_id": event.get("source_id", ""),
        "source_name": event.get("source_name", ""),
        "event_type": event.get("event_type", ""),
        "hits": result.get("hits", 0),
        "avg_similarity": f"{float(result.get('avg_similarity', 0.0)):.3f}",
    }

    return values


def render_template(template: str, context: dict, message: str | None = None) -> str:
    values = build_values(context, message=message)

    try:
        return template.format(**values)
    except KeyError as error:
        raise RuntimeError(f"Unbekannter Platzhalter im Template: {error}") from error


def render_nested(value: Any, context: dict, message: str) -> Any:
    if isinstance(value, str):
        return render_template(value, context, message=message)

    if isinstance(value, list):
        return [render_nested(item, context, message) for item in value]

    if isinstance(value, dict):
        return {
            key: render_nested(item, context, message)
            for key, item in value.items()
        }

    return value

def get_person_announcement(context: dict) -> str | None:
    result = context.get("result", {})
    event = context.get("event", {})
    person = context.get("person", {})

    if result.get("status") != "known":
        return None

    announcements = person.get("announcements", {})

    if not isinstance(announcements, dict):
        return None

    event_type = event.get("event_type")

    if event_type and announcements.get(event_type):
        return announcements[event_type]

    if announcements.get("default"):
        return announcements["default"]

    return None

def build_message(action_config: dict, context: dict) -> str:
    person_template = get_person_announcement(context)

    if person_template:
        return render_template(person_template, context)

    status = context.get("result", {}).get("status", "error")
    template = get_template_for_status(action_config, status)
    return render_template(template, context)


def run_voice_monkey(action_config: dict, context: dict, message: str) -> str:
    token = os.getenv("VOICE_MONKEY_TOKEN")

    if not token:
        raise RuntimeError("VOICE_MONKEY_TOKEN oder VOICE_MONKEY_SECRET fehlt in .env.")

    monkeys = action_config.get("monkeys")

    if not monkeys:
        single_monkey = action_config.get("monkey")
        if single_monkey:
            monkeys = [single_monkey]

    if not monkeys:
        raise RuntimeError("Voice-Monkey-Action braucht 'monkey' oder 'monkeys'.")

    url = "https://api-v3.voicemonkey.io/announce"
    language = get_announcement_language(action_config, context)

    sent = []

    for monkey in monkeys:
        payload = {
            "token": token,
            "device": monkey,
            "speech": message,
            "language": language,
        }

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()

        sent.append(monkey)

    print(f"Voice Monkey Ansage gesendet an {sent}: {message}")
    return "voice_monkey_sent"


def run_home_assistant_webhook(action_config: dict, context: dict, message: str) -> str:
    webhook_url = action_config.get("webhook_url")

    if not webhook_url:
        raise RuntimeError("Home-Assistant-Webhook-Action hat keine webhook_url.")

    payload = {
        "message": message,
        "status": context.get("result", {}).get("status"),
        "subject": context.get("result", {}).get("subject"),
        "source_id": context.get("event", {}).get("source_id"),
        "source_name": context.get("event", {}).get("source_name"),
        "event_type": context.get("event", {}).get("event_type"),
        "role": context.get("person", {}).get("role"),
        "open_gate": bool(context.get("person", {}).get("open_gate", False)),
    }

    response = requests.post(webhook_url, json=payload, timeout=10)
    response.raise_for_status()

    print(f"Home Assistant Webhook gesendet: {message}")
    return "home_assistant_webhook_sent"


def run_home_assistant_service(action_config: dict, context: dict, message: str) -> str:
    token = os.getenv("HOME_ASSISTANT_TOKEN")

    if not token:
        raise RuntimeError("HOME_ASSISTANT_TOKEN fehlt in .env.")

    base_url = action_config.get("base_url", "").rstrip("/")
    domain = action_config.get("domain")
    service = action_config.get("service")

    if not base_url or not domain or not service:
        raise RuntimeError("Home-Assistant-Service-Action braucht base_url, domain und service.")

    url = f"{base_url}/api/services/{domain}/{service}"

    service_data = deepcopy(action_config.get("service_data", {}))
    service_data = render_nested(service_data, context, message)

    # Für notify.alexa_media ist message typischerweise direkt im Service-Data-Root.
    service_data["message"] = message

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=service_data, timeout=10)
    response.raise_for_status()

    print(f"Home Assistant Service gesendet: {domain}.{service}: {message}")
    return "home_assistant_service_sent"


def run_http_webhook(action_config: dict, context: dict, message: str) -> str:
    url = action_config.get("url")

    if not url:
        raise RuntimeError("HTTP-Webhook-Action hat keine url.")

    method = action_config.get("method", "POST").upper()
    headers = render_nested(action_config.get("headers", {}), context, message)
    payload_template = action_config.get("payload", {"message": "{message}"})
    payload = render_nested(payload_template, context, message)

    response = requests.request(
        method=method,
        url=url,
        headers=headers,
        json=payload,
        timeout=10,
    )
    response.raise_for_status()

    print(f"HTTP Webhook gesendet: {message}")
    return "http_webhook_sent"


def run(action_config: dict, context: dict) -> str:
    backend = action_config.get("backend")
    message = build_message(action_config, context)

    if backend == "voice_monkey":
        return run_voice_monkey(action_config, context, message)

    if backend == "home_assistant_webhook":
        return run_home_assistant_webhook(action_config, context, message)

    if backend == "home_assistant_service":
        return run_home_assistant_service(action_config, context, message)

    if backend == "http_webhook":
        return run_http_webhook(action_config, context, message)

    raise RuntimeError(f"Unbekanntes Announcement-Backend: {backend}")