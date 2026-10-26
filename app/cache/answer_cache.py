import base64
import hashlib
import json
import re
import uuid
from typing import Callable

import logfire
import numpy as np

from app.cache.store import get_store
from app.config import settings
from app.observability.stats import stats

_NUM = re.compile(r"\d+(?:\.\d+)?")


def normalize(question: str) -> str:
    return " ".join(question.lower().split()).strip(" ?!.")


def is_cacheable_question(question: str) -> bool:
    """Short single-line questions only: pasted logs / code are too specific to match semantically."""
    return len(question) <= 500 and question.count("\n") <= 1 and "```" not in question


def numbers_match(a: str, b: str) -> bool:
    return sorted(_NUM.findall(a)) == sorted(_NUM.findall(b))


def corpus_version(store=None) -> str:
    return (store or get_store()).get("corpus:version") or "0"


def bump_corpus_version(store=None) -> int:
    """Call after (re)ingesting documents to invalidate every cached answer/retrieval."""
    v = (store or get_store()).incr("corpus:version")
    logfire.info(f"Corpus version bumped to {v}; caches invalidated.")
    return v


def namespace(store=None) -> str:
    return f"{settings.PROMPT_VERSION}:{corpus_version(store)}:{settings.LLM_MODEL}"


def _pack(vec: np.ndarray) -> str:
    return base64.b64encode(vec.astype(np.float32).tobytes()).decode("ascii")


def _unpack(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.float32)


def _unit(vec) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n else v


def _default_embedder(text: str):
    from app.services.retrieval.embeddings import embed_query

    return embed_query(text)


class AnswerCache:
    def __init__(self, store=None, embedder: Callable[[str], list[float]] | None = None) -> None:
        self._store = store
        self._embed = embedder or _default_embedder

    @property
    def store(self):
        return self._store or get_store()

    # ── keys ───────────────────────────────────────────────────────────────────
    def _exact_key(self, question: str, ns: str) -> str:
        return f"ans:{ns}:{hashlib.sha256(normalize(question).encode()).hexdigest()}"

    # ── lookup ─────────────────────────────────────────────────────────────────
    def lookup(self, question: str) -> tuple[str, dict] | None:
        """Return ("exact"|"semantic", payload) or None. Never raises (cache is best-effort)."""
        if not settings.CACHE_ENABLED or not is_cacheable_question(question):
            return None
        try:
            store, ns = self.store, namespace(self.store)

            raw = store.get(self._exact_key(question, ns))
            if raw:
                stats.incr("cache.exact_hit")
                return "exact", json.loads(raw)

            if settings.SEMANTIC_CACHE_ENABLED:
                hit = self._semantic_lookup(store, ns, question)
                if hit:
                    stats.incr("cache.semantic_hit")
                    return "semantic", hit
            stats.incr("cache.miss")
        except Exception as e:  # noqa: BLE001
            logfire.warning(f"Answer-cache lookup failed (ignored): {e}")
        return None

    def _semantic_lookup(self, store, ns: str, question: str) -> dict | None:
        vectors = store.hgetall(f"semv:{ns}")
        if not vectors:
            return None
        q = _unit(self._embed(question))
        ids = list(vectors)
        matrix = np.stack([_unpack(vectors[i]) for i in ids])
        sims = matrix @ q
        for idx in np.argsort(-sims):
            if sims[idx] < settings.SEMANTIC_THRESHOLD:
                return None
            entry = store.hget(f"semp:{ns}", ids[idx])
            if not entry:
                continue
            entry = json.loads(entry)
            if numbers_match(question, entry["q"]):
                logfire.info(f"Semantic cache hit (sim={sims[idx]:.3f}) for: {question[:80]}")
                return entry["p"]
        return None

    # ── save ───────────────────────────────────────────────────────────────────
    def save(self, question: str, payload: dict) -> None:
        if not settings.CACHE_ENABLED or not is_cacheable_question(question):
            return
        try:
            store, ns = self.store, namespace(self.store)
            store.set(self._exact_key(question, ns), json.dumps(payload), ex=settings.EXACT_CACHE_TTL)

            if settings.SEMANTIC_CACHE_ENABLED:
                eid = uuid.uuid4().hex
                store.hset(f"semv:{ns}", eid, _pack(_unit(self._embed(question))))
                store.hset(f"semp:{ns}", eid, json.dumps({"q": question, "p": payload}))
                store.lpush(f"semo:{ns}", eid)
                while store.hlen(f"semv:{ns}") > settings.SEMANTIC_CACHE_MAX:
                    old = store.rpop(f"semo:{ns}")
                    if not old:
                        break
                    store.hdel(f"semv:{ns}", old)
                    store.hdel(f"semp:{ns}", old)
                for k in (f"semv:{ns}", f"semp:{ns}", f"semo:{ns}"):
                    store.expire(k, settings.EXACT_CACHE_TTL)
        except Exception as e:  # noqa: BLE001
            logfire.warning(f"Answer-cache save failed (ignored): {e}")
