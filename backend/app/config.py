from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from typing import Optional, List, Any
from pydantic import Field, field_validator, AliasChoices, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)


def _parse_list_value(v: Any, default: List[str]) -> List[str]:
    """
    Robust parser for List[str] fields from .env.
    Handles: JSON arrays, comma-separated strings, plain strings, or None.
    """
    if v is None:
        return default
    if isinstance(v, list):
        return [str(item).strip() for item in v if str(item).strip()]
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return default
        # Try JSON parse first (for ["a","b"] format)
        if v.startswith("[") or v.startswith("{"):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
        # Fallback: comma-separated or single value
        return [item.strip() for item in v.split(",") if item.strip()]
    return default


class Settings(BaseSettings):
    """
    Application configuration — reads from backend/.env then environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # APP_API_KEYS=key1,key2 instead of requiring JSON arrays in every env.
        enable_decoding=False,
    )

    # -- App Identity -------------------------------------------
    app_name: str = Field(default="DocuMind AI")
    app_version: str = Field(default="2.0.0")
    api_reload: bool = Field(default=False)
    eager_startup_services: bool = Field(
        default=False,
        validation_alias=AliasChoices("EAGER_STARTUP_SERVICES", "eager_startup_services"),
        description="Initialize OCR/vector/RAG/graph services during startup instead of lazy-loading them.",
    )
    environment: str = Field(default="dev", description="dev | staging | production")

    # -- Server -------------------------------------------------
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000, ge=1, le=65535)

    cors_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
        description='Allowed CORS origins. NEVER use ["*"] with allow_credentials=True.',
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        return _parse_list_value(v, default=["http://localhost:3000", "http://127.0.0.1:3000"])

    # -- OpenAI (or any OpenAI-compatible chat endpoint, e.g. Groq) ----
    openai_api_key: Optional[str] = Field(default=None)
    openai_chat_model: str = Field(default="gpt-4o")
    openai_embedding_model: str = Field(default="text-embedding-3-large")
    openai_base_url: Optional[str] = Field(
        default=None,
        description=(
            "Override base URL to point ChatOpenAI at an OpenAI-compatible provider "
            "(e.g. Groq: https://api.groq.com/openai/v1). Leave unset for real OpenAI."
        ),
    )
    embedding_api_key: Optional[str] = Field(
        default=None,
        description=(
            "Separate real-OpenAI key for embeddings, used when OPENAI_API_KEY/OPENAI_BASE_URL "
            "point chat at a non-OpenAI provider (e.g. Groq has no embeddings API). "
            "Leave unset to use local sentence-transformers embeddings instead of OpenAI."
        ),
    )
    fallback_to_openai: bool = Field(default=True)

    # -- Groq (free cloud LLM fallback when Ollama is unavailable) -----
    groq_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "groq_api_key"),
        description="Groq API key — used as cloud LLM fallback when Ollama is down. Free tier available.",
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq model to use as LLM fallback (llama-3.3-70b-versatile or mixtral-8x7b-32768).",
    )

    # -- Google Gemini (free tier: 1M context, 15 RPM, 1500 req/day) ------
    gemini_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "gemini_api_key"),
        description="Google AI Studio API key — Gemini 2.0 Flash is free, fast, 1M token context.",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        validation_alias=AliasChoices("GEMINI_MODEL", "gemini_model"),
        description="Gemini model to use (default: gemini-2.0-flash — free, 1M context).",
    )

    # -- OpenRouter (free cloud LLM — 16+ free models, OpenAI-compatible) -
    openrouter_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "openrouter_api_key"),
        description="OpenRouter API key (sk-or-v1-...) — gives access to 16+ free models via OpenAI-compatible API.",
    )
    openrouter_model: str = Field(
        default="nvidia/nemotron-ultra-253b-v1:free",
        validation_alias=AliasChoices("OPENROUTER_MODEL", "openrouter_model"),
        description="OpenRouter model ID to use (default: NVIDIA Nemotron Ultra 253B, free tier).",
    )

    # -- Voyage AI (free cloud embedding fallback) ---------------------
    voyage_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("VOYAGE_API_KEY", "voyage_api_key"),
        description="Voyage AI key — used as embedding fallback when local sentence-transformers fail. 50M free tokens.",
    )
    voyage_model: str = Field(default="voyage-3-lite", description="Voyage AI embedding model.")

    # -- Mistral (free cloud OCR fallback) ----------------------------
    mistral_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MISTRAL_API_KEY", "mistral_api_key"),
        description="Mistral API key — used as OCR fallback when PaddleOCR fails. Free tier available.",
    )

    # -- Embedding provider priority -----------------------------------
    embedding_provider: str = Field(
        default="local",
        description="Primary embedding source: 'local' (sentence-transformers) | 'openai' | 'voyage'.",
    )
    local_embedding_model: str = Field(
        default="all-mpnet-base-v2",
        description="sentence-transformers model for local embeddings (768-dim, no API cost).",
    )
    embedding_dimensions: int = Field(
        default=768,
        description="Output embedding dimensions. 768 = local/voyage-3-lite compatible. 3072 = OpenAI large.",
    )

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_key(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("sk-"):
            logger.info(
                "OPENAI_API_KEY doesn't start with 'sk-' — assuming an OpenAI-compatible "
                "provider (e.g. Groq) via OPENAI_BASE_URL."
            )
        return v

    # -- Ollama -------------------------------------------------
    llm_provider: str = Field(default="ollama")
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="llama3.2:7b")
    ollama_temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    ollama_code_model: Optional[str] = Field(default=None)

    # -- OCR ----------------------------------------------------
    ocr_language_list: List[str] = Field(
        default_factory=lambda: ["en"],
        validation_alias=AliasChoices("OCR_LANGUAGES", "OCR_LANGUAGE_LIST", "ocr_language_list"),
    )

    @field_validator("ocr_language_list", mode="before")
    @classmethod
    def parse_ocr_languages(cls, v):
        return _parse_list_value(v, default=["en"])

    ocr_confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    ocr_use_gpu: bool = Field(default=False)

    # -- Vector Stores ------------------------------------------
    chroma_persist_dir: str = Field(default="./data/chroma")
    chroma_collection_name: str = Field(default="documind_chunks")
    faiss_index_path: str = Field(default="./data/faiss/index.bin")
    bm25_cache_path: Optional[str] = Field(default=None)
    vectorstore_max_workers: int = Field(default=2, ge=1, le=8)

    # -- RAG ----------------------------------------------------
    rag_chunk_size_child: int = Field(default=400, ge=100, le=2000)
    rag_chunk_overlap_child: int = Field(default=50, ge=0)
    rag_chunk_size_parent: int = Field(default=2000, ge=500, le=5000)
    rag_chunk_overlap_parent: int = Field(default=200, ge=0)
    rag_top_k_retrieval: int = Field(default=20, ge=1)
    rag_top_k_rerank: int = Field(default=3, ge=1)
    # Cross-encoder reranking. Disable on low-RAM hosts (e.g. Render free 512MB)
    # to avoid loading PyTorch + sentence-transformers (~1GB). When disabled,
    # retrieval (vector + BM25 RRF) scores are used directly — slightly lower
    # precision but the full RAG/Agent/Graph pipeline still works.
    rerank_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("RERANK_ENABLED", "rerank_enabled"),
    )

    # -- Observability ------------------------------------------
    langchain_api_key: Optional[str] = Field(default=None)
    langchain_endpoint: str = Field(default="https://api.smith.langchain.com")
    langchain_project: str = Field(default="documind-ai")
    langchain_dataset_name: Optional[str] = Field(default=None)
    mlflow_tracking_uri: str = Field(default="./data/mlflow")
    mlflow_experiment_name: str = Field(default="documind-rag")

    # -- File Handling ------------------------------------------
    max_upload_size_mb: int = Field(default=50, ge=1, le=500)
    tmp_dir: str = Field(default="./tmp")
    data_dir: str = Field(default="./data")
    dead_letter_dir: Optional[str] = Field(default=None)

    # -- Neo4j --------------------------------------------------
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_username: str = Field(default="neo4j")
    neo4j_password: str = Field(default="documind_neo4j_pass")
    neo4j_database: str = Field(default="neo4j")
    graph_extraction_enabled: bool = Field(default=True)
    graph_max_triplets_per_chunk: int = Field(default=15, ge=1, le=50)

    # -- Redis --------------------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379/2",
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
    )
    celery_broker_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CELERY_BROKER_URL", "celery_broker_url"),
    )
    cache_embed_ttl_seconds: int = Field(default=7200)
    cache_result_ttl_seconds: int = Field(default=1800)

    # -- PostgreSQL ---------------------------------------------
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = Field(default="documind")
    postgres_password: str = Field(default="changeme_in_production")
    postgres_db: str = Field(default="documind")

    database_url_override: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )

    @property
    def database_url(self) -> str:
        raw = self.database_url_override
        if raw:
            if "+asyncpg" not in raw:
                raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
                    "postgres://", "postgresql+asyncpg://", 1
                )
            return raw
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    # -- Auth ---------------------------------------------------
    auth_enabled: bool = Field(default=True, validation_alias=AliasChoices("AUTH_ENABLED", "auth_enabled"))
    allow_self_registration: bool = Field(
        default=True,
        validation_alias=AliasChoices("ALLOW_SELF_REGISTRATION", "allow_self_registration"),
    )
    skip_email_verification: bool = Field(
        default=False,
        validation_alias=AliasChoices("SKIP_EMAIL_VERIFICATION", "skip_email_verification"),
    )
    frontend_url: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("FRONTEND_URL", "frontend_url"),
    )

    jwt_secret_key: str = Field(
        default="dev-secret-DO-NOT-USE-IN-PROD-min-64-chars-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        validation_alias=AliasChoices("JWT_SECRET_KEY", "jwt_secret_key"),
        min_length=64,  # ✅ FIXED: Enforce stronger minimum
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=60, ge=5, le=1440)
    jwt_refresh_token_expire_days: int = Field(default=30, ge=1, le=90)

    # RS256 asymmetric key pair (preferred for production microservices).
    # When both are set, RS256 is used regardless of jwt_algorithm.
    # Generate with: openssl genrsa -out private.pem 4096
    #                openssl rsa -in private.pem -pubout -out public.pem
    # Set as PEM strings (with \n escaped) in JWT_PRIVATE_KEY / JWT_PUBLIC_KEY env vars.
    jwt_private_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("JWT_PRIVATE_KEY", "jwt_private_key"),
    )
    jwt_public_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("JWT_PUBLIC_KEY", "jwt_public_key"),
    )

    encryption_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ENCRYPTION_KEY", "encryption_key"),
        description=(
            "Fernet key (32-byte urlsafe-base64) for encrypting secrets at rest, e.g. "
            "per-workspace BYOK LLM API keys. Generate with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ),
    )

    # -- Stripe billing ------------------------------------------
    stripe_secret_key: Optional[str] = Field(
        default=None,
        description="Stripe secret key (sk_test_... / sk_live_...) — from the Stripe dashboard API keys page.",
    )
    stripe_publishable_key: Optional[str] = Field(
        default=None,
        description="Stripe publishable key (pk_test_... / pk_live_...) — safe to expose to the frontend.",
    )
    stripe_webhook_secret: Optional[str] = Field(
        default=None,
        description="Stripe webhook signing secret (whsec_...) — from the webhook endpoint's settings page.",
    )
    # Stripe price IDs per plan (create Products + Prices in Stripe dashboard, paste IDs here)
    stripe_price_id_starter: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("STRIPE_PRICE_ID_STARTER", "stripe_price_id_starter"),
        description="Stripe Price ID for the 'starter' plan ($29/mo).",
    )
    stripe_price_id_pro: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("STRIPE_PRICE_ID_PRO", "stripe_price_id_pro"),
        description="Stripe Price ID for the 'pro' plan ($79/mo).",
    )
    # Legacy alias kept for backward compat — maps to pro
    stripe_price_id_business: Optional[str] = Field(
        default=None,
        description="[Deprecated] use STRIPE_PRICE_ID_PRO instead.",
    )

    # Razorpay (India / INR payments)
    razorpay_key_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RAZORPAY_KEY_ID", "razorpay_key_id"),
        description="Razorpay Key ID (rzp_test_... / rzp_live_...) — from Razorpay dashboard.",
    )
    razorpay_key_secret: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RAZORPAY_KEY_SECRET", "razorpay_key_secret"),
        description="Razorpay Key Secret — from Razorpay dashboard.",
    )
    razorpay_webhook_secret: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RAZORPAY_WEBHOOK_SECRET", "razorpay_webhook_secret"),
        description="Razorpay webhook secret for signature verification.",
    )
    # Razorpay Plan IDs (create Subscriptions Plans in Razorpay dashboard)
    razorpay_plan_id_starter: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RAZORPAY_PLAN_ID_STARTER", "razorpay_plan_id_starter"),
    )
    razorpay_plan_id_pro: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RAZORPAY_PLAN_ID_PRO", "razorpay_plan_id_pro"),
    )

    default_workspace_id: str = Field(
        default="default",
        validation_alias=AliasChoices("DEFAULT_WORKSPACE_ID", "default_workspace_id"),
        pattern="^[a-z0-9_-]{3,64}$",
    )

    app_api_keys: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("APP_API_KEYS", "app_api_keys"),
        description="Comma-separated list of valid API keys for app authentication",
    )

    @field_validator("app_api_keys", mode="before")
    @classmethod
    def parse_app_api_keys(cls, v):
        return _parse_list_value(v, default=[])

    # -- Alerting -----------------------------------------------
    alert_smtp_host: Optional[str] = Field(default=None)
    alert_smtp_user: Optional[str] = Field(default=None)
    alert_smtp_pass: Optional[str] = Field(default=None)
    alert_email_to: Optional[str] = Field(default=None)

    # -- Audio / Whisper ----------------------------------------
    whisper_model: Optional[str] = Field(default=None)
    whisper_language: Optional[str] = Field(default=None)
    enable_speaker_diarization: bool = Field(default=False)
    huggingface_token: Optional[str] = Field(default=None)
    hf_username: Optional[str] = Field(default=None)
    handwriting_confidence_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    trocr_model_path: Optional[str] = Field(default=None)
    max_audio_size_mb: int = Field(default=100, ge=1)
    supported_audio_formats: str = Field(default="mp3,mp4,wav,m4a,ogg,webm")
    supported_doc_formats: str = Field(default="docx,xlsx,pdf,pptx,txt,md")

    # -- Logging ------------------------------------------------
    log_level: str = Field(default="INFO")

    # -- Computed Properties ------------------------------------
    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def effective_dead_letter_dir(self) -> Path:
        return Path(self.dead_letter_dir or Path(self.data_dir) / "dead_letter").resolve()

    @property
    def effective_bm25_cache_path(self) -> Path:
        return Path(self.bm25_cache_path or ".cache/bm25_index.pkl").resolve()

    @property
    def effective_embedding_api_key(self) -> Optional[str]:
        """
        Key to use for OpenAI embeddings, independent of the chat LLM provider.

        - EMBEDDING_API_KEY set → use it explicitly.
        - No OPENAI_BASE_URL (real OpenAI for chat too) → reuse OPENAI_API_KEY (unchanged
          behavior for existing setups).
        - OPENAI_BASE_URL set (chat routed at Groq/etc, whose keys aren't valid for the
          real OpenAI embeddings API) → return None so embeddings fall back to the local
          hash-based embedder instead of failing auth against OpenAI.
        """
        if self.embedding_api_key:
            return self.embedding_api_key
        if self.openai_base_url:
            return None
        return self.openai_api_key

    @field_validator("tmp_dir", "data_dir", "chroma_persist_dir")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in str(Path(v)):
            raise ValueError(f"Path traversal not allowed: {v}")
        return str(Path(v))

    @model_validator(mode="after")
    def warn_on_prod_defaults(self) -> "Settings":
        """Warn if dangerous defaults are used in non-dev environments."""
        if self.environment != "dev":
            if "*" in self.cors_origins and len(self.cors_origins) == 1:
                logger.error(
                    "🔴 CORS_ORIGINS=['*'] with allow_credentials=True is INVALID - restrict to specific domains"
                )
            if "dev-secret" in self.jwt_secret_key or len(self.jwt_secret_key) < 64:
                logger.error("🔴 JWT_SECRET_KEY is weak or contains dev placeholder - SET A STRONG 64+ CHAR SECRET")
            if self.neo4j_password == "documind_neo4j_pass":
                logger.warning("⚠️ NEO4J_PASSWORD is default - change in production")
            if not self.app_api_keys and self.auth_enabled:
                logger.warning("⚠️ APP_API_KEYS is empty - external API access will fail")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton — reads .env once at first call."""
    return Settings()


class _LazySettings:
    """
    Lazy proxy for settings — forwards attribute access to get_settings() at call time.

    [OK] FIXED: Replaces the pattern `settings = get_settings()` at module level.
    Module-level calls crash at import time when env vars aren't configured (tests/CI).

    Usage (in any module):
        from app.config import get_settings, lazy_settings as settings
        # settings.X now resolves lazily — no import-time get_settings() call.
    """

    __slots__ = ()

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)

    def __repr__(self) -> str:
        return f"_LazySettings(proxy -> {get_settings()!r})"


# Module-level singleton — import once, use everywhere.
lazy_settings = _LazySettings()


def has_llm_configured() -> bool:
    """Return True if any LLM provider key is configured (Gemini, Groq, OpenRouter, OpenAI, or Ollama)."""
    s = get_settings()
    provider = getattr(s, "llm_provider", "openai")
    if provider == "ollama":
        return True  # Ollama needs no key; connectivity checked at call time
    if provider == "gemini":
        return bool(getattr(s, "gemini_api_key", None))
    # openai provider — accept any of: openai / gemini / groq / openrouter
    return bool(
        getattr(s, "openai_api_key", None)
        or getattr(s, "gemini_api_key", None)
        or getattr(s, "groq_api_key", None)
        or getattr(s, "openrouter_api_key", None)
    )


__all__ = ["Settings", "get_settings", "lazy_settings", "has_llm_configured"]


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.config) --------------
# ========================================================================

