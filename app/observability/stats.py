import threading
from collections import Counter, deque

_WINDOW = 500


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round(p / 100 * (len(sorted_vals) - 1))))
    return round(sorted_vals[idx], 1)


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.counters: Counter = Counter()
            self.tools: Counter = Counter()
            self.tokens: Counter = Counter()
            self.latency_ms: deque = deque(maxlen=_WINDOW)
            self.llm_latency_ms: dict[str, deque] = {}
            self.steps: deque = deque(maxlen=_WINDOW)

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def tool(self, name: str) -> None:
        with self._lock:
            self.tools[name] += 1

    def llm_call(self, role: str, latency_ms: float, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        with self._lock:
            self.counters[f"llm_calls.{role}"] += 1
            self.tokens["prompt"] += prompt_tokens
            self.tokens["completion"] += completion_tokens
            self.tokens[f"{role}.total"] += prompt_tokens + completion_tokens
            self.llm_latency_ms.setdefault(role, deque(maxlen=_WINDOW)).append(latency_ms)

    def request(self, latency_ms: float, steps: int | None = None) -> None:
        with self._lock:
            self.counters["requests"] += 1
            self.latency_ms.append(latency_ms)
            if steps is not None:
                self.steps.append(steps)

    def snapshot(self) -> dict:
        with self._lock:
            lat = sorted(self.latency_ms)
            c = dict(self.counters)
            hits = c.get("cache.exact_hit", 0) + c.get("cache.semantic_hit", 0)
            lookups = hits + c.get("cache.miss", 0)
            return {
                "requests": c.get("requests", 0),
                "errors": c.get("errors", 0),
                "guardrail_blocks": c.get("guardrail_blocks", 0),
                "latency_ms": {"p50": _pct(lat, 50), "p95": _pct(lat, 95), "p99": _pct(lat, 99), "samples": len(lat)},
                "cache": {
                    "exact_hits": c.get("cache.exact_hit", 0),
                    "semantic_hits": c.get("cache.semantic_hit", 0),
                    "misses": c.get("cache.miss", 0),
                    "hit_rate": round(hits / lookups, 3) if lookups else 0.0,
                    "retrieval_hits": c.get("cache.retrieval_hit", 0),
                    "retrieval_misses": c.get("cache.retrieval_miss", 0),
                },
                "agent": {
                    "avg_steps": round(sum(self.steps) / len(self.steps), 2) if self.steps else 0.0,
                    "tool_calls": dict(self.tools),
                    "verify_failed": c.get("verify.failed", 0),
                    "verify_regenerated": c.get("verify.regenerated", 0),
                },
                "llm": {
                    "calls": {k.split(".", 1)[1]: v for k, v in c.items() if k.startswith("llm_calls.")},
                    "tokens": dict(self.tokens),
                    "latency_ms": {
                        role: {"p50": _pct(sorted(v), 50), "p95": _pct(sorted(v), 95)}
                        for role, v in self.llm_latency_ms.items()
                    },
                },
            }


stats = Stats()
