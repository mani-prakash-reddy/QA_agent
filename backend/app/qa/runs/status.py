def status_from_agents(statuses: list[str]) -> str:
    """Overall status from sub-agent outcomes.

    `budget_exceeded` is a partial result (the model is usable, coverage is incomplete), not a failure;
    the run summary carries the `partial` flag so the UI can say so.
    """
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "stopped" for s in statuses):
        return "stopped"
    return "succeeded"


def is_partial(statuses: list[str]) -> bool:
    return any(s == "budget_exceeded" for s in statuses)
