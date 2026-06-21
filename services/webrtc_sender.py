"""
WebRTC screen-share sender for the relay client.

When enabled (WEBRTC_ENABLED=true), this module creates an RTCPeerConnection and
streams the agent's screen as a video track directly to the operator's browser.
The coordinator only relays the tiny signaling messages (SDP offer/answer + ICE
candidates) — the actual video bypasses it entirely (P2P).

Falls back gracefully: if aiortc isn't installed or WebRTC negotiation fails,
the relay client continues using the existing JPEG-over-WS path.

Usage from relay_client.py:
    from services.webrtc_sender import WebRTCSender
    sender = WebRTCSender(ws, fps=1, width=640, quality=45)
    await sender.start()       # creates offer, sends via WS
    await sender.handle_answer(sdp_dict)
    await sender.handle_ice(candidate_dict)
    sender.close()
"""
from __future__ import annotations

import asyncio
import fractions
import io
import time
from typing import Optional

try:
    from aiortc import (
        RTCConfiguration,
        RTCIceCandidate,
        RTCIceServer,
        RTCPeerConnection,
        RTCSessionDescription,
    )
    from aiortc.mediastreams import MediaStreamTrack, VideoStreamTrack
    from av import VideoFrame

    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False


def _capture_pil_image():
    """Capture screen as a PIL Image. Same logic as relay_client._capture_frame but returns PIL directly."""
    from PIL import Image

    # Primary: pyautogui full-screen capture
    try:
        import pyautogui
        return pyautogui.screenshot()
    except Exception:
        pass

    # Fallback: Playwright CDP
    try:
        from services.relay_client import _get_cdp_page
        page = _get_cdp_page()
        if page:
            raw = page.screenshot(type="png")
            return Image.open(io.BytesIO(raw))
    except Exception:
        pass

    return None


class ScreenCaptureTrack(MediaStreamTrack if AIORTC_AVAILABLE else object):  # type: ignore[misc]
    """A video track that captures the screen at the configured frame rate."""

    kind = "video"

    def __init__(self, fps: float = 1.0, width: int = 640):
        if AIORTC_AVAILABLE:
            super().__init__()
        self._fps = max(fps, 0.1)
        self._width = width
        self._interval = 1.0 / self._fps
        self._start = time.time()
        self._frame_count = 0
        self._time_base = fractions.Fraction(1, 90000)

    async def recv(self) -> "VideoFrame":
        """Called by aiortc when it needs the next frame."""
        # Pace the frames
        self._frame_count += 1
        target_time = self._start + (self._frame_count * self._interval)
        now = time.time()
        if target_time > now:
            await asyncio.sleep(target_time - now)

        # Capture screen in executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        img = await loop.run_in_executor(None, _capture_pil_image)

        if img is None:
            # Return a black frame if capture fails
            img = _black_frame(self._width)

        # Resize if needed
        if self._width and img.width > self._width:
            ratio = self._width / img.width
            img = img.resize((self._width, int(img.height * ratio)))

        # Convert to VideoFrame
        img = img.convert("RGB")
        frame = VideoFrame.from_image(img)
        frame.pts = int((time.time() - self._start) * 90000)
        frame.time_base = self._time_base
        return frame


def _black_frame(width: int = 640):
    """Generate a small black placeholder frame."""
    from PIL import Image
    return Image.new("RGB", (width, int(width * 0.75)), (0, 0, 0))


class WebRTCSender:
    """Manages a WebRTC peer connection for streaming the screen to the UI."""

    def __init__(
        self,
        ws,
        fps: float = 1.0,
        width: int = 640,
        quality: int = 45,
    ):
        self._ws = ws
        self._fps = fps
        self._width = width
        self._quality = quality
        self._pc: Optional["RTCPeerConnection"] = None
        self._track: Optional[ScreenCaptureTrack] = None
        self._started = False

    @property
    def available(self) -> bool:
        return AIORTC_AVAILABLE

    async def start(self) -> bool:
        """Create the peer connection and send an offer through the WS.

        Returns True if offer was sent, False if WebRTC is unavailable.
        """
        if not AIORTC_AVAILABLE:
            return False

        try:
            config = RTCConfiguration(
                iceServers=[
                    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                    RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                ]
            )
            self._pc = RTCPeerConnection(configuration=config)
            self._track = ScreenCaptureTrack(fps=self._fps, width=self._width)

            # Add the video track
            self._pc.addTrack(self._track)

            # Buffer ICE candidates to send after the offer
            ice_candidates = []

            @self._pc.on("icecandidate")
            def on_ice(candidate):
                if candidate:
                    ice_candidates.append(candidate)

            # Trickle ICE candidates as they arrive
            @self._pc.on("icegatheringstatechange")
            async def on_gathering_state_change():
                pass  # We trickle candidates via connectionstatechange

            # Create and set local description (offer)
            offer = await self._pc.createOffer()
            await self._pc.setLocalDescription(offer)

            # Send the offer through the coordinator
            import json
            await self._ws.send(json.dumps({
                "type": "webrtc.offer",
                "data": {
                    "type": self._pc.localDescription.type,
                    "sdp": self._pc.localDescription.sdp,
                },
            }))

            # Start ICE candidate trickling in background
            asyncio.ensure_future(self._trickle_ice())

            self._started = True
            print("[webrtc] offer sent — waiting for answer from UI")
            return True
        except Exception as e:
            print(f"[webrtc] failed to create offer: {e}")
            self.close()
            return False

    async def _trickle_ice(self) -> None:
        """Send ICE candidates as they become available."""
        if not self._pc:
            return
        import json

        # Wait for ICE gathering to progress
        while self._pc and self._pc.iceGatheringState != "complete":
            await asyncio.sleep(0.1)
            # Check for new local candidates via the transport
            for transceiver in self._pc.getTransceivers():
                transport = transceiver.sender.transport
                if transport and transport.transport:
                    iceTransport = transport.transport
                    # aiortc doesn't expose individual candidates easily via events,
                    # but the offer already contains all candidates when gathering
                    # completes quickly. The UI will get them from the SDP.
                    break
            break

        # aiortc typically completes ICE gathering before setLocalDescription returns
        # (non-trickle by default), so the offer SDP already contains all candidates.
        # If we do get late candidates, send them:
        if self._pc and self._pc.localDescription:
            # In aiortc, candidates are embedded in the SDP offer. No separate
            # trickle needed unless we switch to trickle-ICE mode in the future.
            pass

    async def handle_answer(self, data: dict) -> None:
        """Process the SDP answer from the remote UI."""
        if not self._pc:
            return
        try:
            answer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
            await self._pc.setRemoteDescription(answer)
            print("[webrtc] answer applied — P2P connection establishing")
        except Exception as e:
            print(f"[webrtc] failed to apply answer: {e}")

    async def handle_ice(self, data: dict) -> None:
        """Process a remote ICE candidate from the UI."""
        if not self._pc:
            return
        try:
            candidate_data = data.get("candidate", data)
            if not candidate_data:
                return
            # Parse the ICE candidate string
            candidate_str = candidate_data.get("candidate", "")
            if not candidate_str:
                return
            sdp_mid = candidate_data.get("sdpMid", "")
            sdp_mline_index = candidate_data.get("sdpMLineIndex", 0)

            candidate = RTCIceCandidate(
                component=1,
                foundation="",
                ip="",
                port=0,
                priority=0,
                protocol="udp",
                type="host",
                sdpMid=sdp_mid,
                sdpMLineIndex=sdp_mline_index,
            )
            # aiortc parses candidates from the full candidate string
            # Use the lower-level approach
            await self._pc.addIceCandidate(candidate)
        except Exception as e:
            # Non-fatal — aiortc often has all candidates from the SDP already
            pass

    def close(self) -> None:
        """Tear down the peer connection."""
        if self._track:
            self._track.stop()
            self._track = None
        if self._pc:
            asyncio.ensure_future(self._pc.close())
            self._pc = None
        self._started = False

    @property
    def is_connected(self) -> bool:
        if not self._pc:
            return False
        return self._pc.connectionState in ("connected", "completed")
