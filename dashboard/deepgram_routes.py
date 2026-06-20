"""
Deepgram test endpoints — a thin HTTP surface over the pluggable STT engine.

Mounted under /api/deepgram by dashboard/server.py. Lets you verify a key and
transcribe an arbitrary audio file without touching the microphone path. The
caller may supply their own Deepgram API key per request (form field or the
X-Deepgram-Key header); it falls back to the configured key otherwise.

The transcription call is synchronous/blocking, so it runs in a threadpool to
avoid stalling the dashboard event loop.
"""
from typing import Optional

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from config import settings

router = APIRouter(prefix="/api/deepgram", tags=["deepgram"])


@router.get("/status")
async def deepgram_status() -> JSONResponse:
    """Report whether a key is configured (without leaking it) and the defaults."""
    return JSONResponse({
        "configured": bool(settings.deepgram_api_key),
        "model": settings.deepgram_model,
        "language": settings.deepgram_language,
    })


@router.post("/transcribe")
async def deepgram_transcribe(
    file: UploadFile = File(...),
    api_key: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    language: Optional[str] = Form(default=None),
    x_deepgram_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    """
    Transcribe an uploaded audio file via Deepgram and return the transcript.

    Test with curl:
      curl -X POST http://localhost:8765/api/deepgram/transcribe \\
        -F "file=@sample.wav" -F "api_key=YOUR_KEY"
    """
    from services.deepgram_input import transcribe_bytes

    key = api_key or x_deepgram_key or settings.deepgram_api_key
    if not key:
        return JSONResponse(
            {"error": "no Deepgram API key supplied (form 'api_key', header 'X-Deepgram-Key', or DEEPGRAM_API_KEY)"},
            status_code=400,
        )

    audio = await file.read()
    if not audio:
        return JSONResponse({"error": "empty audio file"}, status_code=400)

    try:
        transcript = await run_in_threadpool(
            transcribe_bytes,
            audio,
            api_key=key,
            model=model,
            language=language,
        )
        return JSONResponse({
            "transcript": transcript,
            "filename": file.filename,
            "bytes": len(audio),
            "model": model or settings.deepgram_model,
            "language": language or settings.deepgram_language,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
