from pathlib import Path
import json
from actions import announcement_action

from actions import shelly_action
from save_unknown import save_unknown
from log_visit import log_visit


BASE_DIR = Path(__file__).resolve().parent.parent
PEOPLE_PATH = BASE_DIR / "config" / "people.json"


def load_people() -> dict:
    with open(PEOPLE_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def get_person_config(subject: str | None, people: dict) -> dict:
    if not subject:
        return {
            "display_name": None,
            "role": "unknown",
            "open_gate": False
        }

    return people.get(
        subject.lower(),
        {
            "display_name": subject,
            "role": "unknown",
            "open_gate": False
        }
    )


def build_announcement(result: dict, people: dict) -> str:
    status = result.get("status")
    subject = result.get("subject")

    if status == "known" and subject:
        person = get_person_config(subject, people)
        name = person.get("display_name") or subject
        return f"{name} ist da."

    if status == "no_face":
        return "Keine Person im Bild erkannt."

    if status == "unknown":
        return "Unbekannte Person an der Tür."

    return "Fehler bei der Türerkennung."


def rule_matches(rule: dict, context: dict) -> bool:
    if not rule.get("enabled", True):
        return False

    event = context["event"]
    result = context["result"]
    person = context["person"]

    if rule.get("trigger") and rule["trigger"] != event.get("trigger_id"):
        return False

    if rule.get("event_type") and rule["event_type"] != event.get("event_type"):
        return False

    conditions = rule.get("when", {})

    for key, expected_value in conditions.items():
        if key == "status":
            actual_value = result.get("status")
        elif key == "subject":
            actual_value = result.get("subject")
        elif key == "role":
            actual_value = person.get("role")
        elif key == "open_gate":
            actual_value = bool(person.get("open_gate", False))
        else:
            print(f"Unbekannte Rule-Bedingung: {key}")
            return False

        if actual_value != expected_value:
            return False

    return True


def run_save_unknown(action_config: dict, context: dict) -> str:
    result = context["result"]

    if result.get("status") != "unknown":
        return "save_unknown_skipped"

    save_unknown(result)
    return "unknown_saved"


def run_log_visit(action_config: dict, context: dict) -> str:
    event = context["event"]
    result = context["result"]
    person = context["person"]
    action_summary = context.get("action_summary", "log_only")

    log_visit(
        event=event,
        result=result,
        person=person,
        action=action_summary
    )

    return "visit_logged"


def run_console_announcement(action_config: dict, context: dict) -> str:
    announcement = context.get("announcement", "Kein Ansagetext vorhanden.")
    print(f"Ansage wäre: {announcement}")
    return "announcement_printed"


ACTION_HANDLERS = {
    "shelly_pulse": shelly_action.run,
    "save_unknown": run_save_unknown,
    "log_visit": run_log_visit,
    "console_announcement": run_console_announcement,
    "announcement": announcement_action.run,
}


def run_action(action_id: str, config: dict, context: dict) -> str:
    actions = config.get("actions", {})
    action_config = actions.get(action_id)

    if not action_config:
        print(f"Action nicht gefunden: {action_id}")
        return f"{action_id}:not_found"

    if not action_config.get("enabled", True):
        print(f"Action deaktiviert: {action_id}")
        return f"{action_id}:disabled"

    action_type = action_config.get("type")
    handler = ACTION_HANDLERS.get(action_type)

    if not handler:
        print(f"Kein Handler für Action-Typ: {action_type}")
        return f"{action_id}:no_handler"

    try:
        result = handler(action_config, context)
        return f"{action_id}:{result}"
    except Exception as error:
        print(f"Fehler bei Action {action_id}: {error}")
        return f"{action_id}:error"


def run_actions(event: dict, result: dict, config: dict) -> int:
    people = load_people()

    subject = result.get("subject")
    person = get_person_config(subject, people)
    announcement = build_announcement(result, people)

    context = {
        "event": event,
        "result": result,
        "person": person,
        "announcement": announcement,
        "action_summary": "no_action",
        "config": config
    }

    print("\n=== Entscheidung ===")
    print(f"Source: {event.get('source_id')} ({event.get('source_name')})")
    print(f"Event Type: {event.get('event_type')}")
    print(f"Status: {result.get('status')}")
    print(f"Person: {subject}")
    print(f"Rolle: {person.get('role', 'unknown')}")
    print(f"Open Gate: {bool(person.get('open_gate', False))}")
    print(f"Hits: {int(result.get('hits', 0))}")
    print(f"Avg Distance-Threshold: {float(result.get('avg_similarity', 0.0)):.3f}")
    print(f"Ansage: {announcement}")

    executed = []

    for rule in config.get("rules", []):
        if not rule_matches(rule, context):
            continue

        print(f"Regel trifft zu: {rule.get('name', 'ohne Name')}")

        action_results_for_rule = []

        for action_id in rule.get("actions", []):
            # log_visit soll die vorherigen Action-Ergebnisse loggen.
            context["action_summary"] = ",".join(executed + action_results_for_rule) or "rule_matched"

            action_result = run_action(action_id, config, context)
            action_results_for_rule.append(action_result)

        executed.extend(action_results_for_rule)

    if not executed:
        print("Keine Regel hat ausgelöst.")

        # Optional: falls du wirklich jeden Event loggen willst,
        # lege eine Catch-all-Regel in settings.json an.
        return 0

    return 0