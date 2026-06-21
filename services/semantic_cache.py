"""
Semantic cache — Redis vectorset as an LLM response cache keyed by MEANING.

A normal cache hits only on an exact key match. A semantic cache embeds the
prompt and hits when a *semantically near-identical* prompt was seen before, so
a repeated (or trivially reworded) request returns the stored answer instead of
paying for another model call. This is a Redis-beyond-caching pattern: the same
vectorset primitive (VADD/VSIM) that powers intent routing also powers this.

Used by engine.milestones.segment() to skip the milestone-segmentation LLM call
when a run's trace matches one already crystallized. Hit/miss counters are kept
in Redis so the dashboard can show the cache working live.

Safe by construction: callers pick the similarity threshold, and may store
structured guards in the cached value (e.g. step count) to reject a near-match
that isn't actually substitutable. Degrades to a no-op (always miss) when Redis
or the embedder is unavailable.
"""
import hashlib
import json
from typing import Any, Optional

from config import REDIS_URL
from services import embeddings


class SemanticCache:
    def __init__(self, namespace: str) -> None:
        self._ns = namespace
        self._vset = f"shepherd:semcache:{namespace}"
        self._r = None
        try:
            import redis as _redis
            r = _redis.from_url(REDIS_URL)
            r.ping()
            self._r = r
        except Exception as e:
            print(f"[semcache:{namespace}] Redis unavailable (non-fatal): {e}")

    @property
    def connected(self) -> bool:
        """Redis reachable — enough for read-only stats (no embedder needed)."""
        return self._r is not None

    @property
    def available(self) -> bool:
        return self._r is not None and embeddings.available()

    def _val_key(self, elem: str) -> str:
        return f"{self._vset}:val:{elem}"

    @staticmethod
    def _elem(key_text: str) -> str:
        return hashlib.sha1(key_text.encode("utf-8")).hexdigest()[:16]

    def get(self, key_text: str, min_sim: float = 0.95) -> Optional[tuple[Any, float]]:
        """Return (value, similarity) for the nearest cached entry above min_sim,
        else None. Increments hit/miss counters."""
        if not self.available:
            return None
        try:
            vec = embeddings.embed_bytes(key_text)
            res = self._r.execute_command(
                "VSIM", self._vset, "FP32", vec, "WITHSCORES", "COUNT", 1
            )
            if res and len(res) >= 2:
                elem = res[0].decode() if isinstance(res[0], bytes) else res[0]
                sim = float(res[1])
                if sim >= min_sim:
                    raw = self._r.get(self._val_key(elem))
                    if raw is not None:
                        self._r.incr(f"{self._vset}:hits")
                        return json.loads(raw), round(sim, 4)
            self._r.incr(f"{self._vset}:misses")
            return None
        except Exception as e:
            print(f"[semcache:{self._ns}] get failed (non-fatal): {e}")
            return None

    def put(self, key_text: str, value: Any) -> None:
        if not self.available:
            return
        try:
            elem = self._elem(key_text)
            vec = embeddings.embed_bytes(key_text)
            self._r.execute_command("VADD", self._vset, "FP32", vec, elem)
            self._r.set(self._val_key(elem), json.dumps(value))
        except Exception as e:
            print(f"[semcache:{self._ns}] put failed (non-fatal): {e}")

    def stats(self) -> dict:
        if self._r is None:
            return {"available": False}
        try:
            hits = int(self._r.get(f"{self._vset}:hits") or 0)
            misses = int(self._r.get(f"{self._vset}:misses") or 0)
            total = hits + misses
            try:
                entries = int(self._r.execute_command("VCARD", self._vset) or 0)
            except Exception:
                entries = 0
            return {
                "available": True,
                "namespace": self._ns,
                "entries": entries,
                "hits": hits,
                "misses": misses,
                "hit_rate": round(hits / total, 3) if total else 0.0,
            }
        except Exception as e:
            return {"available": False, "error": str(e)}
