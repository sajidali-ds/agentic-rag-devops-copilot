import requests

from evals import pipeline


def test_detect_tool():
    assert pipeline.detect_tool(["Intent: Guardrails Fired"]) == "guardrails"
    assert pipeline.detect_tool(["Intent: Technical", "Search Term: x"]) == "retrieve_documents"
    assert pipeline.detect_tool(["Intent: Conversational/Memory"]) == "direct_answer"
    assert pipeline.detect_tool(["Guardrails Unavailable"]) == "unknown"


def test_failed_call_does_not_inherit_ground_truth_contexts(monkeypatch):
    """Regression: a failed API call used to be scored against the *reference* contexts,
    which silently inflated context recall/precision."""
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(pipeline.requests, "post", boom)
    monkeypatch.setattr(pipeline, "DELAY_BETWEEN_CALLS", 0)
    ds = {"rag_samples": [{"question": "q", "relevant_contexts": ["GOLD"]}]}
    out = pipeline.run_pipeline(ds)
    assert out["rag_samples"][0]["actual_contexts"] == []
