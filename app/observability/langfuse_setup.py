import logfire

from app.config import settings


def enabled() -> bool:
    return settings.LANGFUSE_ENABLED


def make_handler():
    """New LangChain callback handler for ONE request (or None when disabled/unavailable)."""
    if not enabled():
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception as e:  # noqa: BLE001
        logfire.warning(f"Langfuse handler unavailable (tracing disabled for this request): {e}")
        return None


def run_config(thread_id: str, handler, recursion_limit: int, tags: list[str] | None = None) -> dict:
    cfg: dict = {"recursion_limit": recursion_limit}
    if handler is not None:
        cfg["callbacks"] = [handler]
        cfg["metadata"] = {
            "langfuse_session_id": thread_id,
            "langfuse_tags": [settings.APP_ENV, "agentic-rag", *(tags or [])],
        }
    return cfg


def trace_id_of(handler) -> str | None:
    return getattr(handler, "last_trace_id", None) if handler is not None else None


def score(trace_id: str | None, name: str, value: float, comment: str | None = None) -> bool:
    """Attach a numeric score to a trace. Returns True if it was sent."""
    if not (enabled() and trace_id):
        return False
    try:
        from langfuse import get_client

        get_client().create_score(trace_id=trace_id, name=name, value=float(value), data_type="NUMERIC", comment=comment)
        return True
    except Exception as e:  # noqa: BLE001
        logfire.warning(f"Langfuse score '{name}' failed (ignored): {e}")
        return False


def flush() -> None:
    if not enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as e:  # noqa: BLE001
        logfire.warning(f"Langfuse flush failed: {e}")
