import time
from functools import lru_cache

import logfire

from app.config import settings
from app.llm_json import strip_reasoning
from app.observability.stats import stats

ROLES = {
    # role -> (model getter, max_tokens)
    "fast": (lambda: settings.fast_model, 500),
    "answer": (lambda: settings.primary_model, 1400),
}

_NO_TOOL_NOTE = (
    "\n\nIMPORTANT: Do NOT call any function or tool. Reply with plain text (or the JSON object "
    "requested above) only."
)


@lru_cache(maxsize=None)
def _get_client(role: str):
    from app.gateway.client import get_langchain_llm

    model, max_tokens = ROLES[role][0](), ROLES[role][1]
    return get_langchain_llm(feature=f"agent-{role}", max_tokens=max_tokens, model=model)


def call_llm(role: str, prompt: str, purpose: str = "") -> str:
    """Invoke the ``role`` model and return the cleaned text."""
    client = _get_client(role)
    start = time.perf_counter()
    try:
        response = client.invoke(prompt)
    except Exception as e:  # noqa: BLE001
        # gpt-oss on Groq sometimes emits a native tool call even though none are bound.
        if "tool_use_failed" not in str(e) and "model called a tool" not in str(e):
            raise
        logfire.warning(f"LLM tool_use_failed ({purpose}) — retrying with no-tool instruction")
        response = client.invoke(prompt + _NO_TOOL_NOTE)
    elapsed = (time.perf_counter() - start) * 1000

    usage = getattr(response, "usage_metadata", None) or {}
    p_tok, c_tok = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    stats.llm_call(role, elapsed, p_tok, c_tok)
    logfire.info(
        "llm call",
        role=role,
        purpose=purpose,
        latency_ms=round(elapsed),
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    return strip_reasoning(content)
