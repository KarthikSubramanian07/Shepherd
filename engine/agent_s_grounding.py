"""Screenshot capture and coordinate handling for Agent S (FaceTimeOS-style)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Optional

import pyautogui
from PIL import Image

from config import SCREEN_WIDTH, SCREEN_HEIGHT

# FaceTimeOS caps screenshot dims so grounding models stay within context limits.
# These MUST stay under what the vision API will re-scale to server-side, otherwise
# the model grounds in a different (downscaled) pixel space than the one we register
# as grounding_width/height — and resize_coordinates maps the click to the wrong spot.
# Anthropic downsizes any image whose long edge exceeds ~1568px OR that is above
# ~1.15MP, so we cap on BOTH so our registered dims == what the model actually sees.
MAX_GROUNDING_DIM = 1568
MAX_GROUNDING_PIXELS = 1_150_000

_CLICK_RE = re.compile(
    r"(pyautogui\.(?:click|doubleClick|moveTo|dragTo))\((\d+),\s*(\d+)([^)]*)\)"
)


@dataclass(frozen=True)
class ScreenGeometry:
    """Logical pyautogui space vs resized screenshot fed to the grounding model."""
    logical_w: int
    logical_h: int
    ground_w: int
    ground_h: int


def scale_screen_dimensions(
    width: int, height: int, max_dim_size: int = MAX_GROUNDING_DIM,
    max_pixels: int = MAX_GROUNDING_PIXELS,
) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return max_dim_size, max_dim_size
    # 1) cap the long edge, 2) cap total pixels — whichever is tighter wins, so the
    # image stays under the vision API's server-side downscale threshold.
    scale_factor = min(max_dim_size / width, max_dim_size / height, 1.0)
    if width * height * scale_factor * scale_factor > max_pixels:
        scale_factor = min(scale_factor, (max_pixels / (width * height)) ** 0.5)
    return int(width * scale_factor), int(height * scale_factor)


def screen_geometry(max_dim_size: int = MAX_GROUNDING_DIM) -> ScreenGeometry:
    log_w, log_h = pyautogui.size()
    if log_w <= 0 or log_h <= 0:
        log_w, log_h = SCREEN_WIDTH, SCREEN_HEIGHT
    ground_w, ground_h = scale_screen_dimensions(log_w, log_h, max_dim_size)
    return ScreenGeometry(log_w, log_h, ground_w, ground_h)


def capture_observation(
    geom: ScreenGeometry | None = None,
    *,
    max_dim_size: int = MAX_GROUNDING_DIM,
) -> tuple[bytes, ScreenGeometry]:
    """
    Capture screen the FaceTimeOS way:
      1. logical size = pyautogui.size() (click coordinate space)
      2. resize screenshot to scaled logical dims before sending to the model
    """
    if geom is None:
        geom = screen_geometry(max_dim_size)

    img = pyautogui.screenshot()
    if img.size != (geom.ground_w, geom.ground_h):
        img = img.resize((geom.ground_w, geom.ground_h), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), geom


def normalize_agent_code(
    code: str, geom: ScreenGeometry,
) -> str:
    """
    Fallback scale when predict() returns coords in grounding-image space
    instead of logical pyautogui space.
    """
    if geom.ground_w <= 0 or geom.ground_h <= 0:
        return code
    if geom.ground_w == geom.logical_w and geom.ground_h == geom.logical_h:
        return code

    sx = geom.logical_w / geom.ground_w
    sy = geom.logical_h / geom.ground_h

    def _repl(m: re.Match) -> str:
        fn = m.group(1)
        x = int(int(m.group(2)) * sx)
        y = int(int(m.group(3)) * sy)
        return f"{fn}({x}, {y}{m.group(4)})"

    scaled = _CLICK_RE.sub(_repl, code)
    if scaled != code:
        print(
            f"[agent_s] scaled coords {geom.ground_w}×{geom.ground_h} "
            f"→ logical {geom.logical_w}×{geom.logical_h}"
        )
    return scaled


def grounding_target(instruction: str) -> str:
    """Extract the element description for generate_coords from a step instruction."""
    m = re.search(r"Target:\s*(.+)", instruction, re.S)
    if m:
        return m.group(1).strip()
    m = re.search(r"Step \d+:\s*(.+)", instruction, re.S)
    if m:
        return m.group(1).strip()
    return instruction.strip()


def ground_pointer_code(
    grounding_agent,
    target: str,
    obs: dict,
    *,
    action: str = "click",
) -> str:
    """
    FaceTimeOS path: dedicated grounding LLM call → resize_coordinates → pyautogui.
    Coordinates are emitted in logical pyautogui space.
    """
    grounding_agent.assign_screenshot(obs)
    coords = grounding_agent.generate_coords(target, obs)
    x, y = grounding_agent.resize_coordinates(coords)
    if action == "double_click":
        return f"import pyautogui; pyautogui.doubleClick({x}, {y})"
    if action == "move":
        return f"import pyautogui; pyautogui.moveTo({x}, {y}, duration=0.4)"
    return f"import pyautogui; pyautogui.click({x}, {y})"


def enrich_instruction(
    action: str,
    instruction: str,
    *,
    type_text: Optional[str] = None,
) -> str:
    """Add grounding hints for the full AgentS3 predict() fallback path."""
    action = (action or "").lower()

    if action in ("click", "double_click", "move"):
        return (
            "Use the attached screenshot. Perform ONE action only — no extra steps.\n"
            "Click the CENTER of the target element described below.\n"
            "Match by visible label text, icon, color, and position on screen.\n"
            "If multiple similar elements exist, pick the one that best fits the description.\n\n"
            f"Target: {grounding_target(instruction)}"
        )

    if action == "type":
        text_hint = f"\nText to type: {type_text}" if type_text else ""
        return (
            "Use the attached screenshot. Click the correct input field first if it is not "
            f"focused, then type the text.{text_hint}\n\n"
            f"Task: {instruction}"
        )

    return instruction
