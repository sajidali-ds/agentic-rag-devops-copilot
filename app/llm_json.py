
import json
import re

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> blocks (reasoning models) and surrounding whitespace."""
    text = _THINK.sub("", text or "")
    # an unterminated <think> (truncated output) should not leak into the answer
    text = re.sub(r"<think>.*\Z", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def extract_json(text: str) -> dict | None:
    """Return the first JSON object found in ``text`` or None. Tolerates fences and chatter."""
    text = _FENCE.sub("", strip_reasoning(text))
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        return obj if isinstance(obj, dict) else None
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None
