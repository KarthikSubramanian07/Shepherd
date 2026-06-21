"""
Policy engine — evaluates data/policy.yaml at runtime.

Replaces the hardcoded dicts in monitor_agent.py with a configurable ruleset.
Edit policy.yaml to change what Shepherd halts, flags, or allows — no code
changes needed.

Three entry points:
  evaluate_trigger(trigger)           → {"verdict", "reason"}
  evaluate_screen(ocr_text)           → {"verdict", "reason"} | None
  check_containment(action, target)   → {"verdict", "reason"} | None
"""
import os
from pathlib import Path
from typing import Optional

_POLICY_PATH = Path(os.getenv("POLICY_PATH", "data/policy.yaml"))

# Cached policy dict — reloaded on first call or if file changes.
_policy: dict = {}
_policy_mtime: float = 0.0


def _load() -> dict:
    global _policy, _policy_mtime
    try:
        mtime = _POLICY_PATH.stat().st_mtime
        if mtime != _policy_mtime:
            import yaml  # pyyaml
            _policy = yaml.safe_load(_POLICY_PATH.read_text()) or {}
            _policy_mtime = mtime
    except FileNotFoundError:
        _policy = {}
    except Exception as e:
        print(f"[policy] failed to load {_POLICY_PATH}: {e}")
    return _policy


# ── Trigger overrides ─────────────────────────────────────────────────────────

def evaluate_trigger(trigger: str) -> dict:
    """
    Look up a planted monitor_trigger in the policy triggers map.
    Falls back to hardcoded defaults so the demo always works even without
    a policy file.
    """
    p = _load()
    triggers: dict = p.get("triggers", {})

    _DEFAULTS = {
        "credential": ("halt",  "Credential / password field detected — halting to protect sensitive data"),
        "captcha":    ("halt",  "CAPTCHA detected — human verification required"),
        "phishing":   ("halt",  "Possible prompt injection or phishing content detected"),
        "payment":    ("flag",  "Payment authorization — human approval required"),
        "stuck":      ("flag",  "Possible stuck state — no screen change detected"),
    }

    if trigger in triggers:
        action = triggers[trigger]
        _, default_reason = _DEFAULTS.get(trigger, ("flag", f"Trigger: {trigger}"))
        return {"verdict": action, "reason": default_reason}

    if trigger in _DEFAULTS:
        action, reason = _DEFAULTS[trigger]
        return {"verdict": action, "reason": reason}

    return {"verdict": "flag", "reason": f"Unknown trigger: {trigger}"}


# ── Screen content rules ──────────────────────────────────────────────────────

def evaluate_screen(ocr_text: str) -> Optional[dict]:
    """
    Run OCR text against screen_rules in order. Returns the first matching
    rule's verdict/reason, or None if no rule matches.
    """
    p = _load()
    rules: list = p.get("screen_rules", [])
    text = ocr_text.lower()

    for rule in rules:
        for pattern in rule.get("match_text", []):
            if pattern.lower() in text:
                return {
                    "verdict": rule.get("action", "flag"),
                    "reason":  rule.get("reason", f"Rule '{rule.get('name')}' matched"),
                }
    return None


# ── Containment ───────────────────────────────────────────────────────────────

def check_containment(action_type: str, target: Optional[str]) -> Optional[dict]:
    """
    For open_app and browser steps, verify the target is in the allowlist.
    Returns a halt verdict if blocked, None if allowed.
    """
    if not target:
        return None

    p = _load()
    c: dict = p.get("containment", {})

    # A URL target (even on an open_app step) is governed by the domain allowlist
    # below, not the app-name allowlist — otherwise an allowed host like localhost
    # gets wrongly rejected as an "app".
    looks_like_url = ("://" in target) or ("http" in target) or ("localhost" in target)

    if action_type == "open_app" and not looks_like_url:
        allowed = c.get("allowed_apps", [])
        if allowed and target not in allowed:
            return {
                "verdict": "halt",
                "reason":  f"App '{target}' not in containment allowlist",
            }

    if action_type in ("open_app", "browser"):
        allowed_domains = c.get("allowed_domains", [])
        if allowed_domains:
            matched = any(d in target for d in allowed_domains)
            if not matched and ("http" in target or "." in target):
                return {
                    "verdict": "halt",
                    "reason":  f"Domain in '{target}' not in containment allowlist",
                }

    return None


def get_limits() -> dict:
    """Return the containment rate/size limits."""
    p = _load()
    c: dict = p.get("containment", {})
    return {
        "max_actions_per_minute": c.get("max_actions_per_minute", 0),
        "max_steps_per_run":      c.get("max_steps_per_run", 0),
    }
