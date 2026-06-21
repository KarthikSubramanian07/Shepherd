"""
SimuLang compile-and-replay — graduate a taught workflow into a deterministic,
zero-token desktop script.

Shepherd already learns: a human demonstrates or steers a task, the run
crystallizes into a milestone workflow. Today re-running that workflow still costs
a vision-model call per turn (Agent S re-plans from screenshots). SimuLang
(Simular's "Playwright for the desktop") closes that loop: once a workflow is
proven, we compile its milestones into a SimuLang `.ts` script that drives the
desktop off the **accessibility tree** (deterministic, no LLM tokens per run), and
replay it with `npx tsx` against the @simular-ai/simulang-js runtime. Agent S stays
the explorer/fallback; SimuLang is the cheap, auditable replay of what was learned.

Two Simular products composed in their intended shape: Agent S learns the task,
SimuLang replays it. Lazy + graceful: with the runtime absent the workflow still
runs via Agent S exactly as before; compilation always produces the artifact so the
UI can show "graduated to a deterministic script".

Replay requirements on the demo machine: the runtime ships as a prebuilt native
(NAPI) binary installed at the repo root (`npm install`), and replay drives the live
frontmost app through the macOS Accessibility tree, so the terminal/node process
must be granted Accessibility permission (System Settings > Privacy & Security >
Accessibility). Both are one-time, machine-local steps.
"""
import os
import shutil
import subprocess
from typing import Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
_ARTIFACT_DIR = os.path.join(_REPO_ROOT, "data", "simulang")


def available() -> bool:
    """True when the SimuLang replay runtime is installed: the @simular-ai/simulang-js
    package resolves at the repo root and a TS runner (tsx, via npx) is on PATH, so a
    compiled `.ts` script can replay. There is no standalone `simulang` CLI; replay
    executes the script with `npx tsx`."""
    pkg = os.path.join(_REPO_ROOT, "node_modules", "@simular-ai", "simulang-js", "package.json")
    return os.path.exists(pkg) and shutil.which("npx") is not None


def artifact_path(workflow_id: str) -> str:
    return os.path.join(_ARTIFACT_DIR, f"{workflow_id}.ts")


def compile_workflow(workflow) -> Optional[str]:
    """Compile a workflow's milestones into a SimuLang `.ts` script and write it to
    data/simulang/<id>.ts. Returns the path, or None on failure. Always safe to
    call (it only writes an artifact; it does not run anything)."""
    try:
        wid = getattr(workflow, "id", None) or (workflow.get("id") if isinstance(workflow, dict) else None)
        nodes = getattr(workflow, "nodes", None)
        if nodes is None and isinstance(workflow, dict):
            nodes = workflow.get("nodes")
        if not wid or not nodes:
            return None
        os.makedirs(_ARTIFACT_DIR, exist_ok=True)
        script = _render(wid, nodes)
        path = artifact_path(wid)
        with open(path, "w") as f:
            f.write(script)
        print(f"[simulang] compiled {wid} -> {path} (deterministic, 0 tokens/run)")
        return path
    except Exception as e:
        print(f"[simulang] compile non-fatal: {e}")
        return None


def replay(workflow_id: str, *, timeout: float = 120.0) -> Optional[dict]:
    """Replay a compiled SimuLang script with `npx tsx`. Returns a status dict, or
    None when the runtime is unavailable / the artifact is missing (caller falls back
    to Agent S vision replay)."""
    path = artifact_path(workflow_id)
    if not available() or not os.path.exists(path):
        return None
    try:
        proc = subprocess.run(
            ["npx", "tsx", path],
            cwd=_REPO_ROOT,
            capture_output=True, text=True, timeout=timeout,
        )
        ok = proc.returncode == 0
        return {
            "status": "ok" if ok else "failed",
            "engine": "simulang",
            "tokens": 0,
            "stderr": (proc.stderr or "")[-500:],
        }
    except Exception as e:
        print(f"[simulang] replay non-fatal: {e}")
        return None


# ── compiler ─────────────────────────────────────────────────────────────────

def _node_attr(node, key, default=None):
    return getattr(node, key, None) if not isinstance(node, dict) else node.get(key, default)


def _render(workflow_id: str, nodes) -> str:
    """Emit a SimuLang (@simular-ai/simulang-js) script that replays the workflow's
    milestones off the accessibility tree (activate / setValue by role + label),
    not pixels — so it is deterministic and re-runs with zero LLM tokens."""
    lines = [
        "// Auto-compiled by Shepherd from a taught workflow — deterministic replay,",
        "// zero LLM tokens per run. Run with:  npx tsx "
        f"data/simulang/{workflow_id}.ts",
        "import { AccessibilityTree, App, AriaRole, FocusPolicy, Visibility } "
        "from '@simular-ai/simulang-js'",
        "",
        "const app = App.frontmost().open(FocusPolicy.Steal, Visibility.Show, true)",
        "const tree = AccessibilityTree.fromPid(app.pid)",
        "const root = tree.snapshot(true)",
        "const nodes = flattenDFS(root)",
        "",
        "const byLabel = (re, role) => nodes.find(",
        "  (n) => n.refId != null && (role == null || n.role === role) && re.test(labelOf(n)))",
        "",
    ]
    for node in nodes:
        kind = (_node_attr(node, "kind") or "").lower()
        label = _node_attr(node, "label") or _node_attr(node, "key") or "step"
        value = _node_attr(node, "value")
        safe = _re_escape(label)
        comment = f"// {label}"
        if kind in ("fill", "type") or value:
            lines.append(comment)
            lines.append(f"{{ const el = byLabel(/{safe}/i, AriaRole.Textbox); "
                         f"if (el) tree.setValue(el.refId, {_js_str(value or '')}) }}")
        elif kind in ("submit", "click", "open", "navigate", "interact", "search"):
            lines.append(comment)
            lines.append(f"{{ const el = byLabel(/{safe}/i); if (el) tree.activate(el.refId) }}")
        else:
            lines.append(f"// (skipped non-actionable milestone: {label})")
        lines.append("")
    lines.append("// End of compiled workflow.")
    return "\n".join(lines) + "\n"


def _re_escape(text: str) -> str:
    # Escape for embedding inside a JS regex literal /.../
    out = []
    for ch in str(text)[:60]:
        if ch in r"\\/.^$*+?()[]{}|":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out) or "step"


def _js_str(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'
