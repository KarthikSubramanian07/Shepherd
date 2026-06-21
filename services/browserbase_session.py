"""
Browserbase session manager — a *persistent* cloud browser per agent.

The original ``browserbase_routine.py`` opens a session, runs one action, and
closes it. Multi-agent work needs the opposite: a session that lives for the
whole agent run so the driver can take many turns in it. Each session is its own
isolated surface, so N agents browse in parallel without touching each other or
the local desktop.

Graceful degradation, in order:
  1. **Cloud** — real Browserbase session over CDP (needs key + project id).
  2. **Local** — a headed Playwright Chromium window on this machine, so the
     "multiple windows" story still works offline (each agent gets its own real
     browser window).
  3. **Stub** — neither available: an unavailable session the driver no-ops on
     with a clear message.

Each session is created, used, and closed on the SAME worker thread, so the
Playwright sync API is safe here.
"""
from __future__ import annotations

import math
import os
import threading
import uuid
from typing import Optional

from config import FEATURES, BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, SCREEN_WIDTH, SCREEN_HEIGHT

# Where the parallel browser windows live:
#   local (default) — isolated Chromium windows ON THIS MACHINE. Nothing cloud.
#   cloud           — Browserbase cloud sessions (opt-in; needs key + project id).
#   auto            — try cloud first, fall back to local.
BROWSER_BACKEND = os.getenv("BROWSER_BACKEND", "local").strip().lower()

# ── Non-overlapping window tiling ────────────────────────────────────────────
# Each local window claims a distinct grid slot so N parallel agents' windows
# tile the screen instead of stacking on top of each other. Slots are freed on
# close and reused, so long-running sessions never overlap.
_TILE_COUNT = max(1, int(os.getenv("MAX_BROWSERBASE_SESSIONS", "3")))
_slot_lock = threading.Lock()
_used_slots: set[int] = set()


def _alloc_slot() -> int:
    """Claim the lowest free grid slot."""
    with _slot_lock:
        i = 0
        while i in _used_slots:
            i += 1
        _used_slots.add(i)
        return i


def _free_slot(slot: Optional[int]) -> None:
    if slot is None:
        return
    with _slot_lock:
        _used_slots.discard(slot)


def _tile_geometry(slot: int) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for a slot in a grid sized for _TILE_COUNT windows.

    The grid is the squarest layout that fits _TILE_COUNT cells; each window is
    one cell, so cells never overlap. A slot beyond the grid wraps (modulo) onto
    an existing cell rather than spilling off-screen.
    """
    total = max(_TILE_COUNT, slot + 1)
    cols = math.ceil(math.sqrt(total))
    rows = math.ceil(total / cols)
    i = slot % (cols * rows)
    col, row = i % cols, i // cols
    w = SCREEN_WIDTH // cols
    h = SCREEN_HEIGHT // rows
    return col * w, row * h, w, h


class BrowserbaseSession:
    """Wraps a live page (cloud or local) behind one uniform interface."""

    def __init__(self, kind: str, session_id: str) -> None:
        self.kind = kind                       # 'cloud' | 'local' | 'stub'
        self.session_id = session_id
        self.live_view_url: Optional[str] = None
        self.page = None
        self._pw = None
        self._browser = None
        self._bb = None                        # Browserbase client (cloud teardown)
        self._cloud_id: Optional[str] = None
        self._slot: Optional[int] = None       # grid slot for non-overlapping tiling
        self._halt = threading.Event()

    @property
    def available(self) -> bool:
        return self.kind in ("cloud", "local") and self.page is not None

    def request_halt(self) -> None:
        self._halt.set()

    @property
    def halted(self) -> bool:
        return self._halt.is_set()

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        if self._bb is not None and self._cloud_id:
            try:
                self._bb.complete_session(self._cloud_id)
            except Exception:
                pass
        _free_slot(self._slot)
        self._slot = None
        self.page = None


def open_session(agent_id: str = "") -> BrowserbaseSession:
    """Open a persistent, isolated browser window for one agent.

    Local by default — each agent gets its own Chromium window on this machine and
    they run in parallel, nothing on the cloud. Cloud Browserbase is strictly
    opt-in via BROWSER_BACKEND=cloud (or =auto to prefer cloud, fall back local).
    """
    sid = uuid.uuid4().hex[:12]

    # Cloud only when explicitly requested.
    if BROWSER_BACKEND in ("cloud", "auto"):
        cloud = _try_cloud(sid)
        if cloud is not None:
            print(f"[browser] cloud session {cloud.session_id} for {agent_id}")
            return cloud
        if BROWSER_BACKEND == "cloud":
            print("[browser] BROWSER_BACKEND=cloud but Browserbase unavailable — "
                  "using a local window instead")

    local = _try_local(sid)
    if local is not None:
        print(f"[browser] local Chromium window for {agent_id} (no cloud)")
        return local

    print(f"[browser] no local Playwright (run: uv run playwright install chromium) "
          f"— {agent_id} browser agent unavailable")
    return BrowserbaseSession(kind="stub", session_id=sid)


def _try_cloud(sid: str) -> Optional[BrowserbaseSession]:
    if not FEATURES.get("browserbase") or not BROWSERBASE_PROJECT_ID:
        return None
    try:
        from browserbase import Browserbase
        from playwright.sync_api import sync_playwright

        bb = Browserbase(api_key=BROWSERBASE_API_KEY, project_id=BROWSERBASE_PROJECT_ID)
        cloud = bb.create_session()
        connect_url = bb.get_connect_url(cloud.id)

        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(connect_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        sess = BrowserbaseSession(kind="cloud", session_id=cloud.id)
        sess._pw, sess._browser, sess.page = pw, browser, page
        sess._bb, sess._cloud_id = bb, cloud.id
        sess.live_view_url = _cloud_live_url(bb, cloud.id)
        return sess
    except Exception as e:
        print(f"[browserbase] cloud session failed ({e}) — trying local")
        return None


def _cloud_live_url(bb, cloud_id: str) -> Optional[str]:
    """Best-effort live-view URL for the Control Hub embed (SDK-version tolerant)."""
    for getter in ("get_live_view_url", "get_debug_url"):
        try:
            fn = getattr(bb, getter, None)
            if fn:
                return fn(cloud_id)
        except Exception:
            continue
    return None


def _try_local(sid: str) -> Optional[BrowserbaseSession]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    slot = _alloc_slot()
    x, y, w, h = _tile_geometry(slot)
    try:
        pw = sync_playwright().start()
        # Tile this window into its grid slot so parallel agents' windows never
        # overlap. --window-position/-size place + size the OS window; the
        # context uses no_viewport so Playwright doesn't force its own size and
        # shrink the window back over a neighbour.
        browser = pw.chromium.launch(
            headless=False,
            args=[f"--window-position={x},{y}", f"--window-size={w},{h}"],
        )
        ctx = browser.new_context(no_viewport=True)
        page = ctx.new_page()
        sess = BrowserbaseSession(kind="local", session_id=sid)
        sess._pw, sess._browser, sess.page, sess._slot = pw, browser, page, slot
        return sess
    except Exception as e:
        _free_slot(slot)
        print(f"[browserbase] local Chromium launch failed ({e})")
        return None
