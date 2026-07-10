
"""
DocuMind AI - Evaluation & Alerting Module

Provides end-to-end evaluation pipelines for:
- RAG quality (RAGAS-style faithfulness, relevancy, precision, recall)
- Traditional NLP metrics (BLEU, ROUGE, F1, CER/WER)
- Retrieval benchmarking (Precision@K, Recall@K, MRR, Hit@K)
- Dataset management with schema validation & atomic I/O
- Threshold-based alerting with rate limiting & PII-safe logging

Public API:
    from app.evaluation import RAGAsPipeline, DatasetManager, AlertEngine
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Pipeline & Orchestration
    "RAGAsPipeline",
    "PipelineConfig",
    "PipelineResult",
    # Dataset Management
    "DatasetManager",
    "EvalDataset",
    "EvalSample",
    # RAGAS Evaluation
    "RAGAsEvaluator",
    "RAGAsSample",
    "RAGAsReport",
    # Traditional Metrics
    "RAGMetricsCalculator",
    "RAGASMetrics",
    "RAGEvalSuite",
    "OCRMetricsCalculator",
    "OCRPageMetrics",
    "OCRDocumentMetrics",
    # Retrieval Evaluation
    "RetrievalEvaluator",
    "RetrievalResult",
    "RetrievalEvalSuite",
    # Alerting
    "AlertEngine",
    "Alert",
    # Text Utilities
    "levenshtein_distance",
    "normalize_text_for_ocr",
    "compute_f1_from_counts",
    # Metadata helpers
    "get_evaluation_metadata",
]

__version__ = "2.1.0"
__description__ = "DocuMind AI RAG Evaluation & Monitoring Pipeline"
__routing__ = "Dataset -> Generation -> RAGAS/Traditional Metrics -> Alerts -> Report"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Pipeline
    "RAGAsPipeline": (".ragas_pipeline", "RAGAsPipeline"),
    "PipelineConfig": (".ragas_pipeline", "PipelineConfig"),
    "PipelineResult": (".ragas_pipeline", "PipelineResult"),
    # Dataset
    "DatasetManager": (".ragas_dataset", "DatasetManager"),
    "EvalDataset": (".ragas_dataset", "EvalDataset"),
    "EvalSample": (".ragas_dataset", "EvalSample"),
    # RAGAS
    "RAGAsEvaluator": (".ragas_evaluator", "RAGAsEvaluator"),
    "RAGAsSample": (".ragas_evaluator", "RAGAsSample"),
    "RAGAsReport": (".ragas_evaluator", "RAGAsReport"),
    # Metrics
    "RAGMetricsCalculator": (".rag_metrics", "RAGMetricsCalculator"),
    "RAGASMetrics": (".rag_metrics", "RAGASMetrics"),
    "RAGEvalSuite": (".rag_metrics", "RAGEvalSuite"),
    "OCRMetricsCalculator": (".ocr_metrics", "OCRMetricsCalculator"),
    "OCRPageMetrics": (".ocr_metrics", "OCRPageMetrics"),
    "OCRDocumentMetrics": (".ocr_metrics", "OCRDocumentMetrics"),
    # Retrieval
    "RetrievalEvaluator": (".retrieval_metrics", "RetrievalEvaluator"),
    "RetrievalResult": (".retrieval_metrics", "RetrievalResult"),
    "RetrievalEvalSuite": (".retrieval_metrics", "RetrievalEvalSuite"),
    # Alerting
    "AlertEngine": (".alert_engine", "AlertEngine"),
    "Alert": (".alert_engine", "Alert"),
    # Utilities
    "levenshtein_distance": (".text_utils", "levenshtein_distance"),
    "normalize_text_for_ocr": (".text_utils", "normalize_text_for_ocr"),
    "compute_f1_from_counts": (".text_utils", "compute_f1_from_counts"),
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

    if name == "get_evaluation_metadata":
        from .ragas_pipeline import get_pipeline_metadata

        return get_pipeline_metadata

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
    import sys

    # Invalidate import caches
    for mod_name in [
        ".ragas_pipeline",
        ".ragas_dataset",
        ".ragas_evaluator",
        ".rag_metrics",
        ".ocr_metrics",
        ".retrieval_metrics",
        ".alert_engine",
    ]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

    try:
        from . import ragas_pipeline

        if hasattr(ragas_pipeline, "_pipeline_instance"):
            ragas_pipeline._pipeline_instance = None
    except ImportError:
        pass

    try:
        from . import ragas_dataset

        if hasattr(ragas_dataset.DatasetManager, "_datasets"):
            ragas_dataset.DatasetManager._datasets.clear()
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
        f"Evaluation module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


def get_evaluation_metadata() -> dict[str, Any]:
    """Return evaluation module metadata for monitoring/debugging."""
    from .ragas_pipeline import get_pipeline_metadata as _get_meta

    return _get_meta()
