import time

import logfire

from app.config import settings

BATCH_SIZE = 50

_model = None
_dim: int | None = None


def _load_local():
    from sentence_transformers import SentenceTransformer

    logfire.info(f"Loading local embedding model: {settings.LOCAL_EMBEDDING_MODEL}")
    return SentenceTransformer(settings.LOCAL_EMBEDDING_MODEL)


def _load_gemini():
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    logfire.info(f"Using Gemini embeddings: {settings.GEMINI_EMBEDDING_MODEL}")
    return GoogleGenerativeAIEmbeddings(
        model=settings.GEMINI_EMBEDDING_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
    )


def _init():
    """Lazy, once-per-process initialisation. Fails loudly on a bad provider."""
    global _model, _dim
    if _model is not None:
        return
    provider = settings.EMBEDDING_PROVIDER.lower()
    if provider == "local":
        _model = _load_local()
        _dim = int(_model.get_sentence_embedding_dimension())
    elif provider == "gemini":
        _model = _load_gemini()
        _dim = len(_model.embed_query("dimension probe"))
    else:
        raise ValueError(f"EMBEDDING_PROVIDER must be 'local' or 'gemini', got {provider!r}")


def get_embedding_dim() -> int:
    _init()
    return _dim  # type: ignore[return-value]


def _embed_batch(batch: list[str]) -> list[list[float]]:
    if settings.EMBEDDING_PROVIDER.lower() == "local":
        return _model.encode(batch, show_progress_bar=False).tolist()

    # Gemini: exponential backoff on rate limits (1s, 2s, 4s)
    for attempt in range(4):
        try:
            return _model.embed_documents(batch)
        except Exception as e:  # noqa: BLE001
            err = str(e).lower()
            rate_limited = any(x in err for x in ("429", "rate", "quota", "resource_exhausted"))
            if rate_limited and attempt < 3:
                wait = 2**attempt
                logfire.warning(f"Gemini rate limit — retrying in {wait}s ({attempt + 1}/4).")
                time.sleep(wait)
            else:
                logfire.error(f"Gemini embedding failed: {e}")
                raise
    raise RuntimeError("unreachable")


def embed_query(query: str) -> list[float]:
    _init()
    if settings.EMBEDDING_PROVIDER.lower() == "local":
        return _model.encode([query])[0].tolist()
    return _model.embed_query(query)


def embed_texts(texts: list[str]) -> list[list[float]]:
    _init()
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        with logfire.span("Embed batch", provider=settings.EMBEDDING_PROVIDER, start=i, size=len(batch)):
            out.extend(_embed_batch(batch))
    return out
