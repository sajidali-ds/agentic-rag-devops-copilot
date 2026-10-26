from langchain_openai import ChatOpenAI
from portkey_ai import PORTKEY_GATEWAY_URL, Portkey, createHeaders

from app.config import settings

GATEWAY_CONFIG = {
    "strategy": {"mode": "fallback"},
    "cache": {"mode": "simple"},
    "retry": {"attempts": 2, "on_status_codes": [429, 503]},
    "targets": [
        {"override_params": {"model": settings.primary_model}},
        {"override_params": {"model": settings.fallback_model}},
    ],
}

portkey_client = Portkey(api_key=settings.PORTKEY_API_KEY)


def _headers(feature: str) -> dict:
    kwargs = {
        "api_key": settings.PORTKEY_API_KEY,
        "metadata": {"feature": feature, "environment": settings.APP_ENV},
    }
    if settings.PORTKEY_CONFIG_ID:
        kwargs["config"] = settings.PORTKEY_CONFIG_ID
    return createHeaders(**kwargs)


def get_langchain_llm(feature: str = "rag", max_tokens: int = 1000, model: str | None = None) -> ChatOpenAI:

    model = model or settings.primary_model
    extra = {}
    if settings.LLM_REASONING_EFFORT and "gpt-oss" in model:
        extra["extra_body"] = {"reasoning_effort": settings.LLM_REASONING_EFFORT}
    return ChatOpenAI(
        api_key=settings.PORTKEY_API_KEY,
        base_url=PORTKEY_GATEWAY_URL,
        model=model,
        temperature=0,
        default_headers=_headers(feature),
        max_tokens=max_tokens,
        **extra,
    )


def get_guard_llm() -> ChatOpenAI:
    """Small-output LLM for guardrail intent classification."""
    return get_langchain_llm(feature="guardrails", max_tokens=200, model=settings.fast_model)


def extract_cache_status(response) -> str:
    """Return 'HIT' / 'MISS' from Portkey's cache header when exposed, else 'MISS'."""
    meta = getattr(response, "response_metadata", None) or {}
    headers = meta.get("headers") or {}
    for key in ("x-portkey-cache-status", "X-Portkey-Cache-Status"):
        if headers.get(key):
            return str(headers[key]).upper()
    for attr in ("_raw_response", "_response", "_http_response"):
        raw = getattr(response, attr, None)
        status = getattr(raw, "headers", {}).get("x-portkey-cache-status", "") if raw is not None else ""
        if status:
            return status.upper()
    return "MISS"
