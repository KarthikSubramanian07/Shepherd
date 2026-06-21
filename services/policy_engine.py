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
        # URLs belong in text, not target — skip app-name check if mis-placed.
        if allowed and "://" not in target and target not in allowed:
            return {
                "verdict": "halt",
                "reason":  f"App '{target}' not in containment allowlist",
            }

    if action_type in ("open_app", "browser") and looks_like_url:
        host = _url_host(target)

        # SSRF floor for the cloud browser: even with a permissive (empty) domain
        # allowlist, never let a planned/captured URL reach loopback, private,
        # link-local, or cloud-metadata endpoints, and never a non-http(s) scheme.
        if action_type == "browser":
            scheme = _url_scheme(target)
            if scheme and scheme not in ("http", "https"):
                return {"verdict": "halt",
                        "reason": f"Blocked non-web scheme '{scheme}' (SSRF guard)"}
            if _is_internal_host(host):
                return {"verdict": "halt",
                        "reason": f"Blocked internal/metadata host '{host}' (SSRF guard)"}

        # Domain allowlist (when configured): exact host or a true subdomain, not a
        # naive substring (which 'example.com' would also pass for evil-example.com).
        allowed_domains = c.get("allowed_domains", [])
        if allowed_domains and host:
            ok = any(host == d or host.endswith("." + d) for d in allowed_domains)
            if not ok:
                return {
                    "verdict": "halt",
                    "reason":  f"Domain '{host}' not in containment allowlist",
                }

    return None


def _url_scheme(target: str) -> Optional[str]:
    from urllib.parse import urlparse
    try:
        return (urlparse(target).scheme or "").lower() or None
    except Exception:
        return None


def _url_host(target: str) -> Optional[str]:
    from urllib.parse import urlparse
    try:
        t = target if "://" in target else f"http://{target}"
        return (urlparse(t).hostname or "").lower() or None
    except Exception:
        return None


def _is_internal_host(host: Optional[str]) -> bool:
    """True for loopback / private / link-local / metadata targets that a cloud
    browser must never reach."""
    if not host:
        return False
    if host in ("localhost", "metadata.google.internal") or host.endswith((".local", ".internal")):
        return True
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
        return (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
    except ValueError:
        return False  # a normal hostname; the allowlist (if any) governs it


def get_limits() -> dict:
    """Return the containment rate/size limits."""
    p = _load()
    c: dict = p.get("containment", {})
    return {
        "max_actions_per_minute": c.get("max_actions_per_minute", 0),
        "max_steps_per_run":      c.get("max_steps_per_run", 0),
    }
