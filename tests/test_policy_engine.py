"""
Policy engine — the rule-based core of the oversight stack. These are the
governance guarantees the whole product rests on, so they get explicit unit tests
(verdicts, screen rules, rate/size limits), independent of any model or network.
"""
from services import policy_engine


# ── triggers: the planted, deterministic high-stakes signals ─────────────────

def test_trigger_credential_and_external_send_halt():
    assert policy_engine.evaluate_trigger("credential")["verdict"] == "halt"
    assert policy_engine.evaluate_trigger("external_send")["verdict"] == "halt"
    assert policy_engine.evaluate_trigger("captcha")["verdict"] == "halt"


def test_trigger_payment_flags_not_halts():
    # payment is a "flag" (human decides), not an automatic halt.
    assert policy_engine.evaluate_trigger("payment")["verdict"] == "flag"


def test_unknown_trigger_fails_safe_to_flag():
    # Conservative default: an unrecognized trigger flags for a human, never "ok".
    v = policy_engine.evaluate_trigger("totally-unknown-trigger")
    assert v["verdict"] == "flag"


# ── screen rules: text-match guards (credentials, secrets) ───────────────────

def test_screen_rule_halts_on_credential_text():
    v = policy_engine.evaluate_screen("Please enter your password to continue")
    assert v is not None and v["verdict"] == "halt"


def test_screen_rule_passes_benign_text():
    assert policy_engine.evaluate_screen("Welcome to the dashboard. View your reports.") is None


# ── containment limits ───────────────────────────────────────────────────────

def test_get_limits_shape():
    lim = policy_engine.get_limits()
    assert "max_actions_per_minute" in lim and "max_steps_per_run" in lim
    assert isinstance(lim["max_actions_per_minute"], int)


# ── containment: app/url gating (complements test_policy_ssrf) ───────────────

def test_open_app_blocked_when_allowlist_set(monkeypatch):
    monkeypatch.setattr(policy_engine, "_load",
                        lambda: {"containment": {"allowed_apps": ["Safari", "TextEdit"]}})
    blocked = policy_engine.check_containment("open_app", "Malware.app")
    assert blocked is not None and blocked["verdict"] == "halt"
    assert policy_engine.check_containment("open_app", "Safari") is None


def test_no_target_is_allowed():
    assert policy_engine.check_containment("browser", None) is None
    assert policy_engine.check_containment("open_app", "") is None
