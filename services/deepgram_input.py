"""
Deepgram STT module — pluggable speech-to-text for the Shepherd backend.

Two layers, cleanly split:

  1. Engine — transcribe_bytes() / transcribe_file(): turn ANY audio that is
     already present in the backend (an upload, a saved file, a buffer) into
     text. No microphone or pyaudio required. Accepts optional api_key/model/
     language overrides so callers (e.g. the /api/deepgram test endpoint) can
     supply their own key at request time.

  2. Mic source — listen_and_transcribe() / listen_for_stop_command(): capture
     the local microphone, then hand the audio to the engine. Desktop-only
     (needs pyaudio + OS mic permission); pyaudio is imported lazily so the
     engine keeps working on a headless server.

Called ONLY before an Intent is built — never inside or between routine steps.
macOS mic capture: requires System Settings > Privacy & Security > Microphone.
VERIFY: uses deepgram-sdk v3. Check developers.deepgram.com before modifying.
"""
import io
import threading
import wave
from typing import Optional

from config import settings

_stop_listener = threading.Event()


# ── Engine: transcribe audio that already exists (no microphone) ─────────────

def transcribe_bytes(
    audio: bytes,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    language: Optional[str] = None,
    smart_format: bool = True,
) -> str:
    """
    Transcribe in-memory audio bytes via Deepgram → transcript string.
    Works for any audio Deepgram supports (wav, mp3, m4a, flac, …); the
    container/encoding is auto-detected from the buffer.

    api_key/model/language fall back to config settings when not supplied,
    letting a caller override per request (e.g. the test endpoint).
    Raises on failure so callers can fall back to typed input.
    """
    key = api_key or settings.deepgram_api_key
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    if not audio:
        raise RuntimeError("empty audio buffer")

    from deepgram import DeepgramClient, PrerecordedOptions, FileSource
    client = DeepgramClient(api_key=key)
    payload: FileSource = {"buffer": audio}
    opts = PrerecordedOptions(
        model=model or settings.deepgram_model,
        language=language or settings.deepgram_language,
        smart_format=smart_format,
    )
    resp = client.listen.prerecorded.v("1").transcribe_file(payload, opts)
    transcript = resp.results.channels[0].alternatives[0].transcript
    print(f"[deepgram] Transcript: {transcript!r}")
    return transcript


def transcribe_file(path: str, **kwargs) -> str:
    """Transcribe an audio file already on disk. kwargs forwarded to transcribe_bytes."""
    with open(path, "rb") as f:
        return transcribe_bytes(f.read(), **kwargs)


# ── Mic source: capture the local microphone, then transcribe ────────────────

def listen_and_transcribe(duration_seconds: float = 5.0, *, api_key: Optional[str] = None) -> str:
    """
    Record mic for duration_seconds → Deepgram STT → transcript string.
    Thin wrapper over the engine; raises on failure so caller can fall back.
    """
    audio = _record_mic(duration_seconds)
    return transcribe_bytes(audio, api_key=api_key)


def listen_for_stop_command(halt_callback, poll_seconds: float = 2.0) -> threading.Thread:
    """
    Background daemon: continuously listens for spoken 'stop'.
    Calls halt_callback() once on detection, then exits.
    Start this before engine.execute(); it self-terminates on halt.
    Call stop_listener() after execution completes to clean up.
    """
    _stop_listener.clear()

    def _loop():
        while not _stop_listener.is_set():
            try:
                t = listen_and_transcribe(duration_seconds=poll_seconds)
                if "stop" in t.lower():
                    print("[deepgram] 'stop' detected — requesting halt")
                    halt_callback()
                    return
            except Exception:
                pass

    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    return th


def stop_listener() -> None:
    """Signal the stop-command listener to exit cleanly after execution completes."""
    _stop_listener.set()


def _record_mic(duration: float) -> bytes:
    """Record from default mic. Returns WAV bytes."""
    try:
        import pyaudio
        CHUNK, FMT, CH, RATE = 1024, pyaudio.paInt16, 1, 16000
        p = pyaudio.PyAudio()
        s = p.open(format=FMT, channels=CH, rate=RATE, input=True, frames_per_buffer=CHUNK)
        frames = [s.read(CHUNK) for _ in range(int(RATE / CHUNK * duration))]
        s.stop_stream(); s.close(); p.terminate()

        buf = io.BytesIO()
        wf = wave.open(buf, 'wb')
        wf.setnchannels(CH)
        wf.setsampwidth(p.get_sample_size(FMT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()
        return buf.getvalue()
    except ImportError:
        raise RuntimeError("pyaudio not installed — run: uv sync --extra voice")
