from log_visit import log_visit


def run(action_config: dict, context: dict) -> str:
    result = context["result"]
    action_summary = context.get("action_summary", "log_only")

    log_visit(
        status=result.get("status"),
        subject=result.get("subject"),
        hits=int(result.get("hits", 0)),
        avg_similarity=float(result.get("avg_similarity", 0.0)),
        action=action_summary
    )

    return "visit_logged"