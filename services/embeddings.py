"""
Shared local embedding model — one BAAI/bge-small-en-v1.5 instance, reused by
the semantic intent router and the semantic cache so the model is loaded once.

Local + free (fastembed, no API key). 384-dim float vectors, packed FP32 the way
Redis 8 vectorsets (VADD/VSIM) expect.
"""
import struct
import threading
from typing import Optional

EMBEDDING_DIM = 384
_MODEL_NAME = "BAAI/bge-small-en-v1.5"

_model = None
_lock = threading.Lock()


def get_model():
    """Lazily load (once, thread-safe) the shared embedding model."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding
                _model = TextEmbedding(_MODEL_NAME)
    return _model


def embed_floats(text: str) -> list[float]:
    return list(get_model().embed([text]))[0].tolist()


def embed_bytes(text: str) -> bytes:
    """FP32-packed embedding, the wire format for VADD/VSIM."""
    return struct.pack(f"{EMBEDDING_DIM}f", *embed_floats(text))


def available() -> bool:
    try:
        get_model()
        return True
    except Exception:
        return False
