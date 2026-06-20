"""Context integration — criteria unpublished. VERIFY Saturday on Slack."""
from config import FEATURES


def get_context_for_routine(routine_id: str) -> dict:
    if not FEATURES["context"]:
        return {}
    print("[context] VERIFY criteria and API Saturday before implementing.")
    return {}
