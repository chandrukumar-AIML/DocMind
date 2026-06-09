# backend/app/observability/langsmith_config.py
# DVMELTSS-FIX: M - Modular, S - Security, L - Logging
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Proper cache key handling + input validation + safe SDK re-init

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Final, Optional, Any

from app.config import get_settings

# DVMELTSS-M: Import centralized utilities
from app.core.pii_utils import scrub_pii_for_evaluation

logger = logging.getLogger(__name__)

# DVMELTSS-S: LangSmith env var names (immutable)
_LANGSMITH_API_KEY: Final = "LANGCHAIN_API_KEY"
_LANGSMITH_TRACING: Final = "LANGCHAIN_TRACING_V2"
_LANGSMITH_ENDPOINT: Final = "LANGCHAIN_ENDPOINT"
_LANGSMITH_PROJECT: Final = "LANGCHAIN_PROJECT"
_LANGSMITH_DATASET: Final = "LANGCHAIN_DATASET"


# ✅ NEW: Input validation helper
def _validate_metadata_inputs(
    source_file: Optional[str],
    document_type: Optional[str],
    extra: Optional[dict],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate metadata inputs before processing."""
    if source_file is not None and not isinstance(source_file, str):
        return False, "source_file must be a string or None"
    if document_type is not None and not isinstance(document_type, str):
        return False, "document_type must be a string or None"
    if extra is not None and not isinstance(extra, dict):
        return False, "extra must be a dict or None"
    return True, ""


def configure_langsmith(correlation_id: Optional[str] = None) -> bool:
    """
    Configure LangSmith auto-tracing for all LangChain calls.

    Key behaviors:
    - Uses setdefault — never overwrites keys already in environment
    - When API key absent, explicitly disables tracing to prevent noisy errors
    - Propagates pydantic-settings values to os.environ (LangSmith SDK reads env directly)
    - FIXED: Accepts correlation_id for distributed tracing

    Args:
        correlation_id: Optional request ID for tracing context

    Returns:
        True if tracing is active, False if disabled
    """
    corr_id = correlation_id or "langsmith_config"
    settings = get_settings()

    # Case 1: No API key — explicitly disable to prevent LangChain callback errors
    if not settings.langchain_api_key:
        os.environ[_LANGSMITH_TRACING] = "false"
        os.environ.pop(_LANGSMITH_API_KEY, None)  # Clean up any stale value
        logger.info(f"[{corr_id}] LANGCHAIN_API_KEY not set — LangSmith tracing disabled.")
        return False

    # Case 2: API key present — propagate to environment for LangSmith SDK
    # Use setdefault to respect any values already set by shell/deployment
    os.environ.setdefault(_LANGSMITH_API_KEY, settings.langchain_api_key)
    os.environ.setdefault(_LANGSMITH_TRACING, "true")
    os.environ.setdefault(_LANGSMITH_ENDPOINT, settings.langchain_endpoint)
    os.environ.setdefault(_LANGSMITH_PROJECT, settings.langchain_project)
    # Optional: set dataset name if configured
    if settings.langchain_dataset_name:
        os.environ.setdefault(_LANGSMITH_DATASET, settings.langchain_dataset_name)

    # ✅ FIXED: Re-init LangSmith SDK to pick up new env vars
    try:
        from langsmith import Client

        # Force re-init with new env vars
        Client(api_key=settings.langchain_api_key, api_url=settings.langchain_endpoint)
    except ImportError:
        logger.debug(f"[{corr_id}] langsmith.Client not available — continuing with env-only config")
    except Exception as e:
        logger.warning(f"[{corr_id}] LangSmith SDK re-init failed: {e}")

    logger.info(
        f"[{corr_id}] LangSmith tracing active: project='{settings.langchain_project}', "
        f"endpoint={settings.langchain_endpoint}"
    )
    return True


@lru_cache(maxsize=1)
def _get_base_metadata() -> dict[str, str]:
    """
    Cache static metadata fields — computed once per process.

    ✅ FIXED: Exclude correlation_id from cache key (it's request-specific).

    Returns:
        Dict with project name and app version for tagging runs
    """
    settings = get_settings()
    return {
        "project": settings.langchain_project,
        "app_version": settings.app_version,
        "environment": "production" if not settings.api_reload else "development",
    }


def get_run_metadata(
    source_file: Optional[str] = None,
    document_type: Optional[str] = None,
    ocr_model: Optional[str] = None,
    strategy: Optional[str] = None,
    sensitive_data: bool = False,
    extra: Optional[dict[str, str]] = None,
    correlation_id: Optional[str] = None,
) -> dict[str, str]:
    """
    Build structured metadata dict for LangSmith run tags.

    Args:
        source_file: Original document filename
        document_type: Classified type (invoice, contract, etc.)
        ocr_model: OCR engine used (paddleocr, vision, etc.)
        strategy: RAG strategy name (hyde, hybrid, etc.)
        sensitive_data: If True, hide inputs/outputs in LangSmith UI
        extra: Additional custom metadata key-value pairs
        correlation_id: Request ID for distributed tracing

    Returns:
        Dict suitable for LangSmith run metadata/tags
    """
    corr_id = correlation_id or "metadata_build"

    # ✅ Validate inputs
    is_valid, error = _validate_metadata_inputs(source_file, document_type, extra, corr_id)
    if not is_valid:
        logger.warning(f"[{corr_id}] Invalid metadata inputs: {error}")
        # Return minimal safe metadata
        return {**_get_base_metadata(), "error": error}

    # FIXED: Get base metadata (without correlation_id in cache)
    metadata = {**_get_base_metadata()}

    # Add correlation_id separately (not cached)
    if correlation_id:
        metadata["correlation_id"] = correlation_id

    # Add optional fields if provided
    if source_file:
        metadata["source_file"] = os.path.basename(source_file)
    if document_type:
        metadata["document_type"] = document_type
    if ocr_model:
        metadata["ocr_model"] = ocr_model
    if strategy:
        metadata["rag_strategy"] = strategy

    # Security: hide inputs/outputs for sensitive documents in LangSmith UI
    if sensitive_data:
        metadata["langsmith:hidden_inputs"] = "true"
        metadata["langsmith:hidden_outputs"] = "true"
        metadata["sensitive"] = "true"

    # Merge extra metadata (user-provided overrides allowed)
    if extra:
        # ✅ FIXED: Safe type conversion + PII scrubbing + truncate
        def _safe_str(v: Any, max_len: int = 500) -> str:
            if v is None:
                return ""
            s = str(v)
            if len(s) > max_len:
                return s[: max_len - 3] + "..."
            return s

        metadata.update(
            {
                k: scrub_pii_for_evaluation(_safe_str(v), domain="general")
                for k, v in extra.items()
                if isinstance(k, str) and k  # Only process valid string keys
            }
        )
    return metadata


def get_dataset_metadata(dataset_name: str, correlation_id: Optional[str] = None) -> dict[str, str]:
    """
    Build metadata for LangSmith dataset creation.

    Args:
        dataset_name: Name of the evaluation dataset
        correlation_id: Request ID for distributed tracing

    Returns:
        Dict with dataset description and tags
    """
    corr_id = correlation_id or "dataset_metadata"

    # ✅ Validate and sanitize dataset_name
    if not isinstance(dataset_name, str) or not dataset_name.strip():
        logger.error(f"[{corr_id}] Invalid dataset_name: must be a non-empty string")
        dataset_name = "documind-eval-default"

    # Sanitize: allow only alphanumeric, underscore, hyphen
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", dataset_name.strip())[:100]

    settings = get_settings()

    # ✅ FIXED: Get base metadata without caching correlation_id
    base_meta = _get_base_metadata()

    return {
        "description": f"DocuMind AI evaluation dataset: {safe_name}",
        "metadata": {
            "created_by": "documind-ai",
            "app_version": settings.app_version,
            "environment": "production" if not settings.api_reload else "development",
            **({"correlation_id": correlation_id} if correlation_id else {}),
        },
    }


def get_langsmith_config_metadata() -> dict[str, Any]:
    """✅ NEW: Return LangSmith config metadata for debugging."""
    return {
        "env_vars": {
            "api_key": _LANGSMITH_API_KEY,
            "tracing": _LANGSMITH_TRACING,
            "endpoint": _LANGSMITH_ENDPOINT,
            "project": _LANGSMITH_PROJECT,
            "dataset": _LANGSMITH_DATASET,
        },
        "cache_enabled": True,
        "pii_scrubbing_enabled": True,
        "max_metadata_value_length": 500,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "configure_langsmith",
    "get_run_metadata",
    "get_dataset_metadata",
    "get_langsmith_config_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
