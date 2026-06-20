"""
Coordinate map loader.
All coords are LOGICAL points (physical_px / retina_scale).
pyautogui accepts logical points on macOS.
"""
import json
import os
import time
from typing import Optional

_COORDS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "coords.demo.json")
_cache: dict | None = None


def load_coords(path: str = _COORDS_PATH) -> dict:
    global _cache
    if _cache is None:
        with open(path) as f:
            _cache = json.load(f)
    return _cache


def get(key: str, coords: Optional[dict] = None) -> tuple[int, int]:
    c = coords or load_coords()
    entry = c.get(key)
    if entry is None:
        raise KeyError(
            f"Coordinate key '{key}' not found. "
            "Re-calibrate on this display: python -c \"from engine.coords import calibration_helper; calibration_helper()\""
        )
    return int(entry["x"]), int(entry["y"])


def physical_to_logical(px: int, py: int, scale: int = 2) -> tuple[int, int]:
    """Convert physical Retina pixel coords → logical pyautogui coords."""
    return px // scale, py // scale


def calibration_helper() -> None:
    """
    Interactive calibration: prints logical mouse position every second.
    Run on the demo machine to re-map coordinates for the venue display.
    """
    import pyautogui
    print("Calibration mode — move mouse to each target.")
    print("Printing LOGICAL position (store these in data/coords.demo.json). Ctrl-C to stop.\n")
    try:
        while True:
            x, y = pyautogui.position()
            print(f"  logical=({x:4d}, {y:4d})  |  physical≈({x*2:4d}, {y*2:4d})")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nCalibration done.")
