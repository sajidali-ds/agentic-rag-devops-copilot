import json
import time

import fakeredis
import pytest

from app.cache import answer_cache as ac
from app.cache import conversation
from app.cache.answer_cache import AnswerCache, bump_corpus_version, is_cacheable_question, normalize, numbers_match
from app.cache.retrieval_cache import cached_retrieve
from app.cache.store import MemoryStore, RedisStore, get_store, set_store
from app.config import settings
from app.observability.stats import stats

PAYLOAD = {"answer": "A", "citations": ["x.txt"], "thought_process": []}


def embedder(text: str):
    """Toy embedding: bag of characters, so similar strings are close, different ones far."""
    v = [0.0] * 26
    for ch in text.lower():
        if "a" <= ch <= "z":
            v[ord(ch) - 97] += 1
    return v


@pytest.fixture(params=["memory", "redis"])
def store(request, monkeypatch):
    if request.param == "memory":
        s = MemoryStore()
    else:
        monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: fakeredis.FakeStrictRedis(decode_responses=True))
        s = RedisStore("redis://fake")
    set_store(s)
    return s


# ── store contract (same behaviour for memory + redis) ────────────────────────
def test_store_kv_list_hash(store):
    store.set("k", "v")
    assert store.get("k") == "v" and store.incr("n") == 1 and store.incr("n") == 2
    for i in range(5):
        store.rpush("l", str(i))
    store.ltrim_last("l", 3)
    assert store.lrange_all("l") == ["2", "3", "4"]
    store.lpush("q", "a")
    store.lpush("q", "b")
    assert store.rpop("q") == "a"
    store.hset("h", "f", "1")
    store.hset("h", "g", "2")
    assert store.hget("h", "f") == "1" and store.hlen("h") == 2 and set(store.hgetall("h")) == {"f", "g"}
    store.hdel("h", "f")
    assert store.hlen("h") == 1
    store.delete("k")
    assert store.get("k") is None


def test_memory_store_ttl():
    s = MemoryStore()
    s.set("k", "v", ex=0.05)
    assert s.get("k") == "v"
    time.sleep(0.08)
    assert s.get("k") is None


def test_get_store_falls_back_to_memory_when_redis_down(monkeypatch):
    set_store(None)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
    assert get_store().kind == "memory"


# ── answer cache ──────────────────────────────────────────────────────────────
def test_normalize_and_cacheability():
    assert normalize("  Why is   my POD failing? ") == "why is my pod failing"
    assert is_cacheable_question("short question")
    assert not is_cacheable_question("line1\nline2\nline3")
    assert not is_cacheable_question("```code```")
    assert not is_cacheable_question("x" * 501)
    assert numbers_match("port 6443", "PORT 6443?") and not numbers_match("port 6443", "port 2379")


def test_exact_hit_miss_and_stats(store):
    c = AnswerCache(embedder=embedder)
    assert c.lookup("why is my pod oomkilled") is None
    c.save("why is my pod oomkilled", PAYLOAD)
    kind, p = c.lookup("Why is my pod OOMKilled?")
    assert kind == "exact" and p == PAYLOAD
    snap = stats.snapshot()["cache"]
    assert snap["exact_hits"] == 1 and snap["misses"] == 1 and snap["hit_rate"] == 0.5


def test_semantic_hit_for_near_duplicate_but_not_for_different_question(store):
    c = AnswerCache(embedder=embedder)
    c.save("why is my pod stuck in pending", PAYLOAD)
    kind, _ = c.lookup("why is my pod stuck in pending state")
    assert kind == "semantic"
    assert c.lookup("how do I rotate kubeadm certificates") is None


def test_semantic_cache_requires_matching_numbers(store):
    c = AnswerCache(embedder=embedder)
    c.save("what listens on port 6443", PAYLOAD)
    assert c.lookup("what listens on port 2379") is None


def test_semantic_cache_can_be_disabled(store, monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", False)
    c = AnswerCache(embedder=embedder)
    c.save("why is my pod stuck in pending", PAYLOAD)
    assert c.lookup("why is my pod stuck in pending state") is None


def test_semantic_cache_evicts_oldest(store, monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_MAX", 2)
    c = AnswerCache(embedder=embedder)
    for q in ["alpha question one", "bravo question two", "charlie question three"]:
        c.save(q, PAYLOAD)
    assert store.hlen(f"semv:{ac.namespace(store)}") == 2


def test_corpus_version_bump_invalidates_everything(store):
    c = AnswerCache(embedder=embedder)
    c.save("why is my pod oomkilled", PAYLOAD)
    assert c.lookup("why is my pod oomkilled")
    bump_corpus_version(store)
    assert c.lookup("why is my pod oomkilled") is None


def test_prompt_version_change_invalidates(store, monkeypatch):
    c = AnswerCache(embedder=embedder)
    c.save("why is my pod oomkilled", PAYLOAD)
    monkeypatch.setattr(settings, "PROMPT_VERSION", "v999")
    assert c.lookup("why is my pod oomkilled") is None


def test_cache_disabled_and_failures_are_swallowed(store, monkeypatch):
    c = AnswerCache(embedder=embedder)
    monkeypatch.setattr(settings, "CACHE_ENABLED", False)
    c.save("q one", PAYLOAD)
    assert c.lookup("q one") is None
    monkeypatch.setattr(settings, "CACHE_ENABLED", True)

    class Broken(MemoryStore):
        def get(self, key):
            raise ConnectionError("redis down")

    broken = AnswerCache(store=Broken(), embedder=embedder)
    assert broken.lookup("anything") is None  # must not raise
    broken.save("anything", PAYLOAD)  # must not raise


# ── retrieval cache ───────────────────────────────────────────────────────────
def test_retrieval_cache_hits_and_skips_empty(store):
    calls = []

    def fn(q):
        calls.append(q)
        return [{"content": "c", "source": "s"}]

    r1, c1 = cached_retrieve("Pending pods", fn)
    r2, c2 = cached_retrieve("pending   PODS", fn)
    assert (c1, c2) == (False, True) and r1 == r2 and len(calls) == 1

    empty_calls = []
    cached_retrieve("nothing", lambda q: empty_calls.append(q) or [])
    cached_retrieve("nothing", lambda q: empty_calls.append(q) or [])
    assert len(empty_calls) == 2  # empty results are never cached
    snap = stats.snapshot()["cache"]
    assert snap["retrieval_hits"] == 1


def test_retrieval_cache_invalidated_by_corpus_version(store):
    calls = []
    fn = lambda q: calls.append(q) or [{"content": "c", "source": "s"}]  # noqa: E731
    cached_retrieve("q", fn)
    bump_corpus_version(store)
    cached_retrieve("q", fn)
    assert len(calls) == 2


# ── conversation memory ───────────────────────────────────────────────────────
def test_conversation_roundtrip_trim_and_clear(store, monkeypatch):
    monkeypatch.setattr(settings, "CONVERSATION_MAX_MESSAGES", 4)
    conversation.append_messages("t", [{"role": "user", "content": "1"}, {"role": "assistant", "content": "2"}])
    conversation.append_messages("t", [{"role": "user", "content": "3"}, {"role": "assistant", "content": "4"}, {"role": "user", "content": "5"}])
    assert [m["content"] for m in conversation.load_history("t")] == ["2", "3", "4", "5"]
    assert conversation.load_history("other") == []
    conversation.clear_history("t")
    assert conversation.load_history("t") == []


def test_conversation_survives_store_failure(monkeypatch):
    class Broken(MemoryStore):
        def lrange_all(self, key):
            raise ConnectionError("down")

        def rpush(self, key, value):
            raise ConnectionError("down")

    set_store(Broken())
    assert conversation.load_history("t") == []
    conversation.append_messages("t", [{"role": "user", "content": "x"}])  # must not raise
    json.dumps({})  # keep import used
