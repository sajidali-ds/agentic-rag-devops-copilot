import logfire

from app.agents.nodes.responder import NOT_FOUND, build_context
from app.agents.state import AgentState
from app.config import settings
from app.llm import call_llm
from app.llm_json import extract_json
from app.observability.stats import stats

PROMPT = """You are a strict fact-checker. Compare the ANSWER with the EVIDENCE.

EVIDENCE:
{evidence}

ANSWER:
{answer}

List specific factual claims in the ANSWER (causes, numbers, version/behaviour statements, named settings) that
are NOT supported by the EVIDENCE. Ignore generic safe advice (check logs, describe the resource) and formatting.
Reply with ONLY JSON: {{"supported": true|false, "unsupported_claims": ["..."]}}"""


def verify_node(state: AgentState):
    draft = state.get("draft_answer", "")
    plan = list(state.get("plan", []))
    if (state.get("mode") == "conversational" or not settings.ENABLE_VERIFY or draft == NOT_FOUND
            or not (state.get("documents") or state.get("scratchpad"))):
        return {"grounded": None, "needs_regen": False}

    with logfire.span("Groundedness Check"):
        raw = call_llm("fast", PROMPT.format(evidence=build_context(state)[:12000], answer=draft[:6000]), purpose="verify")
    verdict = extract_json(raw)
    if not verdict or "supported" not in verdict:
        logfire.warning("Verifier returned no usable verdict; skipping.")
        return {"grounded": None, "needs_regen": False}

    if verdict["supported"]:
        return {"grounded": True, "needs_regen": False, "plan": plan + ["Verified: grounded in evidence"]}

    stats.incr("verify.failed")
    claims = [str(c) for c in (verdict.get("unsupported_claims") or [])][:5]
    if state.get("draft_retry", 0) == 0:
        stats.incr("verify.regenerated")
        return {"grounded": False, "needs_regen": True, "draft_retry": 1, "verify_notes": "\n- ".join(claims) or "unspecified claims",
                "plan": plan + ["Verify: unsupported claims → regenerating once"]}
    return {"grounded": False, "needs_regen": False, "verify_notes": "\n- ".join(claims),
            "plan": plan + ["Verify: still unsupported after retry (flagged)"]}
