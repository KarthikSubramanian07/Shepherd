"""Agent Session Replay — Session Replay, but for a screen-driving agent.

Every agent turn produces a screenshot (what it saw) plus reasoning and a grounded
action (what it decided). This module keeps a small per-run ring buffer of those
"frames" and, the moment a run fails or is halted, attaches them to the captured
Sentry event as:

  - a textual breadcrumb timeline (always, rides any event in this thread's scope)
  - the screenshots as image attachments  ("frame_NN_stepX_kind.png")
  - a "session_replay.json" manifest stitching coords/reasoning to each frame

so a Sentry issue shows a frame-by-frame replay of exactly what the agent saw and
where it clicked leading up to the failure. Off the click path; no-op when Sentry
is disabled. Bytes never touch the event bus (which JSON-broadcasts to the HUD).
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Optional

from config import FEATURES

# Only the frames leading up to a failure matter, so the per-run buffer is bounded.
_MAX_FRAMES = 12
# Buffers persist past run end (so a late run-level capture still has them) but the
# number of retained runs is capped so a long session can't leak memory.
_MAX_RUNS = 8

_frames: "dict[str, deque]" = {}
_lock = threading.Lock()


def _enabled() -> bool:
    return bool(FEATURES.get("sentry"))


def start(run_id: str) -> None:
    """Drop any stale buffer for this run id at the start of a fresh run."""
    if not run_id:
        return
    with _lock:
        _frames.pop(run_id, None)


def record(
    run_id: str,
    *,
    step_index: int,
    kind: str,
    label: str,
    reasoning: Optional[str] = None,
    target: Optional[str] = None,
    coords: Optional[tuple] = None,
    code: Optional[str] = None,
    screenshot_png: Optional[bytes] = None,
    status: str = "ok",
) -> None:
    """Record one agent turn as a replay frame and mirror it as a Sentry breadcrumb."""
    if not _enabled() or not run_id:
        return
    frame = {
        "ts": round(time.time(), 3),
        "step_index": step_index,
        "kind": kind,
        "label": (label or "")[:200],
        "reasoning": (reasoning or "")[:600] or None,
        "target": target,
        "coords": list(coords) if coords else None,
        "code": (code or "")[:400] or None,
        "status": status,
    }
    with _lock:
        if run_id not in _frames and len(_frames) >= _MAX_RUNS:
            # Evict the oldest run (dict preserves insertion order).
            _frames.pop(next(iter(_frames)), None)
        buf = _frames.setdefault(run_id, deque(maxlen=_MAX_FRAMES))
        buf.append((frame, screenshot_png))
    # Textual breadcrumb — appears on the timeline of any event captured next in
    # this thread's scope (engine runs in a propagate_scope thread).
    try:
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="agent.frame",
            message=f"[{step_index}] {kind}: {frame['label']}",
            level="error" if status == "error" else "info",
            data={k: v for k, v in (
                ("step_index", step_index), ("kind", kind),
                ("target", target), ("coords", frame["coords"]),
                ("status", status),
            ) if v is not None},
        )
    except Exception:
        pass


def attach_to_scope(scope, run_id: Optional[str]) -> None:
    """Attach the buffered screenshots + a JSON manifest to a Sentry scope so the
    captured event carries a visual filmstrip of the agent's final frames."""
    if not _enabled() or not run_id:
        return
    with _lock:
        buf = list(_frames.get(run_id, ()))
    if not buf:
        return
    manifest = []
    try:
        for n, (frame, png) in enumerate(buf):
            image_name = None
            if png:
                image_name = f"frame_{n:02d}_step{frame['step_index']}_{frame['kind']}.png"
                try:
                    scope.add_attachment(
                        bytes=png, filename=image_name, content_type="image/png")
                except Exception:
                    image_name = None
            manifest.append({**frame, "image": image_name})
        scope.add_attachment(
            bytes=json.dumps(manifest, indent=2).encode(),
            filename="session_replay.json",
            content_type="application/json",
        )
        scope.set_context("session_replay", {
            "frames": len(manifest),
            "images": sum(1 for m in manifest if m.get("image")),
            "last_action": manifest[-1].get("label") if manifest else None,
        })
    except Exception:
        pass


def clear(run_id: str) -> None:
    if not run_id:
        return
    with _lock:
        _frames.pop(run_id, None)
