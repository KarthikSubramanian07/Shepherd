"""Fieldguide integration — criteria unpublished. VERIFY Saturday on Slack."""
from config import FEATURES


def submit_audit_record(run_id: str, routine_id: str, status: str) -> None:
    if not FEATURES["fieldguide"]:
        return
    print("[fieldguide] VERIFY criteria and API Saturday before implementing.")
