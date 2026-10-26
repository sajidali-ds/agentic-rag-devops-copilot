import importlib
import json

from fastapi.testclient import TestClient

from tests.conftest import install_fake_graph


class FakeAgent:
    def __init__(self, fail=False, mode="troubleshoot", grounded=True, answer="42"):
        self.fail, self.mode, self.grounded, self.answer = fail, mode, grounded, answer
        self.calls = 0
        self.last_state = None

    def invoke(self, state, config=None):
        self.calls += 1
        self.last_state = state
        if self.fail:
            raise RuntimeError("graph exploded")
        return {
            "final_answer": self.answer, "plan": ["Intent: Technical"], "status": "ok", "mode": self.mode,
            "documents": ["[Source: a.pdf]\nchunk"], "citations": ["a.pdf"], "tools_used": ["parse_logs"],
            "command_risks": [], "steps": 2, "grounded": self.grounded,
        }


def make_client(monkeypatch, agent, blocked=None):
    install_fake_graph(monkeypatch, agent)
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "initialize_rails", lambda: None)
    from app.guardrails.rails import GuardResult

    monkeypatch.setattr(main, "guard", lambda q: blocked or GuardResult(blocked=False))
    # cache tests must not need an embedding model
    monkeypatch.setattr(main.answer_cache, "_embed", lambda text: [float(len(text)), 1.0, 0.0])
    return main, TestClient(main.app)


def test_health_reports_store_kind(monkeypatch):
    _, c = make_client(monkeypatch, FakeAgent())
    body = c.get("/health").json()
    assert body["status"] == "ok" and body["cache_store"] == "memory"


def test_query_success_shape(monkeypatch):
    _, c = make_client(monkeypatch, FakeAgent())
    r = c.post("/query", json={"q": "what is sriov"})
    assert r.status_code == 200
    b = r.json()
    assert b["answer"] == "42" and b["citations"] == ["a.pdf"] and b["tools_used"] == ["parse_logs"]
    assert b["cache"] == "miss" and b["thread_id"] and b["request_id"] and b["latency_ms"] >= 0 and b["steps"] == 2


def test_second_identical_question_is_served_from_cache(monkeypatch):
    agent = FakeAgent()
    _, c = make_client(monkeypatch, agent)
    c.post("/query", json={"q": "Why is my pod OOMKilled?"})
    r = c.post("/query", json={"q": "why is my pod oomkilled?  "})  # normalisation: case/whitespace
    assert r.json()["cache"] == "exact" and agent.calls == 1
    assert r.json()["answer"] == "42"
    assert r.json()["thought_process"][0] == "Cache: exact hit"


def test_cache_is_skipped_when_conversation_has_history(monkeypatch):
    agent = FakeAgent()
    _, c = make_client(monkeypatch, agent)
    c.post("/query", json={"q": "why OOMKilled", "thread_id": "t1"})
    c.post("/query", json={"q": "why OOMKilled", "thread_id": "t1"})  # same thread -> has history
    assert agent.calls == 2


def test_history_is_loaded_into_agent_state(monkeypatch):
    agent = FakeAgent()
    _, c = make_client(monkeypatch, agent)
    c.post("/query", json={"q": "first question", "thread_id": "t9"})
    c.post("/query", json={"q": "and the second?", "thread_id": "t9"})
    msgs = agent.last_state["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"] and msgs[-1]["content"] == "and the second?"


def test_ungrounded_and_conversational_answers_are_not_cached(monkeypatch):
    for kwargs in ({"grounded": False}, {"mode": "conversational"}):
        agent = FakeAgent(**kwargs)
        _, c = make_client(monkeypatch, agent)
        c.post("/query", json={"q": "some question"})
        c.post("/query", json={"q": "some question"})
        assert agent.calls == 2


def test_blocked_request_skips_pipeline(monkeypatch):
    from app.guardrails.rails import GuardResult

    agent = FakeAgent(fail=True)
    _, c = make_client(monkeypatch, agent, blocked=GuardResult(True, "OFF_TOPIC", "no"))
    r = c.post("/query", json={"q": "joke"})
    assert r.status_code == 200 and r.json()["thought_process"][0] == "Intent: Guardrails Fired" and agent.calls == 0


def test_internal_error_is_a_real_500(monkeypatch):
    _, c = make_client(monkeypatch, FakeAgent(fail=True))
    r = c.post("/query", json={"q": "x"})
    assert r.status_code == 500 and r.json()["status"] == "error"
    assert c.get("/stats").json()["errors"] == 1


def test_validation_rejects_empty_and_oversized(monkeypatch):
    main, c = make_client(monkeypatch, FakeAgent())
    assert c.post("/query", json={"q": ""}).status_code == 422
    assert c.post("/query", json={"q": "x" * (main.settings.MAX_QUERY_CHARS + 1)}).status_code == 422


def test_api_key_enforced_when_configured(monkeypatch):
    main, c = make_client(monkeypatch, FakeAgent())
    monkeypatch.setattr(main.settings, "API_KEY", "secret")
    assert c.post("/query", json={"q": "x"}).status_code == 401
    assert c.get("/stats").status_code == 401
    assert c.post("/query", json={"q": "x"}, headers={"X-API-Key": "secret"}).status_code == 200


def test_rate_limit(monkeypatch):
    main, c = make_client(monkeypatch, FakeAgent())
    main._hits.clear()
    monkeypatch.setattr(main.settings, "RATE_LIMIT_PER_MINUTE", 2)
    codes = [c.post("/query", json={"q": f"question {i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_stats_endpoint_reports_cache_and_latency(monkeypatch):
    _, c = make_client(monkeypatch, FakeAgent())
    c.post("/query", json={"q": "alpha question"})
    c.post("/query", json={"q": "alpha question"})
    s = c.get("/stats").json()
    assert s["requests"] == 2 and s["cache"]["exact_hits"] == 1 and s["cache"]["hit_rate"] == 0.5
    assert s["latency_ms"]["samples"] == 2


def test_feedback_is_logged_and_counted(monkeypatch):
    main, c = make_client(monkeypatch, FakeAgent())
    r = c.post("/feedback", json={"request_id": "abc", "rating": "down", "comment": "wrong fix"})
    assert r.json()["recorded"] is True
    line = json.loads(open(main.settings.FEEDBACK_LOG_PATH).read().splitlines()[0])
    assert line["rating"] == "down" and line["comment"] == "wrong fix"
    assert c.post("/feedback", json={"request_id": "abc", "rating": "meh"}).status_code == 422


def test_conversation_can_be_cleared(monkeypatch):
    agent = FakeAgent()
    _, c = make_client(monkeypatch, agent)
    c.post("/query", json={"q": "one", "thread_id": "z"})
    assert c.delete("/conversations/z").json() == {"cleared": "z"}
    c.post("/query", json={"q": "two", "thread_id": "z"})
    assert len(agent.last_state["messages"]) == 1
