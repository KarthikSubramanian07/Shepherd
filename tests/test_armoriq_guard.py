"""
ArmorIQ guard — graceful degradation + plan/policy shaping.

ArmorIQ is never load-bearing: with no key it is inert and runs proceed. When it
is configured, the resolved steps must shape into a valid ArmorIQ plan and the
allow/deny policy must track Shepherd's own containment triggers.
"""
from services import armoriq_guard


class _Step:
    def __init__(self, action, target=None):
        self.action = action
        self.target = target


def test_disabled_is_a_noop(monkeypatch):
    # Force the feature off regardless of the ambient .env so this never hits the
    # live API: inert, returns None (caller falls back to no enforcement).
    monkeypatch.setitem(armoriq_guard.FEATURES, "armoriq", False)
    monkeypatch.setattr(armoriq_guard, "_client", None)
    assert armoriq_guard.available() is False
    assert armoriq_guard.authorize_run("ROUTINE_X", [_Step("open")], {}) is None


def test_build_plan_shapes_steps():
    plan = armoriq_guard._build_plan(
        "send email", [_Step("open", "Mail"), _Step("type", "body")], {"to": "x"}
    )
    assert plan["goal"] == "send email"
    assert len(plan["steps"]) == 2
    assert plan["steps"][0] == {"action": "open", "mcp": "shepherd-desktop",
                                "params": {"index": 0, "target": "Mail"}}
    assert plan["variables"] == {"to": "x"}


def test_policy_denies_halt_triggers_from_containment():
    pol = armoriq_guard._policy_from_containment()
    assert pol["allow"] == ["shepherd-desktop/*"]
    # data/policy.yaml halts on external_send + credential — those become denies.
    assert any("external_send" in d for d in pol["deny"])


def test_field_reads_dict_or_object():
    assert armoriq_guard._field({"token": "ak"}, "token") == "ak"
    assert armoriq_guard._field(_Step("x"), "action") == "x"
    assert armoriq_guard._field({}, "missing") is None
