"""
Shared utilities for fine-tuning modules.

Centralizes:
- Async-safe model loading with memory guards
- HuggingFace API retry logic
- Embedding dimension validation
- Correlation ID propagation

Usage:
    from app.core.finetune_utils import load_model_safe, hf_api_with_retry
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Final, Callable, Any


from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)

# DVMELTSS-S: Safety limits for model loading
_MAX_EMBEDDING_DIM: Final = 2048
_MAX_MODEL_SIZE_GB: Final = 10
_VALID_MODEL_EXTENSIONS: Final = frozenset({".bin", ".safetensors", ".pt", ".pkl"})

# BATMAN-A: HF API retry config
_HF_RETRY_MAX_ATTEMPTS: Final = 3
_HF_RETRY_BASE_DELAY: Final = 1.0
_HF_RETRY_MAX_DELAY: Final = 30.0


async def load_model_safe(
    model_path: str | Path,
    max_dim: int = _MAX_EMBEDDING_DIM,
    max_size_gb: float = _MAX_MODEL_SIZE_GB,
) -> Any:
    """
    Load sentence-transformers model with safety checks.

    Args:
        model_path: Path to model directory or file
        max_dim: Maximum allowed embedding dimension
        max_size_gb: Maximum allowed model size in GB

    Returns:
        Loaded SentenceTransformer model

    Raises:
        FileNotFoundError: If model path doesn't exist
        ValueError: If model exceeds safety limits
        ImportError: If sentence-transformers not installed
    """
    from sentence_transformers import SentenceTransformer

    path = Path(model_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Model path not found: {path}")

    # Check model size
    if path.is_file():
        size_gb = path.stat().st_size / (1024**3)
        if size_gb > max_size_gb:
            raise ValueError(f"Model file {size_gb:.2f}GB exceeds limit {max_size_gb}GB")
    elif path.is_dir():
        total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        size_gb = total_size / (1024**3)
        if size_gb > max_size_gb:
            raise ValueError(f"Model directory {size_gb:.2f}GB exceeds limit {max_size_gb}GB")

    # Load model in thread to avoid blocking event loop
    loop = asyncio.get_running_loop()
    model = await loop.run_in_executor(None, lambda: SentenceTransformer(str(path)))

    # Validate embedding dimension
    dim = model.get_sentence_embedding_dimension()
    if dim > max_dim:
        raise ValueError(f"Model embedding dimension {dim} exceeds safety limit {max_dim}")

    logger.info(f"Model loaded safely: {path} | dim={dim}")
    return model


async def hf_api_with_retry(
    func: Callable,
    *args,
    max_attempts: int = _HF_RETRY_MAX_ATTEMPTS,
    base_delay: float = _HF_RETRY_BASE_DELAY,
    max_delay: float = _HF_RETRY_MAX_DELAY,
    **kwargs,
) -> Any:
    """
    Call HuggingFace API with exponential backoff retry.

    Args:
        func: Async function to call (e.g., api.create_repo)
        *args: Positional arguments for func
        max_attempts: Maximum retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay cap (seconds)
        **kwargs: Keyword arguments for func

    Returns:
        Result from func

    Raises:
        Exception: If all retries exhausted
    """
    delay = base_delay

    for attempt in range(max_attempts + 1):
        try:
            # Add small delay before each attempt to respect rate limits
            await asyncio.sleep(delay * 0.1)
            return await func(*args, **kwargs)

        except Exception as e:
            error_msg = str(e).lower()

            # Retry on rate limit errors
            if "rate limit" in error_msg and attempt < max_attempts:
                wait = min(delay * (2**attempt), max_delay)
                logger.warning(f"HF API rate limit — retry {attempt+1}/{max_attempts} in {wait:.1f}s")
                await asyncio.sleep(wait)
                continue

            # Don't retry on auth/permission errors
            if any(kw in error_msg for kw in ["unauthorized", "forbidden", "invalid token"]):
                raise

            # Retry on transient errors
            if attempt < max_attempts and any(kw in error_msg for kw in ["timeout", "connection", "server"]):
                wait = min(delay * (2**attempt), max_delay)
                logger.warning(f"HF API transient error — retry {attempt+1}/{max_attempts} in {wait:.1f}s: {e}")
                await asyncio.sleep(wait)
                continue

            # All retries exhausted or non-retryable error
            raise


def generate_finetune_correlation_id(prefix: str = "finetune") -> str:
    """Generate correlation ID for fine-tuning operations."""
    return f"{prefix}_{generate_correlation_id()}"


def validate_domain(domain: str, valid_domains: frozenset) -> str:
    """Validate and normalize domain name."""
    normalized = domain.lower().strip()
    if normalized not in valid_domains:
        logger.warning(f"Invalid domain '{domain}' — using 'general'")
        return "general"
    return normalized


# DVMELTSS-M: Explicit module exports
__all__ = [
    "load_model_safe",
    "hf_api_with_retry",
    "generate_finetune_correlation_id",
    "validate_domain",
]
# Local smoke test entry point. Run: python -m

