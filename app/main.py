import json
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal, Optional

import logfire
from dotenv import load_dotenv

load_dotenv()
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"), send_to_logfire="if-token-present")

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.agents.graph import RECURSION_LIMIT, rag_agent  # noqa: E402
from app.agents.nodes.responder import NOT_FOUND  # noqa: E402
from app.cache.answer_cache import AnswerCache  # noqa: E402
from app.cache.conversation import append_messages, clear_history, load_history  # noqa: E402
from app.cache.store import get_store  # noqa: E402
from app.config import settings  # noqa: E402
from app.guardrails.rails import guard, initialize_rails  # noqa: E402
from app.observability import langfuse_setup  # noqa: E402
from app.observability.stats import stats  # noqa: E402

answer_cache = AnswerCache()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_rails()
    get_store()  # connect to Redis early (falls back to memory with a warning)
    yield
    langfuse_setup.flush()


app = FastAPI(title="IT/DevOps Troubleshooting Copilot (Agentic RAG)", lifespan=lifespan)

if settings.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])


# ── Auth + rate limiting (both opt-in via env) ──────────────────────────────────
_hits: dict[str, deque] = defaultdict(deque)


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


def rate_limit(request: Request) -> None:
    limit = settings.RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return
    client = request.client.host if request.client else "unknown"
    now = time.time()
    window = _hits[client]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")
    window.append(now)


class QueryRequest(BaseModel):
    q: str = Field(min_length=1, max_length=settings.MAX_QUERY_CHARS)
    thread_id: Optional[str] = None  # each caller gets its own memory unless it supplies one


class FeedbackRequest(BaseModel):
    request_id: str
    rating: Literal["up", "down"]
    trace_id: Optional[str] = None
    thread_id: Optional[str] = None
    comment: Optional[str] = Field(default=None, max_length=1000)


@app.get("/")
def home():
    return {"message": "IT/DevOps Troubleshooting Copilot API is live."}


@app.get("/health")
def health():
    return {"status": "ok", "cache_store": get_store().kind, "langfuse": langfuse_setup.enabled()}


@app.get("/stats", dependencies=[Depends(require_api_key)])
def get_stats():
    """Runtime metrics: latency percentiles, cache hit-rate, tool usage, tokens."""
    return {**stats.snapshot(), "cache_store": get_store().kind, "langfuse": langfuse_setup.enabled()}


@app.get("/graph")
def get_graph_image():
    try:
        return Response(content=rag_agent.get_graph().draw_mermaid_png(), media_type="image/png")
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": f"Could not generate graph image: {e}"})


def _elapsed_ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


@app.post("/query", dependencies=[Depends(require_api_key), Depends(rate_limit)])
def query(request: QueryRequest):
    """Guardrails -> cache -> agent loop (search/tools/verify) -> answer."""
    t0 = time.perf_counter()
    q = request.q
    thread_id = request.thread_id or str(uuid.uuid4())
    request_id = uuid.uuid4().hex
    base = {"question": q, "thread_id": thread_id, "request_id": request_id, "trace_id": None}

    try:
        # Gate 1: input guardrails
        guard_result = guard(q)
        if guard_result.blocked:
            logfire.info(f"Request blocked by guardrails | rail={guard_result.rail} | thread={thread_id}")
            unavailable = guard_result.rail == "UNAVAILABLE"
            stats.incr("guardrail_blocks")
            return {
                **base,
                "answer": guard_result.response,
                "thought_process": ["Guardrails Unavailable" if unavailable else "Intent: Guardrails Fired", "Retrieval: Skipped"],
                "status": "Guardrails unavailable." if unavailable else "Blocked by guardrails.",
                "sources": [], "citations": [], "tools_used": [], "command_risks": [], "steps": 0,
                "cache": "bypass", "latency_ms": _elapsed_ms(t0),
            }

        history = load_history(thread_id)

        # Gate 2: answer cache — only for standalone questions (answers that depend on history are unsafe to reuse)
        if not history:
            hit = answer_cache.lookup(q)
            if hit:
                kind, payload = hit
                append_messages(thread_id, [{"role": "user", "content": q}, {"role": "assistant", "content": payload["answer"]}])
                ms = _elapsed_ms(t0)
                stats.request(ms)
                logfire.info(f"Cache {kind} hit | {ms}ms")
                return {**base, **payload, "thought_process": [f"Cache: {kind} hit", *payload.get("thought_process", [])],
                        "cache": kind, "latency_ms": ms}

        # Gate 3: the agent
        handler = langfuse_setup.make_handler()
        config = langfuse_setup.run_config(thread_id, handler, RECURSION_LIMIT)
        state_in = {"messages": [*history, {"role": "user", "content": q}]}
        out = rag_agent.invoke(state_in, config=config)

        answer = out.get("final_answer") or ""
        payload = {
            "answer": answer,
            "thought_process": out.get("plan") or [],
            "status": out.get("status"),
            "sources": out.get("documents", []),
            "citations": out.get("citations", []),
            "tools_used": out.get("tools_used", []),
            "command_risks": out.get("command_risks", []),
            "steps": out.get("steps", 0),
            "mode": out.get("mode"),
            "grounded": out.get("grounded"),
        }

        append_messages(thread_id, [{"role": "user", "content": q}, {"role": "assistant", "content": answer}])
        if out.get("mode") != "conversational" and out.get("grounded") is not False and answer and not answer.startswith(NOT_FOUND[:40]):
            answer_cache.save(q, payload)

        trace_id = langfuse_setup.trace_id_of(handler)
        if trace_id:
            if out.get("grounded") is not None:
                langfuse_setup.score(trace_id, "grounded", 1.0 if out["grounded"] else 0.0)
            langfuse_setup.score(trace_id, "agent_steps", out.get("steps", 0))

        ms = _elapsed_ms(t0)
        stats.request(ms, out.get("steps", 0))
        return {**base, **payload, "trace_id": trace_id, "cache": "miss", "latency_ms": ms}
    except Exception as e:  # noqa: BLE001
        stats.incr("errors")
        logfire.error(f"Backend execution failed: {e}")
        # A real 5xx so clients, evals and monitors can tell failure from a valid answer.
        return JSONResponse(
            status_code=500,
            content={
                **base,
                "answer": "I apologize, but I encountered an internal error while processing your request. Please try again later.",
                "thought_process": ["Error encountered during execution."], "status": "error",
                "sources": [], "citations": [], "tools_used": [], "command_risks": [], "steps": 0,
                "cache": "miss", "latency_ms": _elapsed_ms(t0),
            },
        )


@app.post("/feedback", dependencies=[Depends(require_api_key)])
def feedback(fb: FeedbackRequest):
    """Thumbs up/down on an answer: stored as a Langfuse score (if enabled) and in a local JSONL log."""
    value = 1.0 if fb.rating == "up" else 0.0
    sent = langfuse_setup.score(fb.trace_id, "user_feedback", value, fb.comment)
    try:
        with open(settings.FEEDBACK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **fb.model_dump()}, ensure_ascii=False) + "\n")
    except OSError as e:
        logfire.warning(f"Could not write feedback log: {e}")
    stats.incr(f"feedback.{fb.rating}")
    return {"recorded": True, "langfuse": sent}


@app.delete("/conversations/{thread_id}", dependencies=[Depends(require_api_key)])
def delete_conversation(thread_id: str):
    clear_history(thread_id)
    return {"cleared": thread_id}
