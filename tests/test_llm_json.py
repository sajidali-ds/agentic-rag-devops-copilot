from app.llm_json import extract_json, strip_reasoning


def test_strip_reasoning_blocks():
    assert strip_reasoning("<think>hidden plan</think>\nAnswer") == "Answer"
    assert strip_reasoning("ok <think>never closed") == "ok"


def test_plain_and_fenced_json():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_embedded_in_chatter_and_braces_in_strings():
    assert extract_json('Sure! Here you go: {"query": "pods {x}", "mode": "question"} hope it helps') == {"query": "pods {x}", "mode": "question"}


def test_think_block_before_json():
    assert extract_json('<think>{"decoy": true}</think>{"action": "final"}') == {"action": "final"}


def test_garbage_returns_none():
    assert extract_json("no json here") is None
    assert extract_json("") is None
    assert extract_json("[1, 2]") is None
