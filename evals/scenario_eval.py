import json
import os
import re
import sys
import time
import uuid

import requests

API_URL = os.getenv("RAG_API_URL", "http://localhost:8000/query")
API_KEY = os.getenv("API_KEY")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}
MIN_PASS_RATE = float(os.getenv("SCENARIO_MIN_PASS_RATE", "0.8"))
DELAY = float(os.getenv("EVAL_DELAY_SECONDS", "2"))
SCENARIOS_PATH = os.path.join(os.path.dirname(__file__), "scenarios.json")
NOTICE_MARKER = "Command safety review"


def load_scenarios(path: str = SCENARIOS_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_scenario(scn: dict, resp: dict, repeat_resp: dict | None = None) -> list[tuple[str, bool, str]]:
    """Pure function: returns [(check_name, passed, detail)]. Unit-tested without any network."""
    answer = (resp.get("answer") or "")
    low = answer.lower()
    tools = set(resp.get("tools_used") or [])
    cited = " ".join(resp.get("citations") or []).lower()
    thought = " ".join(resp.get("thought_process") or []).lower()
    out: list[tuple[str, bool, str]] = []

    if scn.get("expect_blocked"):
        out.append(("blocked_by_guardrails", "guardrails fired" in thought, thought[:80]))
        return out

    out.append(("no_error", resp.get("status") != "error", str(resp.get("status"))))
    if "expect_tools_all" in scn:
        missing = [t for t in scn["expect_tools_all"] if t not in tools]
        out.append(("tools_all", not missing, f"missing={missing} used={sorted(tools)}"))
    if "expect_tools_any" in scn:
        out.append(("tools_any", bool(tools & set(scn["expect_tools_any"])), f"used={sorted(tools)}"))
    if "expect_keywords_any" in scn:
        hit = [k for k in scn["expect_keywords_any"] if k.lower() in low]
        out.append(("keywords_any", bool(hit), f"hit={hit}"))
    if "expect_cited_any" in scn:
        hit = [k for k in scn["expect_cited_any"] if k.lower() in cited]
        out.append(("cited_any", bool(hit), f"citations={resp.get('citations')}"))
    if scn.get("expect_notice_if_risky"):
        risky = [r for r in resp.get("command_risks") or [] if r.get("risk") in {"mutating", "destructive"}]
        out.append(("risky_commands_flagged", (not risky) or NOTICE_MARKER in answer, f"risky={len(risky)}"))
    for pattern in scn.get("must_not_match", []):
        out.append((f"must_not_match:{pattern}", re.search(pattern, answer) is None, ""))
    if scn.get("expect_cache_on_repeat"):
        kind = (repeat_resp or {}).get("cache")
        out.append(("cache_on_repeat", kind in {"exact", "semantic"}, f"cache={kind}"))
    return out


def _call(question: str) -> dict:
    r = requests.post(API_URL, json={"q": question, "thread_id": f"scn_{uuid.uuid4().hex[:8]}"}, headers=HEADERS, timeout=300)
    return r.json() if r.content else {"status": "error", "answer": ""}


def run(scenarios: list[dict] | None = None) -> dict:
    scenarios = scenarios if scenarios is not None else load_scenarios()
    results = []
    for scn in scenarios:
        try:
            resp = _call(scn["input"])
            time.sleep(DELAY)
            repeat = _call(scn["input"]) if scn.get("expect_cache_on_repeat") else None
            checks = check_scenario(scn, resp, repeat)
        except Exception as e:  # noqa: BLE001
            checks = [("request", False, f"{type(e).__name__}: {e}")]
            resp = {}
        passed = all(ok for _, ok, _ in checks)
        results.append({"id": scn["id"], "passed": passed, "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
                        "latency_ms": resp.get("latency_ms"), "steps": resp.get("steps"), "tools_used": resp.get("tools_used")})
        print(f"{'PASS' if passed else 'FAIL'}  {scn['id']:<34} " + ("" if passed else "; ".join(f"{n}: {d}" for n, ok, d in checks if not ok)))
        time.sleep(DELAY)
    rate = sum(r["passed"] for r in results) / len(results) if results else 0.0
    return {"pass_rate": round(rate, 3), "results": results}


if __name__ == "__main__":
    report = run()
    with open("scenario_results.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nPass rate: {report['pass_rate']:.0%} (gate {MIN_PASS_RATE:.0%}) -> scenario_results.json")
    sys.exit(0 if report["pass_rate"] >= MIN_PASS_RATE else 1)
