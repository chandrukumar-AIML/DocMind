
"""
DocuMind AI - Fine-Tuning Module

Provides tools for embedding model fine-tuning:
- Triplet dataset generation for contrastive learning
- Model registry for HuggingFace Hub integration
- Embedding updater for workspace vector store migration

Public API:
    from app.finetuning import TripletDatasetGenerator, ModelRegistry, EmbeddingUpdater
"""

from __future__ import annotations

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Dataset Generation
    "TripletDatasetGenerator",
    "TripletDataset",
    "TrainingTriplet",
    # Model Registry
    "ModelRegistry",
    "ModelCard",
    # Embedding Updates
    "EmbeddingUpdater",
    "ReembedResult",
]

# ASCALE-S: Module metadata for observability & version tracking
__version__ = "1.1.0"  # FIXED: Bumped for async fixes + correlation_id
__description__ = "DocuMind AI Embedding Fine-Tuning Pipeline"
__supported_domains__ = "legal, medical, invoice, general"


def __getattr__(name: str):
    """
    DVMELTSS-T: Dynamically resolve imports only when accessed.
    Prevents circular imports between finetuning ↔ vectorstore ↔ evaluation modules.
    Enables pytest to collect tests without initializing heavy ML dependencies.
    """
    # Dataset Generation
    if name in ("TripletDatasetGenerator", "TripletDataset", "TrainingTriplet"):
        from .dataset_generator import (
            TripletDatasetGenerator,
            TripletDataset,
            TrainingTriplet,
        )

        return locals()[name]

    # Model Registry
    if name in ("ModelRegistry", "ModelCard"):
        from .model_registry import ModelRegistry, ModelCard

        return locals()[name]

    # Embedding Updates
    if name in ("EmbeddingUpdater", "ReembedResult"):
        from .embedding_updater import EmbeddingUpdater, ReembedResult

        return locals()[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reset_caches_for_tests() -> None:
    """Reset internal caches & singletons for clean pytest runs."""
    import importlib

    for mod_name in [".dataset_generator", ".model_registry", ".embedding_updater"]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass


def _log_module_init() -> None:
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Finetuning module loaded | version={__version__} | {__description__}")


_log_module_init()
