"""Tests for services/webrtc_sender.py — verifies the WebRTC P2P sender."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_aiortc_available():
    """Smoke-test that aiortc imported successfully."""
    from services.webrtc_sender import AIORTC_AVAILABLE
    # If aiortc is installed, this should be True
    assert isinstance(AIORTC_AVAILABLE, bool)


def test_screen_capture_track_creation():
    """ScreenCaptureTrack can be instantiated."""
    from services.webrtc_sender import AIORTC_AVAILABLE, ScreenCaptureTrack
    if not AIORTC_AVAILABLE:
        pytest.skip("aiortc not installed")
    track = ScreenCaptureTrack(fps=1.0, width=640)
    assert track.kind == "video"
    assert track._fps == 1.0
    assert track._width == 640


def test_webrtc_sender_available_flag():
    """WebRTCSender.available reflects aiortc import status."""
    from services.webrtc_sender import AIORTC_AVAILABLE, WebRTCSender
    ws = MagicMock()
    sender = WebRTCSender(ws, fps=1, width=640, quality=45)
    assert sender.available == AIORTC_AVAILABLE


def test_webrtc_sender_start_sends_offer():
    """When started, the sender creates an offer and sends it through the WS."""
    from services.webrtc_sender import AIORTC_AVAILABLE, WebRTCSender
    if not AIORTC_AVAILABLE:
        pytest.skip("aiortc not installed")

    async def _run():
        ws = AsyncMock()
        ws.send = AsyncMock()

        sender = WebRTCSender(ws, fps=1, width=640, quality=45)

        with patch("services.webrtc_sender._capture_pil_image") as mock_capture:
            from PIL import Image
            mock_capture.return_value = Image.new("RGB", (640, 480), (0, 0, 0))
            result = await sender.start()

        assert result is True
        assert ws.send.called
        sent_msg = json.loads(ws.send.call_args[0][0])
        assert sent_msg["type"] == "webrtc.offer"
        assert "sdp" in sent_msg["data"]
        assert sent_msg["data"]["type"] == "offer"

        sender.close()

    asyncio.run(_run())


def test_webrtc_sender_handle_answer():
    """Sender can process an SDP answer."""
    from services.webrtc_sender import AIORTC_AVAILABLE, WebRTCSender
    if not AIORTC_AVAILABLE:
        pytest.skip("aiortc not installed")

    async def _run():
        ws = AsyncMock()
        ws.send = AsyncMock()

        sender = WebRTCSender(ws, fps=1, width=640, quality=45)

        with patch("services.webrtc_sender._capture_pil_image") as mock_capture:
            from PIL import Image
            mock_capture.return_value = Image.new("RGB", (640, 480), (0, 0, 0))
            await sender.start()

        sent_msg = json.loads(ws.send.call_args[0][0])
        offer_sdp = sent_msg["data"]["sdp"]

        answer_data = {"type": "answer", "sdp": offer_sdp.replace("a=setup:actpass", "a=setup:active")}
        await sender.handle_answer(answer_data)

        sender.close()

    asyncio.run(_run())


def test_webrtc_sender_without_aiortc():
    """If aiortc is not available, sender reports unavailable gracefully."""
    import services.webrtc_sender as mod
    original = mod.AIORTC_AVAILABLE
    try:
        mod.AIORTC_AVAILABLE = False
        ws = MagicMock()
        sender = mod.WebRTCSender(ws, fps=1, width=640, quality=45)
        assert sender.available is False
    finally:
        mod.AIORTC_AVAILABLE = original
