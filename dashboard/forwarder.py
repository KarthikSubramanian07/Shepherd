"""
Event forwarder — streams an agent process's event_bus events to a SEPARATE,
persistent backend (set BACKEND_URL). This lets the dashboard/API run as its own
long-lived process while agents come and go: the backend keeps showing graphs,
runs, and live activity even after an agent exits.

Fire-and-forget over HTTP and OFF the click path: the event_bus subscriber only
enqueues (never blocks), and a daemon worker POSTs to <backend>/api/ingest.
"""
import queue
import threading

from dashboard.events import event_bus

_q: "queue.Queue[dict]" = queue.Queue(maxsize=10000)
_started = False
_lock = threading.Lock()


def start_forwarding(backend_url: str) -> None:
    """Begin streaming local events to a persistent backend. Idempotent."""
    global _started
    with _lock:
        if _started or not backend_url:
            return
        _started = True

    ingest = backend_url.rstrip("/") + "/api/ingest"

    def _enqueue(message: dict) -> None:
        try:
            _q.put_nowait(message)
        except queue.Full:
            pass  # drop under backpressure — never block the agent

    def _worker() -> None:
        import httpx
        with httpx.Client(timeout=2.0) as client:
            while True:
                msg = _q.get()
                try:
                    client.post(ingest, json=msg)
                except Exception:
                    pass  # backend down / slow is non-fatal to the agent
                finally:
                    _q.task_done()

    threading.Thread(target=_worker, name="event-forwarder", daemon=True).start()
    event_bus.subscribe(_enqueue)
    print(f"[forwarder] streaming events → {ingest}")
