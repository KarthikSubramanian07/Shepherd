"""
Regression tests for duplicated stdout lines: the same event was printed N times
because the console logger's subscriber was registered more than once. The bus
now dedupes identical subscribers and start_console_logging() is guarded.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.events import event_bus
import telemetry.console_log as CL


def test_subscribe_is_idempotent():
    calls = []
    fn = lambda m: calls.append(m)
    n0 = len(event_bus._subs)
    event_bus.subscribe(fn)
    event_bus.subscribe(fn)          # second add must be a no-op
    try:
        assert len(event_bus._subs) == n0 + 1
        event_bus.emit("test.idem", {"k": 1})
        assert sum(1 for c in calls if c["type"] == "test.idem") == 1   # fired once
    finally:
        event_bus.unsubscribe(fn)


def test_distinct_subscribers_still_both_register():
    a, b = (lambda m: None), (lambda m: None)
    n0 = len(event_bus._subs)
    event_bus.subscribe(a)
    event_bus.subscribe(b)
    try:
        assert len(event_bus._subs) == n0 + 2   # different callables both kept
    finally:
        event_bus.unsubscribe(a)
        event_bus.unsubscribe(b)


def test_start_console_logging_is_idempotent():
    CL._started = False
    n0 = len(event_bus._subs)
    CL.start_console_logging()
    CL.start_console_logging()       # guarded → no second subscriber
    CL.start_console_logging()
    assert len(event_bus._subs) == n0 + 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
