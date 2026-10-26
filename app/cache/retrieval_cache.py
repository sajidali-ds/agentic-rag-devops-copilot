import hashlib
import json
from typing import Callable

import logfire

from app.cache.answer_cache import corpus_version
from app.cache.store import get_store
from app.config import settings
from app.observability.stats import stats


def _key(query: str) -> str:
    q = " ".join(query.lower().split())
    sig = f"{settings.RETRIEVE_TOP_N}:{settings.RERANK_TOP_K}:{settings.EMBEDDING_PROVIDER}:{q}"
    return f"ret:{corpus_version()}:{hashlib.sha256(sig.encode()).hexdigest()}"


def cached_retrieve(query: str, retrieve_fn: Callable[[str], list[dict]]) -> tuple[list[dict], bool]:
    """Return (results, from_cache). Empty results are never cached (could be a transient failure)."""
    if settings.CACHE_ENABLED:
        try:
            raw = get_store().get(_key(query))
            if raw:
                stats.incr("cache.retrieval_hit")
                return json.loads(raw), True
        except Exception as e:  # noqa: BLE001
            logfire.warning(f"Retrieval-cache read failed (ignored): {e}")
        stats.incr("cache.retrieval_miss")

    results = retrieve_fn(query)

    if settings.CACHE_ENABLED and results:
        try:
            get_store().set(_key(query), json.dumps(results), ex=settings.RETRIEVAL_CACHE_TTL)
        except Exception as e:  # noqa: BLE001
            logfire.warning(f"Retrieval-cache write failed (ignored): {e}")
    return results, False
