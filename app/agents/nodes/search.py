import logfire

from app.agents.state import AgentState
from app.cache.answer_cache import normalize
from app.cache.retrieval_cache import cached_retrieve
from app.config import settings
from app.services.retrieval.qdrant_service import search_enterprise_knowledge
from app.services.retrieval.ranking_service import rerank_results


def _retrieve(query: str) -> list[dict]:
    raw = search_enterprise_knowledge(query, limit=settings.RETRIEVE_TOP_N)
    return rerank_results(query, raw, top_n=settings.RERANK_TOP_K)


def _score(hit: dict) -> float:
    return float(hit.get("rerank_score", hit.get("score", 0.0)) or 0.0)


def _fmt(hit: dict) -> str:
    return f"[Source: {hit['source']}]\n{hit['content']}"


def search_node(state: AgentState):
    """Vector search -> rerank (cached). Merges new chunks into the evidence pool."""
    action = state.get("next_action") or {}
    query = str(action.get("query") or state.get("current_query") or "").strip()
    searched = list(state.get("searched", []))
    steps = state.get("steps", 0) + 1
    plan = list(state.get("plan", []))

    if not query or normalize(query) in searched:
        obs = {"type": "search", "query": query, "observation": "Already searched this query. Use a different query or finish."}
        return {"scratchpad": [obs], "steps": steps, "plan": plan + [f"Search skipped (duplicate): {query}"]}

    with logfire.span("Knowledge Retrieval", query=query):
        hits, from_cache = cached_retrieve(query, _retrieve)

    pool = list(state.get("doc_hits", []))
    seen = {(h["source"], h["content"][:120]) for h in pool}
    new = [h for h in hits if (h["source"], h["content"][:120]) not in seen]
    pool = sorted(pool + new, key=_score, reverse=True)[: settings.RERANK_TOP_K * 2]

    citations: list[str] = []
    for h in pool:
        if h["source"] not in citations:
            citations.append(h["source"])

    if hits:
        top = "; ".join(f"{h['source']}: {h['content'][:160].strip()!r}" for h in hits[:2])
        observation = f"{len(hits)} chunks ({len(new)} new) from {sorted({h['source'] for h in hits})}. Top: {top}"
    else:
        observation = "No relevant chunks found for this query."

    first = not searched
    return {
        "doc_hits": pool,
        "documents": [_fmt(h) for h in pool],
        "citations": citations,
        "searched": searched + [normalize(query)],
        "steps": steps,
        "scratchpad": [{"type": "search", "query": query, "cached": from_cache, "observation": observation}],
        "plan": plan + ["Context Retrieved" if first and hits else f"Search: {query}" + (" (cache)" if from_cache else "")],
        "status": "Found technical context." if pool else "No relevant context found yet.",
    }
