import requests


def render_message(template: str, context: dict) -> str:
    result = context.get("result", {})
    event = context.get("event", {})
    person = context.get("person", {})

    values = {
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

    try:
        return template.format(**values)
    except KeyError as error:
        raise RuntimeError(f"Unbekannter Platzhalter im Message-Template: {error}") from error


def build_message(action_config: dict, context: dict) -> str:
    status = context.get("result", {}).get("status")

    if status == "known":
        template = action_config.get("message_template_known", "{display_name} ist da.")
    elif status == "unknown":
        template = action_config.get("message_template_unknown", "Unbekannte Person an der Tür.")
    elif status == "no_face":
        template = action_config.get("message_template_no_face", "Keine Person im Bild erkannt.")
    else:
        template = action_config.get("message_template_error", "Fehler bei der Türerkennung.")

    return render_message(template, context)


def run(action_config: dict, context: dict) -> str:
    webhook_url = action_config.get("webhook_url")

    if not webhook_url:
        raise RuntimeError("home_assistant_webhook Action hat keine webhook_url.")

    message = build_message(action_config, context)

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

    response = requests.post(webhook_url, json=payload, timeout=5)
    response.raise_for_status()

    print(f"Home Assistant Webhook gesendet: {message}")
    return "home_assistant_webhook_sent"