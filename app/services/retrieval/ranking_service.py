import time

import logfire

# Lazy initialisation: the ONNX model loads on first use (after logfire is configured).
_ranker = None


def _get_ranker():
    global _ranker
    if _ranker is None:
        from flashrank import Ranker

        logfire.info("Initialising FlashRank cross-encoder locally...")
        try:
            _ranker = Ranker(cache_dir="/tmp/flashrank")
        except Exception:  # noqa: BLE001  (read-only /tmp etc.)
            _ranker = Ranker()
    return _ranker


def _score(query: str, passages: list[dict]) -> list[dict]:
    """Run the cross-encoder. Isolated so tests can stub it without flashrank installed."""
    from flashrank import RerankRequest

    return _get_ranker().rerank(RerankRequest(query=query, passages=passages))


def rerank_results(query: str, results: list[dict], top_n: int = 5) -> list[dict]:
    """Re-score retrieval hits with a cross-encoder, preserving metadata (source, etc.).

    Falls back to the original vector-search order if reranking fails, so the user
    still gets an answer.
    """
    if not results:
        return []

    start = time.time()
    try:
        passages = [{"id": i, "text": r["content"]} for i, r in enumerate(results)]
        ranked = _score(query, passages)
        out = []
        for item in ranked[:top_n]:
            hit = dict(results[int(item["id"])])
            hit["rerank_score"] = float(item["score"])
            out.append(hit)
        logfire.info(f"Reranked {len(results)} -> {len(out)} in {time.time() - start:.2f}s")
        return out
    except Exception as e:  # noqa: BLE001
        logfire.error(f"Reranking failed, using vector order: {e}")
        return results[:top_n]


def rerank_documents(query: str, documents: list[str], top_n: int = 5) -> list[str]:
    """Backward-compatible string API."""
    wrapped = [{"content": d} for d in documents]
    return [r["content"] for r in rerank_results(query, wrapped, top_n)]
