"""
Demonstration recorder — captures a human run into RoutineDefinition.demonstration.

Flow: user presses Cmd+Shift+M to mark a step boundary, optionally speaks a narration
(via Deepgram), then performs the action. Each step captures action + optional instruction.

Lane A owns this module. Lane D wires Deepgram narration via get_narration_fn.
"""
import json
import time
import threading
from typing import Callable, Optional
from pynput import mouse as _mouse, keyboard as _keyboard
from shepherd_types import RecordedStep

MARK_HOTKEY = {_keyboard.Key.cmd, _keyboard.Key.shift, _keyboard.KeyCode.from_char('m')}
STOP_HOTKEY = {_keyboard.Key.cmd, _keyboard.Key.shift, _keyboard.KeyCode.from_char('q')}


class DemonstrationRecorder:
    """
    Records a human demonstration run into a list of RecordedStep objects.
    Attach this to a RoutineDefinition.demonstration so the engine can index against it.
    """

    def __init__(self, get_narration_fn: Optional[Callable[[], str]] = None) -> None:
        self._get_narration = get_narration_fn
        self._steps: list[RecordedStep] = []
        self._index = 0
        self._running = False
        self._keys_held: set = set()
        self._pending_action: Optional[tuple] = None  # (action, target, text)
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self._steps = []
        self._index = 0
        print("[recorder] Started. Cmd+Shift+M = mark step boundary. Cmd+Shift+Q = stop.")
        self._ml = _mouse.Listener(on_click=self._on_click)
        self._kl = _keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._ml.start()
        self._kl.start()

    def stop(self) -> list[RecordedStep]:
        self._running = False
        try:
            self._ml.stop()
            self._kl.stop()
        except Exception:
            pass
        print(f"[recorder] Stopped. {len(self._steps)} steps captured.")
        return self._steps

    def _mark_step(self) -> None:
        with self._lock:
            action, target, text = self._pending_action or ("mark", None, None)
            instruction = None
            if self._get_narration:
                print("[recorder] Speak step instruction now...")
                try:
                    instruction = self._get_narration()
                    print(f"[recorder] Instruction: {instruction!r}")
                except Exception:
                    pass
            step = RecordedStep(
                index=self._index,
                action=action,
                target=target,
                text=text,
                timestamp=time.time(),
                instruction=instruction,
            )
            self._steps.append(step)
            print(f"[recorder] Step {self._index}: {action} @ {target}")
            self._index += 1
            self._pending_action = None

    def _on_click(self, x, y, button, pressed) -> None:
        if not self._running or not pressed:
            return
        with self._lock:
            self._pending_action = ("click", f"{x},{y}", None)

    def _on_press(self, key) -> None:
        if not self._running:
            return
        self._keys_held.add(key)
        if MARK_HOTKEY.issubset(self._keys_held):
            threading.Thread(target=self._mark_step, daemon=True).start()
        elif STOP_HOTKEY.issubset(self._keys_held):
            self.stop()
        else:
            with self._lock:
                try:
                    ch = key.char
                    if self._pending_action and self._pending_action[0] == "type":
                        self._pending_action = ("type", None, (self._pending_action[2] or "") + ch)
                    else:
                        self._pending_action = ("type", None, ch)
                except AttributeError:
                    pass

    def _on_release(self, key) -> None:
        self._keys_held.discard(key)

    def save(self, steps: list[RecordedStep], path: str) -> None:
        data = [
            {"index": s.index, "action": s.action, "target": s.target,
             "text": s.text, "timestamp": s.timestamp, "instruction": s.instruction}
            for s in steps
        ]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[recorder] Saved {len(steps)} steps → {path}")
