import sys
import types

from app.config import settings
from app.observability import langfuse_setup as lf
from app.observability.stats import Stats


def test_langfuse_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)
    assert lf.make_handler() is None
    assert lf.run_config("t", None, 60) == {"recursion_limit": 60}
    assert lf.score("trace", "x", 1) is False
    lf.flush()  # must not raise


def test_langfuse_enabled_builds_callbacks_session_and_scores(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", True)
    sent = []

    class FakeHandler:
        last_trace_id = "trace-123"

    class FakeClient:
        def create_score(self, **kw):
            sent.append(kw)

        def flush(self):
            sent.append("flushed")

    lc = types.ModuleType("langfuse.langchain")
    lc.CallbackHandler = FakeHandler
    root = types.ModuleType("langfuse")
    root.get_client = lambda: FakeClient()
    monkeypatch.setitem(sys.modules, "langfuse", root)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", lc)

    h = lf.make_handler()
    cfg = lf.run_config("thread-1", h, 60, tags=["x"])
    assert cfg["callbacks"] == [h] and cfg["metadata"]["langfuse_session_id"] == "thread-1" and "x" in cfg["metadata"]["langfuse_tags"]
    assert lf.trace_id_of(h) == "trace-123"
    assert lf.score("trace-123", "grounded", 1.0, "ok") is True
    assert sent[0]["name"] == "grounded" and sent[0]["trace_id"] == "trace-123" and sent[0]["value"] == 1.0
    lf.flush()
    assert sent[-1] == "flushed"


def test_langfuse_failures_never_propagate(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", True)
    root = types.ModuleType("langfuse")

    def boom():
        raise RuntimeError("langfuse down")

    root.get_client = boom
    monkeypatch.setitem(sys.modules, "langfuse", root)
    assert lf.score("t", "x", 1) is False
    lf.flush()


def test_stats_percentiles_and_hit_rate():
    s = Stats()
    for ms in range(1, 101):
        s.request(float(ms), steps=2)
    s.incr("cache.exact_hit", 3)
    s.incr("cache.miss", 1)
    s.llm_call("fast", 100.0, 10, 5)
    snap = s.snapshot()
    assert snap["latency_ms"]["p50"] in (50.0, 51.0) and snap["latency_ms"]["p95"] >= 95 and snap["latency_ms"]["p99"] >= 99
    assert snap["cache"]["hit_rate"] == 0.75 and snap["agent"]["avg_steps"] == 2.0
    assert snap["llm"]["tokens"]["prompt"] == 10 and snap["llm"]["calls"]["fast"] == 1
    s.reset()
    assert s.snapshot()["requests"] == 0
