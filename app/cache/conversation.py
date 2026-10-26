import json

import logfire

from app.cache.store import get_store
from app.config import settings


def _key(thread_id: str) -> str:
    return f"conv:{thread_id}"


def load_history(thread_id: str) -> list[dict]:
    try:
        return [json.loads(m) for m in get_store().lrange_all(_key(thread_id))]
    except Exception as e:  # noqa: BLE001
        logfire.warning(f"Conversation load failed (continuing without history): {e}")
        return []


def append_messages(thread_id: str, messages: list[dict]) -> None:
    try:
        store, key = get_store(), _key(thread_id)
        for m in messages:
            store.rpush(key, json.dumps({"role": m["role"], "content": m["content"]}))
        store.ltrim_last(key, settings.CONVERSATION_MAX_MESSAGES)
        store.expire(key, settings.CONVERSATION_TTL)
    except Exception as e:  # noqa: BLE001
        logfire.warning(f"Conversation save failed (ignored): {e}")


def clear_history(thread_id: str) -> None:
    try:
        get_store().delete(_key(thread_id))
    except Exception as e:  # noqa: BLE001
        logfire.warning(f"Conversation clear failed (ignored): {e}")
