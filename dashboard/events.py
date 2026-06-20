"""
Local event bus — the only channel between engine and dashboard.
Engine emits synchronously (fire-and-forget from any thread).
Dashboard WebSocket server subscribes and broadcasts to all clients.
No network dependency; fully offline.
"""
import asyncio
import threading
from collections import deque
from typing import Callable

_MAX_HISTORY = 500


class EventBus:
    def __init__(self) -> None:
        self._subs:  list[Callable] = []
        self._lock   = threading.Lock()
        self._history: deque[dict] = deque(maxlen=_MAX_HISTORY)
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once by the dashboard server after its event loop starts."""
        self._loop = loop

    def subscribe(self, fn: Callable) -> None:
        with self._lock:
            self._subs.append(fn)

    def unsubscribe(self, fn: Callable) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s is not fn]

    def emit(self, event_type: str, data: dict) -> None:
        """Thread-safe emit. Delivers to async subscribers via the dashboard event loop."""
        message = {"type": event_type, "data": data}
        self._history.append(message)
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)

    async def _broadcast(self, message: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(message)
                else:
                    fn(message)
            except Exception:
                pass

    def get_history(self) -> list[dict]:
        return list(self._history)


event_bus = EventBus()
