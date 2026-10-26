import json

from evals import scenario_eval as se


def checks(scn, resp, repeat=None):
    return {n: ok for n, ok, _ in se.check_scenario(scn, resp, repeat)}


def test_scenarios_file_is_valid_and_ids_unique():
    scenarios = se.load_scenarios()
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids)) >= 10
    assert all("input" in s for s in scenarios)
    json.dumps(scenarios)


def test_tool_keyword_and_citation_checks():
    scn = {"expect_tools_all": ["parse_logs"], "expect_keywords_any": ["memory limit"], "expect_cited_any": ["oom"]}
    good = {"status": "ok", "answer": "Raise the Memory Limit.", "tools_used": ["parse_logs"], "citations": ["kubernetes_oomkilled.txt"]}
    assert all(checks(scn, good).values())
    bad = {"status": "ok", "answer": "unrelated", "tools_used": [], "citations": []}
    assert not any(checks(scn, bad)[k] for k in ("tools_all", "keywords_any", "cited_any"))


def test_blocked_scenarios():
    scn = {"expect_blocked": True}
    assert checks(scn, {"thought_process": ["Intent: Guardrails Fired"]}) == {"blocked_by_guardrails": True}
    assert checks(scn, {"thought_process": ["Intent: Technical"]}) == {"blocked_by_guardrails": False}


def test_risky_commands_must_carry_the_notice():
    scn = {"expect_notice_if_risky": True}
    risky = [{"risk": "destructive"}]
    assert checks(scn, {"status": "ok", "answer": "kubectl delete ns x", "command_risks": risky})["risky_commands_flagged"] is False
    assert checks(scn, {"status": "ok", "answer": "x Command safety review y", "command_risks": risky})["risky_commands_flagged"] is True
    assert checks(scn, {"status": "ok", "answer": "x", "command_risks": []})["risky_commands_flagged"] is True


def test_must_not_match_and_cache_on_repeat():
    scn = {"must_not_match": [r"(?i)\bi ran\b"], "expect_cache_on_repeat": True}
    r = checks(scn, {"status": "ok", "answer": "I ran it"}, {"cache": "miss"})
    assert r["cache_on_repeat"] is False and r["must_not_match:(?i)\\bi ran\\b"] is False
    ok = checks(scn, {"status": "ok", "answer": "fine"}, {"cache": "semantic"})
    assert ok["cache_on_repeat"] is True
