from save_unknown import save_unknown


def run(action_config: dict, context: dict) -> str:
    result = context["result"]

    if result.get("status") != "unknown":
        return "save_unknown_skipped"

    save_unknown(result)
    return "unknown_saved"