# backend/app/observability/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ CREATED: Production-ready __init__.py with lazy imports + error handling

"""
DocuMind AI - Observability & Tracing Module

Provides distributed tracing and experiment tracking for:
- LangSmith auto-tracing for LangChain calls with PII-safe metadata
- MLflow logging for OCR, retrieval, RAG, and ingestion metrics
- Circuit breaker pattern for graceful degradation on tracking failures
- Correlation ID propagation across all observability layers

Public API:
    from app.observability import (
        traceable, trace_chain,  # LangSmith decorators
        MLflowLogger, configure_mlflow,  # MLflow logging
        configure_langsmith, get_run_metadata,  # LangSmith config
        LangSmithEvalDataset, EvalRunResult,  # Dataset management
    )
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # LangSmith Tracing
    "traceable",
    "trace_chain",
    "trace_tool",
    "trace_llm",
    "get_tracer_metadata",
    # MLflow Logging
    "MLflowLogger",
    "configure_mlflow",
    "NULL_RUN",
    "get_mlflow_metadata",
    # LangSmith Config
    "configure_langsmith",
    "get_run_metadata",
    "get_dataset_metadata",
    "get_langsmith_config_metadata",
    # LangSmith Dataset
    "LangSmithEvalDataset",
    "EvalRunResult",
    "get_langsmith_dataset_metadata",
    # Module metadata
    "get_observability_metadata",
]

__version__ = "1.0.0"
__description__ = "DocuMind AI Observability & Distributed Tracing Layer"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # LangSmith Tracing
    "traceable": (".langsmith_tracer", "traceable"),
    "trace_chain": (".langsmith_tracer", "trace_chain"),
    "trace_tool": (".langsmith_tracer", "trace_tool"),
    "trace_llm": (".langsmith_tracer", "trace_llm"),
    "get_tracer_metadata": (".langsmith_tracer", "get_tracer_metadata"),
    # MLflow Logging
    "MLflowLogger": (".mlflow_logger", "MLflowLogger"),
    "configure_mlflow": (".mlflow_logger", "configure_mlflow"),
    "NULL_RUN": (".mlflow_logger", "NULL_RUN"),
    "get_mlflow_metadata": (".mlflow_logger", "get_mlflow_metadata"),
    # LangSmith Config
    "configure_langsmith": (".langsmith_config", "configure_langsmith"),
    "get_run_metadata": (".langsmith_config", "get_run_metadata"),
    "get_dataset_metadata": (".langsmith_config", "get_dataset_metadata"),
    "get_langsmith_config_metadata": (
        ".langsmith_config",
        "get_langsmith_config_metadata",
    ),
    # LangSmith Dataset
    "LangSmithEvalDataset": (".langsmith_dataset", "LangSmithEvalDataset"),
    "EvalRunResult": (".langsmith_dataset", "EvalRunResult"),
    "get_langsmith_dataset_metadata": (
        ".langsmith_dataset",
        "get_langsmith_dataset_metadata",
    ),
}


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve imports to prevent circular dependencies at startup.
    ✅ FIXED: Direct return + explicit error handling.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_path, package=__name__.rpartition(".")[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import '{name}' from '{module_path}': {e}") from e

    if name == "get_observability_metadata":
        return get_observability_metadata

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


def _reset_caches_for_tests() -> None:
    """Reset internal caches & singletons for clean pytest runs."""
    import importlib

    # Invalidate import caches
    for mod_name in [
        ".langsmith_tracer",
        ".mlflow_logger",
        ".langsmith_config",
        ".langsmith_dataset",
    ]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

    # Reset MLflowLogger circuit breaker if loaded
    try:
        from . import mlflow_logger

        if hasattr(mlflow_logger.MLflowLogger, "_mlflow_available"):
            mlflow_logger.MLflowLogger._mlflow_available = True
            mlflow_logger.MLflowLogger._failure_count = 0
    except ImportError:
        pass


# DVMELTSS-L: Module initialization logging for observability
__init_logged: bool = False


def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global __init_logged
    if __init_logged:
        return

    import logging

    logger = logging.getLogger(__name__)
    logger.debug(  # ✅ Use debug level to avoid prod log spam
        f"Observability module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Module metadata helper for monitoring
def get_observability_metadata() -> dict[str, Any]:
    """Return observability module metadata for debugging."""
    from .langsmith_tracer import get_tracer_metadata as _get_tracer_meta
    from .mlflow_logger import get_mlflow_metadata as _get_mlflow_meta
    from .langsmith_config import get_langsmith_config_metadata as _get_config_meta
    from .langsmith_dataset import get_langsmith_dataset_metadata as _get_dataset_meta

    return {
        "version": __version__,
        "description": __description__,
        "components": {
            "langsmith_tracer": _get_tracer_meta(),
            "mlflow_logger": _get_mlflow_meta(),
            "langsmith_config": _get_config_meta(),
            "langsmith_dataset": _get_dataset_meta(),
        },
        "features": [
            "distributed_tracing",
            "experiment_tracking",
            "circuit_breaker",
            "pii_scrubbing",
            "async_support",
            "graceful_degradation",
        ],
    }
