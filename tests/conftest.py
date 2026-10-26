import os
import sys
import types

import pytest

# Keep tests hermetic: no telemetry, no real services.
os.environ.setdefault("LOGFIRE_SEND_TO_LOGFIRE", "false")
os.environ.setdefault("PORTKEY_API_KEY", "test-key")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "0")
os.environ.pop("REDIS_URL", None)
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch, tmp_path):
    """Every test gets an empty in-memory store, zeroed stats and a private feedback log."""
    from app.cache.store import MemoryStore, set_store
    from app.config import settings
    from app.observability.stats import stats

    set_store(MemoryStore())
    stats.reset()
    monkeypatch.setattr(settings, "FEEDBACK_LOG_PATH", str(tmp_path / "feedback.jsonl"))
    yield
    set_store(None)


def install_fake_graph(monkeypatch, agent):
    """Stub the LangGraph module so app.main can be imported with light deps only."""
    graph = types.ModuleType("app.agents.graph")
    graph.rag_agent = agent
    graph.RECURSION_LIMIT = 60
    monkeypatch.setitem(sys.modules, "app.agents.graph", graph)
    monkeypatch.delitem(sys.modules, "app.main", raising=False)
