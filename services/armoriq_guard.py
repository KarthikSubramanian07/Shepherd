"""
ArmorIQ — intent-intelligence authorization for the oversight stack. BOUNDARY ONLY.

Before a run executes, Shepherd captures the resolved plan and asks ArmorIQ to
issue a cryptographically-signed intent token gated by an allow/deny policy
derived from Shepherd's own containment rules (data/policy.yaml). That token is
proof the run's *intent* was authorized — identity + access + runtime enforcement
layered in front of the click path, complementing the SHA-256 audit chain. If
ArmorIQ denies the plan, the run is halted before the first action.

Called once, at run start (never mid-click), feature-flagged, and fully graceful:
with no key it is inert and the run proceeds exactly as before. A transport error
also degrades to "no enforcement" so ArmorIQ being down never bricks a run — only
an explicit authentication/authorization failure blocks.
"""
from typing import Optional

from config import FEATURES, ARMORIQ_API_KEY, ARMORIQ_STRICT

_client = None
_PLAN_LLM = "claude-haiku-4-5"
_MCP = "shepherd-desktop"   # the "tool surface" the agent acts through, in ArmorIQ terms


def available() -> bool:
    return bool(FEATURES["armoriq"]) and _get_client() is not None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not FEATURES["armoriq"]:
        return None
    try:
        from armoriq_sdk import ArmorIQClient
        _client = ArmorIQClient(api_key=ARMORIQ_API_KEY)
    except Exception as e:
        print(f"[armoriq] client init failed (non-fatal): {e}")
        _client = None
    return _client


def authorize_run(goal: str, steps, variables: Optional[dict] = None,
                  policy: Optional[dict] = None) -> Optional[dict]:
    """Capture the plan and request a signed intent token.

    Returns:
      {"authorized": bool, "token": str|None, "reason": str, "plan_hash": str|None}
    or None when ArmorIQ is not configured / unreachable (caller treats None as
    "no enforcement" and proceeds).
    """
    client = _get_client()
    if client is None:
        return None
    try:
        plan = _build_plan(goal, steps, variables or {})
        captured = client.capture_plan(llm=_PLAN_LLM, prompt=goal or "shepherd run", plan=plan)
        resolved_policy = policy or _policy_from_containment()
        resp = client.get_intent_token(
            plan_capture=captured,
            policy=resolved_policy,
            validity_seconds=1800,
        )
        # The signed JWT intent token is the authorization artifact. ArmorIQ also
        # enforces the tenant's own control-plane policy (decision_source "native")
        # and reports it in policy_validation. We block when the token is missing or
        # expired; a tenant-policy denial only halts the run in strict mode, so an
        # un-configured tenant never bricks a run (the in-house policy engine still
        # gates). Denials are always surfaced for the audit trail.
        token   = _field(resp, "jwt_token") or _field(resp, "raw_token") or _field(resp, "token")
        expired = bool(_field(resp, "is_expired"))
        pv      = _field(resp, "policy_validation") or {}
        denied  = list(pv.get("denied_tools") or []) if isinstance(pv, dict) else []
        authorized = bool(token) and not expired and (not ARMORIQ_STRICT or not denied)
        if denied:
            print(f"[armoriq] tenant policy flagged tools {denied} (strict={ARMORIQ_STRICT})")
        if not token:
            reason = "ArmorIQ returned no signed token"
        elif expired:
            reason = "ArmorIQ token already expired"
        elif denied and ARMORIQ_STRICT:
            reason = f"ArmorIQ tenant policy denied tools: {denied}"
        elif denied:
            reason = f"ArmorIQ intent token issued (advisory: tenant policy flagged {denied})"
        else:
            reason = "ArmorIQ intent token issued"
        return {
            "authorized":   authorized,
            "token":        token,
            "reason":       reason,
            "plan_hash":    _field(resp, "plan_hash"),
            "denied_tools": denied,
        }
    except Exception as e:
        name = type(e).__name__
        # Auth / token-issuance failures are real denials → block the run.
        # Network / unknown errors degrade to "no enforcement" so a flaky link
        # never strands an otherwise-fine run.
        if name in ("AuthenticationError", "TokenIssuanceError"):
            return {"authorized": False, "token": None,
                    "reason": f"ArmorIQ denied the plan ({name}): {e}", "plan_hash": None}
        print(f"[armoriq] authorize skipped (non-fatal {name}): {e}")
        return None


# ── internals ──────────────────────────────────────────────────────────────

def _field(resp, name: str):
    """Read a field whether the SDK returns a dict or a pydantic-ish object."""
    if isinstance(resp, dict):
        return resp.get(name)
    return getattr(resp, name, None)


def _build_plan(goal: str, steps, variables: dict) -> dict:
    """Shape Shepherd's resolved steps into an ArmorIQ execution plan."""
    out = []
    for i, s in enumerate(steps):
        if isinstance(s, dict):
            action, target = s.get("action"), s.get("target")
        else:
            action, target = getattr(s, "action", None), getattr(s, "target", None)
        out.append({
            "action": action or "step",
            "mcp":    _MCP,
            "params": {"index": i, "target": target},
        })
    return {"goal": goal or "shepherd run", "steps": out, "variables": variables}


def _policy_from_containment() -> dict:
    """Derive an ArmorIQ allow/deny policy from Shepherd's live containment rules.

    Halt triggers in data/policy.yaml become explicit denies on the desktop tool
    surface; everything else on that surface is allowed. This keeps the two
    governance layers in lockstep — change policy.yaml and ArmorIQ's policy moves
    with it (policy.yaml is hot-reloaded)."""
    try:
        from services import policy_engine
        p = policy_engine._load()
        triggers = p.get("triggers", {}) or {}
        deny = [f"{_MCP}/{t}" for t, v in triggers.items() if v == "halt"]
        return {"allow": [f"{_MCP}/*"], "deny": deny}
    except Exception:
        return {"allow": [f"{_MCP}/*"], "deny": []}
