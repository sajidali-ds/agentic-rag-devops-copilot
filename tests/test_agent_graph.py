import json
from types import SimpleNamespace

import pytest

from app import llm as llm_mod
from app.agents.graph import rag_agent
from app.agents.nodes import search as search_mod
from app.agents.nodes.responder import NOT_FOUND
from app.config import settings
from app.observability.stats import stats

HIT = {"content": "OOMKilled means the container exceeded its memory limit (exit 137). Raise the limit.", "source": "oom.txt", "score": 0.9, "rerank_score": 0.9}
HIT2 = {"content": "Pending pods: compare requests with node allocatable.", "source": "pending.txt", "score": 0.8, "rerank_score": 0.8}


class FakeLLM:
    """Routes by prompt content. `decisions` / `verdicts` are consumed in order (last one repeats)."""

    def __init__(self, planner=None, decisions=None, verdicts=None, answers=None):
        self.planner = planner or {"mode": "troubleshoot", "query": "oomkilled memory limit"}
        self.decisions = list(decisions or [{"action": "final"}])
        self.verdicts = list(verdicts or [{"supported": True, "unsupported_claims": []}])
        self.answers = list(answers or ["## Diagnosis\nMemory limit exceeded (Source: oom.txt)."])
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    def _pop(self, lst):
        return lst.pop(0) if len(lst) > 1 else lst[0]

    def invoke(self, prompt):
        if "router of an IT/DevOps" in prompt:
            kind, text = "planner", json.dumps(self.planner) if isinstance(self.planner, dict) else self.planner
        elif "controller of an IT/DevOps" in prompt:
            d = self._pop(self.decisions)
            kind, text = "agent", d if isinstance(d, str) else json.dumps(d)
        elif "strict fact-checker" in prompt:
            kind, text = "verify", json.dumps(self._pop(self.verdicts))
        else:
            kind, text = "answer", self._pop(self.answers)
        self.calls.append(kind)
        self.prompts.setdefault(kind, []).append(prompt)
        return SimpleNamespace(content=text, usage_metadata={"input_tokens": 10, "output_tokens": 5})


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeLLM(**kw)
        monkeypatch.setattr(llm_mod, "_get_client", lambda role: f)
        return f

    monkeypatch.setattr(search_mod, "_retrieve", lambda q: [HIT, HIT2])
    return install


def run(question, history=None):
    state = {"messages": [*(history or []), {"role": "user", "content": question}]}
    return rag_agent.invoke(state, config={"recursion_limit": 60})


def test_conversational_path_skips_retrieval_and_verification(fake):
    f = fake(planner={"mode": "conversational", "query": ""}, answers=["Hi! How can I help?"])
    out = run("hello")
    assert out["final_answer"] == "Hi! How can I help?" and out["mode"] == "conversational"
    assert f.calls == ["planner", "answer"]
    assert out["plan"][0] == "Intent: Conversational/Memory"


def test_troubleshoot_flow_with_logs_runs_tool_search_answer_verify(fake):
    f = fake()
    out = run("Pod crashed:\nLast State: Terminated Reason: OOMKilled Exit Code: 137\nBack-off restarting failed container")
    assert out["mode"] == "troubleshoot"
    assert "parse_logs" in out["tools_used"]  # auto-run by the planner
    assert out["citations"] == ["oom.txt", "pending.txt"]
    assert out["grounded"] is True and "Verified" in " ".join(out["plan"])
    assert f.calls == ["planner", "agent", "answer", "verify"]
    # untrusted input is delimited in the answer prompt
    assert "<user_message>" in f.prompts["answer"][0] and "<tool_result" in f.prompts["answer"][0]
    assert any(p.startswith("Search Term:") for p in out["plan"])  # eval compatibility


def test_agent_can_call_a_tool_then_finish(fake):
    f = fake(planner={"mode": "question", "query": "pod cidr"},
             decisions=[{"action": "tool", "name": "cidr_overlap", "args": {"cidr_a": "10.0.0.0/8", "cidr_b": "10.244.0.0/16"}}, {"action": "final"}])
    out = run("Do 10.0.0.0/8 and 10.244.0.0/16 overlap?")
    assert out["tools_used"] == ["cidr_overlap"]
    tool_obs = [e for e in out["scratchpad"] if e["type"] == "tool"][0]["observation"]
    assert '"overlaps": true' in tool_obs
    assert "cidr_overlap" in f.prompts["answer"][0]
    assert stats.snapshot()["agent"]["tool_calls"]["cidr_overlap"] == 1


def test_agent_can_search_again_with_a_new_query(fake, monkeypatch):
    calls = []
    monkeypatch.setattr(search_mod, "_retrieve", lambda q: calls.append(q) or ([HIT] if "memory" in q else [HIT2]))
    fake(decisions=[{"action": "search", "query": "node allocatable requests"}, {"action": "final"}])
    out = run("why pending")
    assert len(calls) == 2 and set(out["citations"]) == {"oom.txt", "pending.txt"} and out["steps"] == 2


def test_duplicate_search_is_not_repeated(fake, monkeypatch):
    calls = []
    monkeypatch.setattr(search_mod, "_retrieve", lambda q: calls.append(q) or [HIT])
    fake(decisions=[{"action": "search", "query": "oomkilled memory limit"}, {"action": "final"}])
    out = run("why oom")
    assert len(calls) == 1
    assert any("duplicate" in p for p in out["plan"])


def test_step_budget_stops_a_runaway_agent(fake, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_STEPS", 3)
    n = iter(range(100))
    monkeypatch.setattr(search_mod, "_retrieve", lambda q: [HIT])
    f = fake()

    # make every decision a brand-new search
    orig = f.invoke

    def invoke(prompt):
        if "controller of an IT/DevOps" in prompt:
            f.decisions = [{"action": "search", "query": f"endless query {next(n)}"}]
        return orig(prompt)

    f.invoke = invoke
    out = run("loop please")
    assert out["steps"] == 3 and out["final_answer"]
    assert any("step budget" in p for p in out["plan"])


@pytest.mark.parametrize("garbage", ["I think we should stop.", '{"action": "teleport"}', '{"action": "tool", "name": "rm_rf", "args": {}}', '{"action": "search", "query": ""}'])
def test_garbage_agent_output_falls_back_to_answering(fake, garbage):
    f = fake(decisions=[garbage])
    out = run("why oom")
    assert out["final_answer"] and f.calls.count("answer") == 1


def test_planner_tolerates_non_json_output(fake):
    fake(planner="CONVERSATIONAL", answers=["hello!"])
    assert run("thanks!")["mode"] == "conversational"
    fake(planner="kubernetes oomkilled memory limit")  # model ignored the JSON instruction
    out = run("why was my pod killed")
    assert out["mode"] == "question" and out["current_query"] == "kubernetes oomkilled memory limit"


def test_pasted_logs_are_never_treated_as_small_talk(fake):
    fake(planner={"mode": "conversational", "query": ""})
    out = run("Events:\nWarning FailedScheduling 0/3 nodes are available: 3 Insufficient memory.\nreason: x\nmore")
    assert out["mode"] == "troubleshoot"


def test_no_evidence_returns_not_found_without_calling_the_answer_model(fake, monkeypatch):
    monkeypatch.setattr(search_mod, "_retrieve", lambda q: [])
    f = fake(planner={"mode": "question", "query": "obscure"})
    out = run("what is the airspeed of a swallow")
    assert out["final_answer"] == NOT_FOUND and "answer" not in f.calls and "verify" not in f.calls


def test_verify_failure_regenerates_once_with_feedback(fake):
    f = fake(verdicts=[{"supported": False, "unsupported_claims": ["kernel 9.9 fixes it"]}, {"supported": True, "unsupported_claims": []}],
             answers=["Draft with kernel 9.9 claim", "Clean draft"])
    out = run("why oom")
    assert out["final_answer"] == "Clean draft" and out["grounded"] is True
    assert f.calls == ["planner", "agent", "answer", "verify", "answer", "verify"]
    assert "kernel 9.9 fixes it" in f.prompts["answer"][1]  # reviewer feedback reaches the 2nd draft
    assert stats.snapshot()["agent"]["verify_regenerated"] == 1


def test_still_unsupported_after_retry_is_flagged_not_blocked(fake):
    f = fake(verdicts=[{"supported": False, "unsupported_claims": ["x"]}], answers=["Draft"])
    out = run("why oom")
    assert out["grounded"] is False and "could not be fully verified" in out["final_answer"]
    assert f.calls.count("answer") == 2  # exactly one regeneration


def test_verifier_garbage_or_disabled_is_skipped(fake, monkeypatch):
    f = fake(verdicts=[{"unexpected": 1}])
    assert run("why oom")["grounded"] is None
    monkeypatch.setattr(settings, "ENABLE_VERIFY", False)
    f2 = fake()
    run("why oom")
    assert "verify" not in f2.calls and f.calls


def test_risky_commands_get_a_safety_notice_and_are_reported(fake):
    answer = "## Commands\n```bash\nkubectl get pods -n prod\nkubectl delete pod web-1 -n prod\n```"
    fake(answers=[answer])
    out = run("why oom")
    risks = {r["command"]: r["risk"] for r in out["command_risks"]}
    assert risks["kubectl get pods -n prod"] == "read_only" and risks["kubectl delete pod web-1 -n prod"] == "destructive"
    assert "Command safety review" in out["final_answer"] and out["final_answer"].count("Command safety review") == 1


def test_history_reaches_the_planner(fake):
    f = fake(planner={"mode": "question", "query": "oom"})
    run("and how do I fix it?", history=[{"role": "user", "content": "my pod is OOMKilled"}, {"role": "assistant", "content": "It exceeded its memory limit."}])
    assert "my pod is OOMKilled" in f.prompts["planner"][0]


def test_llm_calls_are_counted_with_tokens(fake):
    fake()
    run("why oom")
    s = stats.snapshot()["llm"]
    assert s["calls"]["fast"] >= 2 and s["calls"]["answer"] == 1 and s["tokens"]["prompt"] > 0
