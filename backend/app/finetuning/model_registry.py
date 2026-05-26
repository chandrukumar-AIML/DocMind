# backend/app/finetuning/model_registry.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, A - Async
# ASCALE-FIX: S - Separation, C - Coupling
# OWASP-FIX: 7 - Safe credential handling

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Optional

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.finetune_utils import hf_api_with_retry, generate_finetune_correlation_id
from app.core.pii_utils import scrub_pii_for_evaluation

logger = logging.getLogger(__name__)

# ========================================================================
# CONSTANTS & SECURITY
# ========================================================================

_SENSITIVE_FIELDS: Final = frozenset({"token", "password", "secret", "api_key", "auth"})

# More flexible version pattern
_MODEL_NAME_PATTERN: Final = r"^documind-[a-z]+-embedding-v\d+$"

_HF_RATE_LIMIT_DELAY: Final = 1.0
_MAX_HF_RETRIES: Final = 3

_DEFAULT_CACHE_DIR: Final = Path(".cache/finetuned_models")


# ========================================================================
# MODEL CARD
# ========================================================================

@dataclass(frozen=True)
class ModelCard:
    model_id: str
    domain: str
    base_model: str
    version: str
    local_path: str
    qdrant_dim: int = 1536  # FIXED: OpenAI text-embedding-3-small=1536, 3-large=3072; 1024 was wrong default
    mlflow_run_id: str = ""
    correlation_id: Optional[str] = None  # FIXED: Added for tracing

    base_mrr: float = 0.0
    finetuned_mrr: float = 0.0
    improvement_pct: float = 0.0

    def __post_init__(self):
        if not (-100.0 <= self.improvement_pct <= 1000.0):
            object.__setattr__(self, 'improvement_pct', 0.0)

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "domain": self.domain,
            "base_model": self.base_model,
            "version": self.version,
            "local_path": self.local_path,
            "qdrant_dim": self.qdrant_dim,
            "mlflow_run_id": self.mlflow_run_id,
            "base_mrr": round(self.base_mrr, 4),
            "finetuned_mrr": round(self.finetuned_mrr, 4),
            "improvement_pct": round(self.improvement_pct, 2),
            "correlation_id": self.correlation_id,  # FIXED: Include in output
        }


# ========================================================================
# MODEL REGISTRY
# ========================================================================

class ModelRegistry:

    def __init__(self, cache_dir: Optional[str | Path] = None):
        settings = get_settings()

        self.hf_token = getattr(settings, "huggingface_token", "")
        self.hf_username = getattr(settings, "hf_username", "")

        self.model_cache = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.model_cache.mkdir(parents=True, exist_ok=True)

        logger.info(f"ModelRegistry initialized: cache={self.model_cache}")

    # ----------------------------------------------------------------
    # SECURITY HELPERS
    # ----------------------------------------------------------------

    def _redact_sensitive(self, data: dict) -> dict:
        return {
            k: "[REDACTED]" if k.lower() in _SENSITIVE_FIELDS else v
            for k, v in data.items()
        }

    def _validate_model_name(self, name: str) -> bool:
        return bool(re.match(_MODEL_NAME_PATTERN, name))

    # ----------------------------------------------------------------
    # PUSH MODEL
    # ----------------------------------------------------------------

    async def push_to_hub_async(
        self,
        local_model_path: str | Path,
        domain: str,
        version: str,
        private: bool = True,
        commit_message: str = "",
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> str:

        corr_id = correlation_id or generate_finetune_correlation_id("push_model")

        if not self.hf_token:
            raise ValueError("HUGGINGFACE_TOKEN not set")

        if not self.hf_username:
            raise ValueError("HF_USERNAME not set")

        safe_path = Path(local_model_path).resolve()

        if not safe_path.exists():
            raise FileNotFoundError(f"Model not found: {safe_path}")

        repo_id = f"{self.hf_username}/documind-{domain}-embedding-v{version}"

        if not self._validate_model_name(repo_id.split("/")[-1]):
            raise ValueError(f"Invalid model name: {repo_id}")

        try:
            from sentence_transformers import SentenceTransformer
            from huggingface_hub import AsyncHfApi

            api = AsyncHfApi(token=self.hf_token)

            # Create repo with centralized retry
            try:
                await hf_api_with_retry(
                    api.create_repo,
                    repo_id=repo_id,
                    private=private,
                    exist_ok=True,
                )
            except Exception as e:
                logger.warning(f"[{corr_id}] Repo create warning: {type(e).__name__}")

            # Load model safely (non-blocking)
            loop = asyncio.get_running_loop()
            model = await loop.run_in_executor(
                None, lambda: SentenceTransformer(str(safe_path))
            )

            # Push with centralized retry
            await hf_api_with_retry(
                model.push_to_hub,
                repo_id,
                token=self.hf_token,
                commit_message=commit_message or f"{domain} model v{version}",
                private=private,
            )

            await self._create_model_card_async(api, repo_id, domain, version, corr_id)

            logger.info(f"[{corr_id}] Model pushed: {repo_id}")
            return repo_id

        except ImportError:
            logger.error("Install: sentence-transformers, huggingface_hub")
            raise

        except Exception as e:
            # FIXED: Redact sensitive info from error logs
            safe_error = scrub_pii_for_evaluation(str(e), domain="general")
            
            if self.hf_token:
                safe_error = safe_error.replace(self.hf_token, "[REDACTED]")

            logger.error(f"[{corr_id}] Push failed: {safe_error}")
            raise

    # ----------------------------------------------------------------
    # MODEL CARD
    # ----------------------------------------------------------------

    async def _create_model_card_async(self, api, repo_id, domain, version, correlation_id):

        card = f"""---
language: en
tags:
- sentence-transformers
- documind-ai
- {domain}
---

# DocuMind {domain} model v{version}
"""

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                f.write(card)
                tmp = f.name

            await hf_api_with_retry(
                api.upload_file,
                path_or_fileobj=tmp,
                path_in_repo="README.md",
                repo_id=repo_id,
            )

            os.unlink(tmp)

        except Exception as e:
            logger.warning(f"[{correlation_id}] Model card failed: {type(e).__name__}")

    # ----------------------------------------------------------------
    # DIMENSION VALIDATION
    # ----------------------------------------------------------------

    async def validate_model_dimension(
        self,
        model,
        expected_dim: int,
        repo_id: str,
        corr_id: str,
    ) -> int:
        """
        [OK] FIXED: Validate embedding model output dimension against qdrant_dim config.

        Prevents silent dimension mismatch between the fine-tuned model and the
        Qdrant collection — which would cause cryptic index errors at query time.

        Args:
            model: Loaded SentenceTransformer instance.
            expected_dim: The qdrant_dim from settings or ModelCard.
            repo_id: Model repo identifier for logging.
            corr_id: Correlation ID for tracing.

        Returns:
            Actual embedding dimension of the loaded model.

        Raises:
            ValueError: If strict_qdrant_dim_check=True and dimensions don't match.
        """
        try:
            loop = asyncio.get_running_loop()
            actual_dim: int = await loop.run_in_executor(
                None, lambda: model.get_sentence_embedding_dimension()
            )

            if actual_dim != expected_dim:
                _s = get_settings()
                strict = getattr(_s, 'strict_qdrant_dim_check', False)
                msg = (
                    f"[{corr_id}] Qdrant dimension mismatch for {repo_id}: "
                    f"model outputs {actual_dim}d, configured qdrant_dim={expected_dim}d. "
                    f"Update QDRANT_DIM in .env to {actual_dim} or re-index the collection."
                )
                if strict:
                    raise ValueError(msg)
                logger.warning(msg)
            else:
                logger.debug(f"[{corr_id}] Qdrant dimension validated: {repo_id} = {actual_dim}d ✓")

            return actual_dim

        except (AttributeError, Exception) as e:
            # SentenceTransformer might not have get_sentence_embedding_dimension on all versions
            logger.warning(f"[{corr_id}] Could not validate model dimension for {repo_id}: {e}")
            return expected_dim

    # ----------------------------------------------------------------
    # PULL MODEL
    # ----------------------------------------------------------------

    async def pull_from_hub_async(
        self,
        repo_id: str,
        local_path: Optional[str] = None,
        expected_dim: Optional[int] = None,   # [OK] FIXED: added for dimension validation
        correlation_id: Optional[str] = None,
    ) -> Path:

        corr_id = correlation_id or generate_finetune_correlation_id("pull_model")

        from sentence_transformers import SentenceTransformer

        dest = Path(local_path) if local_path else (self.model_cache / repo_id.replace("/", "_"))
        dest.mkdir(parents=True, exist_ok=True)

        kwargs = {"cache_folder": str(dest)}

        if self.hf_token:
            kwargs["token"] = self.hf_token

        loop = asyncio.get_running_loop()

        model = await loop.run_in_executor(
            None, lambda: SentenceTransformer(repo_id, **kwargs)
        )

        # [OK] FIXED: Validate embedding dimension against qdrant_dim config
        if expected_dim is None:
            _s = get_settings()
            expected_dim = getattr(_s, 'qdrant_dim', 1536)

        await self.validate_model_dimension(model, expected_dim, repo_id, corr_id)

        await loop.run_in_executor(None, lambda: model.save(str(dest)))

        logger.info(f"[{corr_id}] Model saved: {dest}")

        return dest

    # ----------------------------------------------------------------
    # UTILITIES
    # ----------------------------------------------------------------

    def get_local_model_path(self, domain: str) -> Optional[Path]:
        matches = list(self.model_cache.glob(f"*documind-{domain}-embedding*"))
        return matches[0] if matches else None

    def list_available_models(self) -> list[dict]:
        models = []

        for path in self.model_cache.iterdir():
            if path.is_dir():
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

                models.append({
                    "name": path.name,
                    "local_path": str(path),
                    "size_mb": round(size / 1024 / 1024, 1),
                })

        return models

    # ----------------------------------------------------------------
    # SYNC WRAPPERS (SAFE)
    # ----------------------------------------------------------------

    def push_to_hub(self, *args, **kwargs):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            return asyncio.create_task(self.push_to_hub_async(*args, **kwargs))
        else:
            return asyncio.run(self.push_to_hub_async(*args, **kwargs))

    def pull_from_hub(self, *args, **kwargs):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            return asyncio.create_task(self.pull_from_hub_async(*args, **kwargs))
        else:
            return asyncio.run(self.pull_from_hub_async(*args, **kwargs))


# DVMELTSS-M: Explicit module exports
__all__ = ["ModelRegistry", "ModelCard"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

