"""
Worker status mapping — a suspended or unrecognized autonomous result must never
be reported as a success in the fleet view (it has stopped and needs attention).
"""
from dataclasses import dataclass

from orchestrator.worker import AgentWorker


@dataclass
class _Res:
    status: str


def test_completed_maps_to_completed():
    assert AgentWorker._status_from_result(_Res("completed")) == "completed"


def test_failed_maps_to_failed():
    assert AgentWorker._status_from_result(_Res("failed")) == "failed"


def test_aborted_and_suspended_map_to_halted():
    # A monitor halt / steer / parked failure returns 'suspended'; in orchestrated
    # mode the per-worker engine is gone at run end, so it is halted, not done.
    assert AgentWorker._status_from_result(_Res("aborted")) == "halted"
    assert AgentWorker._status_from_result(_Res("suspended")) == "halted"


def test_unknown_result_fails_safe_not_completed():
    # The dangerous regression: an unrecognized result must not read as success.
    assert AgentWorker._status_from_result(object()) == "failed"
    assert AgentWorker._status_from_result(None) == "failed"
