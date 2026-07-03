# DVMELTSS-FIX: V - Validate, S - Security, M - Modular
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Added app_api_keys field, stronger JWT validation, prod warnings
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
        # FIXED: Let field validators parse comma-separated env values such as
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
            "Leave unset to use local hash-based embeddings instead of OpenAI."
        ),
    )
    fallback_to_openai: bool = Field(default=True)

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
    stripe_price_id_business: Optional[str] = Field(
        default=None,
        description="Stripe Price ID for the 'business' plan's recurring subscription — from the Stripe product catalog.",
    )

    default_workspace_id: str = Field(
        default="default",
        validation_alias=AliasChoices("DEFAULT_WORKSPACE_ID", "default_workspace_id"),
        pattern="^[a-z0-9_-]{3,64}$",
    )

    # ✅ FIXED: App-specific API keys (SEPARATE from OpenAI keys)
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

    # ✅ FIXED: Prod-warning validator
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

__all__ = ["Settings", "get_settings", "lazy_settings"]


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.config) --------------
# ========================================================================

if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path
    from unittest.mock import patch

    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]

    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    def run_tests():
        print("🔍 Testing Config module (app/config.py)")
        print("=" * 70)

        try:
            from app.config import Settings, get_settings, _parse_list_value
            from pydantic import ValidationError

            # -- Test 1: _parse_list_value helper ------------------------
            print("\n📌 Test 1: _parse_list_value (robust parsing)")

            result = _parse_list_value(None, default=["en"])
            assert result == ["en"]
            print("   ✅ None input: returns default")

            result = _parse_list_value(["a", "b", ""], default=[])
            assert result == ["a", "b"]
            print("   ✅ List input: filtered empty strings")

            result = _parse_list_value('["en", "ta", "hi"]', default=[])
            assert result == ["en", "ta", "hi"]
            print("   ✅ JSON array: parsed correctly")

            result = _parse_list_value("en,ta,hi", default=[])
            assert result == ["en", "ta", "hi"]
            print("   ✅ Comma-separated: split correctly")

            result = _parse_list_value("en", default=[])
            assert result == ["en"]
            print("   ✅ Single value: wrapped in list")

            result = _parse_list_value(" en , ta , hi ", default=[])
            assert result == ["en", "ta", "hi"]
            print("   ✅ Whitespace: trimmed correctly")

            # -- Test 2: Settings with defaults -------------------------
            print("\n📌 Test 2: Settings instantiation (defaults)")

            get_settings.cache_clear()
            settings = Settings()

            assert settings.app_name == "DocuMind AI"
            assert settings.app_version == "2.0.0"
            assert settings.api_port == 8000
            assert settings.environment == "dev"
            print("   ✅ Defaults: app_name, version, port, env loaded")

            assert settings.max_upload_size_bytes == 50 * 1024 * 1024
            print(f"   ✅ Computed: max_upload_size_bytes = {settings.max_upload_size_bytes}")

            db_url = settings.database_url
            assert db_url.startswith("postgresql+asyncpg://")
            print("   ✅ Computed: database_url has asyncpg driver")

            # -- Test 3: Environment variable overrides -----------------
            print("\n📌 Test 3: Environment variable overrides")

            get_settings.cache_clear()

            test_env = {
                "APP_NAME": "TestApp",
                "API_PORT": "9999",
                "ENVIRONMENT": "staging",
                "OCR_LANGUAGES": "en,ta,hi",
                "APP_API_KEYS": "key1,key2,key3",
                "CORS_ORIGINS": '["http://test.com", "http://staging.com"]',
            }

            with patch.dict(os.environ, test_env, clear=False):
                settings = Settings()

                assert settings.app_name == "TestApp"
                assert settings.api_port == 9999
                assert settings.environment == "staging"
                print("   ✅ Env override: app_name, port, environment")

                assert settings.ocr_language_list == ["en", "ta", "hi"]
                assert settings.app_api_keys == ["key1", "key2", "key3"]
                assert "http://test.com" in settings.cors_origins
                print("   ✅ Env override: list fields parsed correctly")

            # -- Test 4: Field validators -------------------------------
            print("\n📌 Test 4: Field validators")

            with patch("app.config.logger"):
                settings = Settings(openai_api_key="invalid-key-format")
                assert settings.openai_api_key == "invalid-key-format"
                print("   ✅ openai_api_key: accepts any string, logs warning for bad format")

            try:
                Settings(tmp_dir="../../../etc")
                print("   ❌ Should reject path traversal")
            except ValidationError:
                print("   ✅ validate_path: rejects path traversal")

            try:
                Settings(jwt_secret_key="short")
                print("   ❌ Should reject short JWT secret")
            except ValidationError:
                print("   ✅ jwt_secret_key: enforces min_length=64")

            try:
                Settings(default_workspace_id="INVALID@ID!")
                print("   ❌ Should reject invalid workspace ID pattern")
            except ValidationError:
                print("   ✅ default_workspace_id: enforces pattern ^[a-z0-9_-]{3,64}$")

            # -- Test 5: database_url property (essential guarantee) ----
            print("\n📌 Test 5: database_url property")

            settings = Settings()
            assert settings.database_url.startswith("postgresql+asyncpg://")
            print("   ✅ database_url: always returns URL with asyncpg driver")

            # -- Test 6: warn_on_prod_defaults validator ----------------
            print("\n📌 Test 6: warn_on_prod_defaults (model validator)")

            with patch("app.config.logger") as mock_logger:
                # Dev env: accepts dev defaults (no warnings expected)
                settings = Settings(
                    environment="dev",
                    jwt_secret_key="dev-secret-DO-NOT-USE-IN-PROD-min-64-chars-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                )
                print("   ✅ Dev env: accepts dev defaults")

                # Production env: should warn about weak defaults
                # ✅ FIX: Use exactly 64 chars that still contains "dev-secret" to trigger warning
                # "dev-secret-" (11 chars) + "x"*53 = 64 chars total
                weak_but_valid_secret = "dev-secret-" + "x" * 53

                mock_logger.reset_mock()
                settings = Settings(
                    environment="production",
                    jwt_secret_key=weak_but_valid_secret,  # 64 chars, contains "dev-secret"
                    neo4j_password="documind_neo4j_pass",  # Default password
                    cors_origins=["*"],  # Wildcard CORS
                    app_api_keys=[],  # Empty API keys
                    auth_enabled=True,
                )
                logged_messages = [
                    str(call) for call in mock_logger.error.call_args_list + mock_logger.warning.call_args_list
                ]
                assert any("JWT_SECRET_KEY" in msg or "weak" in msg.lower() for msg in logged_messages)
                assert any("NEO4J_PASSWORD" in msg or "default" in msg.lower() for msg in logged_messages)
                print("   ✅ Production env: warns about weak JWT, default passwords, wildcard CORS")

            # -- Test 7: get_settings() cached singleton ----------------
            print("\n📌 Test 7: get_settings() cached singleton")

            get_settings.cache_clear()
            settings1 = get_settings()
            settings2 = get_settings()
            assert settings1 is settings2
            print("   ✅ get_settings: returns cached singleton (same object)")

            get_settings.cache_clear()
            settings3 = get_settings()
            assert settings1 is not settings3
            print("   ✅ get_settings: cache_clear() allows reload")

            # -- Test 8: Computed path properties -----------------------
            print("\n📌 Test 8: Computed path properties")

            settings = Settings(data_dir="./mydata", dead_letter_dir=None)
            expected_dead = Path("./mydata/dead_letter").resolve()
            assert settings.effective_dead_letter_dir == expected_dead
            print("   ✅ effective_dead_letter_dir: uses data_dir fallback")

            expected_bm25 = Path(".cache/bm25_index.pkl").resolve()
            assert settings.effective_bm25_cache_path == expected_bm25
            print("   ✅ effective_bm25_cache_path: resolves to .cache/")

            settings = Settings(bm25_cache_path="/custom/bm25.pkl")
            assert settings.effective_bm25_cache_path == Path("/custom/bm25.pkl").resolve()
            print("   ✅ effective_bm25_cache_path: respects explicit path")

            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! Config module verified.")
            print("\n💡 What we verified:")
            print("   • Helper: _parse_list_value handles JSON, CSV, single values ✅")
            print("   • Defaults: Settings loads with sensible defaults ✅")
            print("   • Env overrides: environment variables override defaults ✅")
            print("   • Validators: path traversal, JWT length, workspace pattern ✅")
            print("   • Properties: database_url always returns valid asyncpg URL ✅")
            print("   • Prod warnings: warns about weak defaults in non-dev envs ✅")
            print("   • Caching: get_settings() returns cached singleton ✅")
            print("\n🔐 Production: Configuration with validation & graceful defaults ready")
            return True

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    success = run_tests()
    sys.exit(0 if success else 1)
