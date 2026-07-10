
from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Tables
    "TableExtractor",
    "ExtractedTable",
    # Charts
    "ChartExtractor",
    "ExtractedChart",
    # Forms
    "FormExtractor",
    "ExtractedForm",
    # Pipeline
    "ExtractionPipeline",
    "ExtractionBundle",
    # Test utilities
    "reset_extraction_caches",  # ✅ NEW
]

# ASCALE-S: Module metadata
__version__ = "2.1.0"
__description__ = "DocuMind AI Structured Extraction Pipeline (Tables, Charts, Forms)"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # (module_path, attribute_name)
    "TableExtractor": (".table_extractor", "TableExtractor"),
    "ExtractedTable": (".table_extractor", "ExtractedTable"),
    "ChartExtractor": (".chart_extractor", "ChartExtractor"),
    "ExtractedChart": (".chart_extractor", "ExtractedChart"),
    "FormExtractor": (".form_extractor", "FormExtractor"),
    "ExtractedForm": (".form_extractor", "ExtractedForm"),
    "ExtractionPipeline": (".extraction_pipeline", "ExtractionPipeline"),
    "ExtractionBundle": (".extraction_pipeline", "ExtractionBundle"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Lazy imports to prevent circular dependencies.
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

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


# -- Test utilities ------------------------------------------------------
def reset_extraction_caches() -> None:
    """
    ✅ NEW: Reset internal caches & singletons for clean pytest runs.

    Actually resets LRU caches and module singletons (not just invalidate_caches).
    """
    import sys
    import importlib

    # Reset any LRU caches in extraction modules
    for mod_name in [
        "app.extraction.table_extractor",
        "app.extraction.chart_extractor",
        "app.extraction.form_extractor",
        "app.extraction.extraction_pipeline",
    ]:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            # Reset any class-level caches if they exist
            for obj_name in dir(mod):
                obj = getattr(mod, obj_name)
                if hasattr(obj, "cache_clear") and callable(obj.cache_clear):
                    try:
                        obj.cache_clear()
                    except Exception:
                        pass

    # Invalidate import cache (secondary effect)
    importlib.invalidate_caches()


# -- Module init logging (idempotent) ------------------------------------
__init_logged: bool = False


def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global __init_logged
    if __init_logged:
        return

    import logging

    logger = logging.getLogger(__name__)
    logger.debug(  # ✅ Use debug level to avoid prod log spam
        f"Extraction module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


_log_module_init()
