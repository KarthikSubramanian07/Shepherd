"""
Deepgram STT voice input layer.
Called ONLY before Intent is built — never inside or between routine steps.
Output is a plain string that becomes Intent.raw_text.

macOS: requires System Settings > Privacy & Security > Microphone permission.
VERIFY: uses deepgram-sdk v3. Check developers.deepgram.com before modifying.
"""
import io
import threading
import wave
from config import DEEPGRAM_API_KEY

_stop_listener = threading.Event()


def listen_and_transcribe(duration_seconds: float = 5.0) -> str:
    """
    Record mic for duration_seconds → Deepgram STT → transcript string.
    Raises on failure so caller can fallback to typed input.
    """
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    audio = _record_mic(duration_seconds)
    return _transcribe(audio)


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


def _transcribe(audio_bytes: bytes) -> str:
    """POST audio to Deepgram. VERIFY API at developers.deepgram.com."""
    from deepgram import DeepgramClient, PrerecordedOptions, FileSource
    client  = DeepgramClient(api_key=DEEPGRAM_API_KEY)
    payload: FileSource = {"buffer": audio_bytes}
    opts    = PrerecordedOptions(model="nova-2", language="en-US", smart_format=True)
    resp    = client.listen.prerecorded.v("1").transcribe_file(payload, opts)
    transcript = resp.results.channels[0].alternatives[0].transcript
    print(f"[deepgram] Transcript: {transcript!r}")
    return transcript
