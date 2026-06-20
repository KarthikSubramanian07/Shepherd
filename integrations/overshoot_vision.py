"""
Overshoot RealtimeVision — passive screen-vision audit stream.
Runs as a parallel daemon thread. NEVER imported inside engine.execute().
Emits vision.update events to the dashboard; degrades gracefully offline.
VERIFY: JS SDK only — confirm REST API at docs.overshoot.ai before implementing.
"""
import threading
import time
from config import FEATURES, OVERSHOOT_API_KEY
from dashboard.events import event_bus

_running  = threading.Event()
_thread: threading.Thread | None = None
POLL_INTERVAL = 3.0  # seconds between screen captures


def start_vision_stream() -> None:
    if not FEATURES["overshoot"]:
        event_bus.emit("vision.offline", {"reason": "overshoot feature disabled"})
        return
    global _thread
    _running.set()
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    print("[overshoot] Vision stream started.")


def stop_vision_stream() -> None:
    _running.clear()


def _loop() -> None:
    while _running.is_set():
        try:
            desc = _capture_and_describe()
            event_bus.emit("vision.update", {"description": desc, "ts": time.time()})
        except Exception as e:
            event_bus.emit("vision.offline", {"reason": str(e)})
            _running.clear()
            return
        time.sleep(POLL_INTERVAL)


def _capture_and_describe() -> str:
    """
    Capture screen → POST to Overshoot → return one-phrase description.
    VERIFY: endpoint, auth, and request schema at docs.overshoot.ai.
    """
    if not OVERSHOOT_API_KEY:
        raise RuntimeError("OVERSHOOT_API_KEY not set")

    import io, base64, httpx, pyautogui
    shot = pyautogui.screenshot()
    buf  = io.BytesIO()
    shot.save(buf, format="PNG")
    b64  = base64.b64encode(buf.getvalue()).decode()

    # VERIFY endpoint and schema before the event
    resp = httpx.post(
        "https://api.overshoot.ai/v1/vision/describe",  # VERIFY
        headers={"Authorization": f"Bearer {OVERSHOOT_API_KEY}"},
        json={
            "image":  b64,
            "prompt": "Describe what the AI agent is doing on screen in one short phrase.",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json().get("description", "")
