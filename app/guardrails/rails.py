import re
from dataclasses import dataclass

import logfire

from app.config import settings
from app.guardrails.colang_rules import (
    COLANG_CONTENT,
    JAILBREAK_PATTERNS,
    OFF_TOPIC_PATTERNS,
    YAML_CONTENT,
)

_rails = None  # nemoguardrails.LLMRails, created at startup

UNAVAILABLE_MSG = (
    "The safety check is temporarily unavailable, so I can't process this request right now. "
    "Please try again in a moment."
)


@dataclass
class GuardResult:
    blocked: bool
    rail: str | None = None
    response: str | None = None


def initialize_rails() -> None:
    """Build the NeMo Guardrails singleton (heavy imports are deferred to here)."""
    global _rails
    from nemoguardrails import LLMRails, RailsConfig

    from app.gateway.client import get_guard_llm

    config = RailsConfig.from_content(colang_content=COLANG_CONTENT, yaml_content=YAML_CONTENT)
    _rails = LLMRails(config, llm=get_guard_llm())
    logfire.info("NeMo Guardrails initialised.")


def parse_rail_response(content: str) -> tuple[bool, str | None, str]:
    """Return (fired, rail_name, clean_response) for a '[RAIL:NAME] text' marker."""
    match = re.match(r"^\[RAIL:([A-Z_]+)\]\s*", content or "")
    if not match:
        return False, None, (content or "").strip()
    return True, match.group(1), content[match.end():].strip()


def deterministic_guard(message: str) -> tuple[bool, str | None]:
    text = " ".join(message.lower().split())

    for pattern in JAILBREAK_PATTERNS:
        if pattern in text:
            return True, (
                "I maintain consistent guidelines regardless of how I am prompted. "
                f"I am here to help with {settings.DOMAIN_DESCRIPTION}. What can I help you with?"
            )

    for pattern in OFF_TOPIC_PATTERNS:
        if pattern in text:
            return True, (
                f"I'm an Enterprise IT Assistant focused on {settings.DOMAIN_DESCRIPTION}. "
                "I can't help with that — but ask me anything technical!"
            )

    return False, None


def _fail(reason: str) -> GuardResult:
    """Guardrail infrastructure failed: fail CLOSED unless explicitly configured otherwise."""
    if settings.GUARDRAILS_FAIL_OPEN:
        logfire.warning(f"Guardrails unavailable ({reason}) — FAIL-OPEN is enabled, allowing request.")
        return GuardResult(blocked=False)
    logfire.error(f"Guardrails unavailable ({reason}) — failing closed.")
    return GuardResult(blocked=True, rail="UNAVAILABLE", response=UNAVAILABLE_MSG)


def guard(message: str) -> GuardResult:
    # Layer 1 — deterministic checks (cheap, no LLM)
    fired, response = deterministic_guard(message)
    if fired:
        logfire.info(f"Deterministic guard fired | query='{message[:80]}'")
        return GuardResult(blocked=True, rail="DETERMINISTIC", response=response)

    # Layer 2 — NeMo Guardrails
    if _rails is None:
        return _fail("not initialised")

    with logfire.span("Guardrails Check"):
        try:
            result = _rails.generate(messages=[{"role": "user", "content": message}])
        except Exception as e:  # noqa: BLE001
            return _fail(f"NeMo error: {e}")

    content = result.get("content", "") if isinstance(result, dict) else str(result)
    fired, rail, response = parse_rail_response(content)
    if fired:
        logfire.info(f"Guardrails fired | rail={rail} | query='{message[:80]}'")
        return GuardResult(blocked=True, rail=rail, response=response)

    logfire.info("Guardrails passed.")
    return GuardResult(blocked=False)
