"""
VectorRouter — Redis 8 native vectorset for semantic intent → routine matching.

Uses VADD / VSIM commands (Redis 8 vectorset module, no RediSearch required).
Embeddings computed locally via BAAI/bge-small-en-v1.5 (fastembed, no API key).
Falls back silently to keyword matching when Redis is unavailable.
"""
import struct
from typing import Optional

EMBEDDING_DIM = 384
VSET_KEY = "shepherd:routines"
SIMILARITY_THRESHOLD = 0.40   # below this, defer to keyword router


class VectorRouter:
    def __init__(self, registry: dict) -> None:
        self._r = None
        self._model = None
        try:
            self._init(registry)
        except Exception as e:
            print(f"[vector_router] Unavailable (non-fatal): {e}")

    def _init(self, registry: dict) -> None:
        import redis as _redis
        from config import REDIS_URL

        r = _redis.from_url(REDIS_URL)
        r.ping()
        self._r = r

        from fastembed import TextEmbedding
        self._model = TextEmbedding("BAAI/bge-small-en-v1.5")

        # (Re-)index all routines into the vectorset
        r.delete(VSET_KEY)
        for routine_id, spec in registry.items():
            text = spec.get("description", "") + " " + " ".join(spec.get("keywords", []))
            vec = self._embed(text)
            r.execute_command("VADD", VSET_KEY, "FP32", vec, routine_id)

        print(f"[vector_router] Ready — {len(registry)} routines indexed in Redis vectorset")

    @property
    def available(self) -> bool:
        return self._r is not None and self._model is not None

    def resolve(self, intent_text: str) -> Optional[tuple[str, float]]:
        """
        Return (routine_id, similarity) for the nearest routine, or None if
        best match is below SIMILARITY_THRESHOLD.
        """
        if not self.available:
            return None
        try:
            vec = self._embed(intent_text)
            results = self._r.execute_command(
                "VSIM", VSET_KEY, "FP32", vec, "WITHSCORES", "COUNT", 1
            )
            if not results or len(results) < 2:
                return None
            routine_id = results[0].decode() if isinstance(results[0], bytes) else results[0]
            similarity = float(results[1])
            if similarity < SIMILARITY_THRESHOLD:
                return None
            return routine_id, round(similarity, 4)
        except Exception as e:
            print(f"[vector_router] search failed (non-fatal): {e}")
            return None

    def _embed(self, text: str) -> bytes:
        vec = list(self._model.embed([text]))[0]
        return struct.pack(f"{EMBEDDING_DIM}f", *vec)
