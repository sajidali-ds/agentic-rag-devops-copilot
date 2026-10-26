import os

from dotenv import load_dotenv
load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- Secrets / endpoints ---------------------------------------------------
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_FALLBACK_API_KEY = os.getenv("GROQ_FALLBACK_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")
    PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY")
    QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None  # None => unauthenticated (local Docker)
    QDRANT_CLUSTER_ENDPOINT = os.getenv("QDRANT_CLUSTER_ENDPOINT", "http://localhost:6333")
    QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "enterprise-rag")

    # --- LLM gateway (Portkey) ---------------------------------------------------
    GROQ_SLUG = os.getenv("GROQ_SLUG", "rag-enterprise-openrouter")  # primary integration slug
    GROQ_SLUG_2 = os.getenv("GROQ_SLUG_2", "marthala-groq-2")  # fallback integration slug
    LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")  # model id behind the primary slug
    LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "google/gemma-4-31b-it:free")
    # Model tiering: a small/cheap model does routing, tool decisions and verification;
    # the stronger LLM_MODEL writes the final answer. Defaults to the same model.
    LLM_FAST_MODEL = os.getenv("LLM_FAST_MODEL") or os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")
    PORTKEY_CONFIG_ID = os.getenv("PORTKEY_CONFIG_ID")  # optional saved gateway config
    APP_ENV = os.getenv("APP_ENV", "dev")

    # --- Embeddings --------------------------------------------------------------
    # ONE provider is used for BOTH ingestion and querying. Mixing providers
    # (different vector sizes / spaces) makes retrieval silently return nothing.
    EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")  # "local" | "gemini"
    LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
    GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-2-preview")

    # --- Chunking ----------------------------------------------------------------
    CHUNK_SIZE = _int("CHUNK_SIZE", 1000)  # characters
    CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 150)  # characters

    # --- Retrieval ---------------------------------------------------------------
    RETRIEVE_TOP_N = _int("RETRIEVE_TOP_N", 15)
    RERANK_TOP_K = _int("RERANK_TOP_K", 5)
    MAX_CONTEXT_CHARS = _int("MAX_CONTEXT_CHARS", 25000)

    # --- Domain / guardrails -----------------------------------------------------
    DOMAIN_DESCRIPTION = os.getenv("DOMAIN_DESCRIPTION", "Kubernetes, Intel hardware, and networking")
    # Fail-closed by default: if the guardrail layer errors, the request is blocked.
    GUARDRAILS_FAIL_OPEN = _bool("GUARDRAILS_FAIL_OPEN", False)

    # --- API hardening -----------------------------------------------------------
    API_KEY = os.getenv("API_KEY")  # if set, /query requires header X-API-Key
    RATE_LIMIT_PER_MINUTE = _int("RATE_LIMIT_PER_MINUTE", 0)  # 0 = disabled
    MAX_QUERY_CHARS = _int("MAX_QUERY_CHARS", 2000)
    CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

    # --- Agent --------------------------------------------------------------------
    AGENT_MAX_STEPS = _int("AGENT_MAX_STEPS", 5)  # search/tool iterations before forced answer
    AGENT_OBSERVATION_CHARS = _int("AGENT_OBSERVATION_CHARS", 1500)
    ENABLE_VERIFY = _bool("ENABLE_VERIFY", True)  # groundedness check (+1 retry) on technical answers
    PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v2")  # bump to invalidate cached answers

    # --- Caching (Redis; falls back to per-process memory if REDIS_URL unset/unreachable) ---
    REDIS_URL = os.getenv("REDIS_URL")
    CACHE_ENABLED = _bool("CACHE_ENABLED", True)
    EXACT_CACHE_TTL = _int("EXACT_CACHE_TTL", 86400)
    SEMANTIC_CACHE_ENABLED = _bool("SEMANTIC_CACHE_ENABLED", True)
    SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_THRESHOLD", "0.92"))
    SEMANTIC_CACHE_MAX = _int("SEMANTIC_CACHE_MAX", 1000)
    RETRIEVAL_CACHE_TTL = _int("RETRIEVAL_CACHE_TTL", 3600)
    CONVERSATION_TTL = _int("CONVERSATION_TTL", 86400)
    CONVERSATION_MAX_MESSAGES = _int("CONVERSATION_MAX_MESSAGES", 12)

    # --- Observability (Langfuse is optional; the SDK reads LANGFUSE_* from the env) ---
    LANGFUSE_ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
    FEEDBACK_LOG_PATH = os.getenv("FEEDBACK_LOG_PATH", "feedback.jsonl")

    @property
    def primary_model(self) -> str:
        return f"@{self.GROQ_SLUG}/{self.LLM_MODEL}"

    @property
    def fast_model(self) -> str:
        return f"@{self.GROQ_SLUG}/{self.LLM_FAST_MODEL}"

    @property
    def fallback_model(self) -> str:
        return f"@{self.GROQ_SLUG_2}/{self.LLM_FALLBACK_MODEL}"


settings = Settings()
