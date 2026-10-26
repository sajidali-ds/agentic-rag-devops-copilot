import logfire
from qdrant_client import QdrantClient

from app.config import settings
from app.services.retrieval.embeddings import embed_query

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    """Lazy Qdrant client (no network/config work at import time)."""
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.QDRANT_CLUSTER_ENDPOINT, api_key=settings.QDRANT_API_KEY, timeout=30)
    return _client


def search_enterprise_knowledge(query: str, limit: int = 8) -> list[dict]:
    """Dense vector search. Returns [{content, source, chunk_index, score}]."""
    response = None
    for attempt in (1, 2):  # one retry: remote clusters occasionally time out
        try:
            vector = embed_query(query)
            response = get_client().query_points(
                collection_name=settings.QDRANT_COLLECTION_NAME,
                query=vector,
                limit=limit,
                with_payload=True,
            )
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "dimension" in msg.lower() or "vector" in msg.lower():
                logfire.error(
                    "Qdrant search failed — likely embedding-dimension mismatch. Re-ingest with "
                    f"`--wipe` using EMBEDDING_PROVIDER={settings.EMBEDDING_PROVIDER}. Error: {msg}"
                )
                return []
            logfire.error(f"Qdrant search failed (attempt {attempt}/2): {msg}")
    if response is None:
        return []

    return [
        {
            "content": p.payload.get("text", ""),
            "source": p.payload.get("source", "Unknown"),
            "chunk_index": p.payload.get("chunk_index"),
            "score": p.score,
        }
        for p in response.points
    ]