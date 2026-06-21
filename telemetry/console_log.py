"""
Comprehensive stdout logger.

Subscribes to the event bus and prints a readable line for EVERY workflow event —
intent, routing, planning, each step, monitor/policy/verifier verdicts, halts,
graph load/save, and workflow traversal. One subscriber gives full-lifecycle
visibility in the agent's terminal without scattering prints across the codebase.

Enabled at startup by main.py when CONSOLE_LOG is set (default on).
"""
import time

from dashboard.events import event_bus

# Per-span telemetry is too chatty for a human terminal; the dashboard still gets it.
_SKIP = {"trace.span.start", "trace.span.end"}


def _fmt(t: str, d: dict) -> str:
    g = d.get
    if t == "intent.received":
        return f"INTENT    \"{g('raw_text', '')}\" (via {g('source', '?')})"
    if t == "routine.resolved":
        return f"ROUTE     -> {g('routine_id')}  conf={g('confidence')}  kw={g('matched_keywords')}"
    if t == "plan.resolved":
        return f"ROUTE     plan={g('kind')} target={g('target')} conf={g('confidence')} src={g('source')}"
    if t == "intent.unmatched":
        return f"ROUTE     no match — \"{g('raw_text', '')}\""
    if t == "intent.autonomous_fallback":
        return f"ROUTE     no routine -> autonomous: \"{g('raw_text', '')}\""
    if t == "routine.planning":
        return f"PLAN      drafting routine for: {g('goal')}"
    if t == "routine.planned":
        steps = " -> ".join(s.get('description', '') for s in g('steps', []))
        return f"PLAN      {g('total_steps')} steps: {steps}"
    if t == "routine.plan_failed":
        return f"PLAN      failed: {g('error')}"
    if t == "execution.start":
        return f"EXEC      start {g('routine_id')} mode={g('mode')} steps={g('total_steps')}"
    if t == "step.start":
        return f"  STEP {g('index')} start  [{g('action')}] {g('description', '')}"
    if t == "step.agent_s_thinking":
        return f"  STEP {g('index')} thinking..."
    if t == "agent.reasoning":
        who = g("agent_id", "?")
        line = f"  THINK [{who}] t{g('turn')} ({g('status', '')}): {g('reasoning', '') or '(none)'}"
        ops = g("ops", [])
        if ops:
            line += f"\n         plan: {' | '.join(ops)}"
        return line
    if t == "step.complete":
        dev = f" (deviation: {g('deviation')})" if g('deviation') else ""
        return f"  STEP {g('index')} done   {g('status')} ({g('duration_ms')}ms){dev}"
    if t == "step.error":
        return f"  STEP {g('index')} ERROR  {g('error')}"
    if t == "step.deviation":
        return f"  STEP {g('step_index')} deviation: {g('reason')}"
    if t == "step.fallback":
        return f"  STEP {g('index')} fallback: {g('reason')}"
    if t == "step.browser":
        return f"  STEP browser {g('url', '')}"
    if t == "execution.halted":
        return f"HALT      step {g('step_index')} — {g('reason')}"
    if t == "execution.complete":
        line = f"EXEC      done {g('status')} — {g('steps_completed')} steps in {g('duration_ms')}ms"
        if g("response"):
            line += f"\n           ↳ {g('response')}"
        return line
    if t == "monitor.alert":
        return f"MONITOR   {str(g('verdict', '')).upper()} @ step {g('step_index')}: {g('reason')}"
    if t == "monitor.decision":
        return f"MONITOR   decision: {g('decision')}"
    if t == "monitor.auto_resolved":
        return f"MONITOR   auto-resolved step {g('step_index')}: {g('action', '')}"
    if t == "verifier.result":
        return f"VERIFIER  {g('verdict')}: {g('reason', '')}"
    if t == "task.graph.loaded":
        return (f"GRAPH     loaded {g('routine_id')} "
                f"(runs={g('run_count')}, nodes={g('node_count')}, known={g('known')})")
    if t == "task.graph.saved":
        return (f"GRAPH     saved {g('routine_id')} "
                f"(runs={g('run_count')}, nodes={g('node_count')})")
    if t in ("remote.intent", "remote.intent.received"):
        return f"REMOTE    intent from {g('source', '?')}: \"{g('text', '')}\""
    if t == "mode.changed":
        return f"MODE      -> {g('mode')}"
    if t.startswith("workflow."):
        ref = g('node_key') or g('workflow_id') or ''
        return f"WORKFLOW  {t.split('.', 1)[1]} {ref}".rstrip()
    return f"{t}  {d}"   # generic fallback for any unmapped event


_started = False


def start_console_logging() -> None:
    """Begin printing every workflow event to stdout. Idempotent — calling it
    more than once is a no-op (a second subscriber would print every line twice)."""
    global _started
    if _started:
        return
    _started = True

    def _on(message: dict) -> None:
        t = message.get("type")
        if not t or t in _SKIP:
            return
        try:
            line = _fmt(t, message.get("data") or {})
        except Exception:
            line = f"{t}  {message.get('data')}"
        print(f"{time.strftime('%H:%M:%S')} | {line}", flush=True)

    event_bus.subscribe(_on)
    print("[console] comprehensive event logging enabled")
