# backend/app/agent/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Logging/Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - LangGraph Agent Module

Provides the stateful RAG agent with CRAG + Self-RAG capabilities.
Exposes:
- AgentState: TypedDict schema for graph state
- AgentRAGChain: Public async API for query/stream
- get_agent_graph / build_agent_graph: Graph compilation utilities
"""
from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface — prevents accidental internal imports
from .state import AgentState
from .graph import get_agent_graph, build_agent_graph
from .agent_chain import AgentRAGChain

__all__ = [
    "AgentState",
    "AgentRAGChain",
    "get_agent_graph",
    "build_agent_graph",
]

# ASCALE-S: Module-level metadata for observability, version tracking & debugging
__version__ = "2.0.0-phase-e"
__agent_architecture__ = "LangGraph + CRAG + Self-RAG"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AgentState": (".state", "AgentState"),
    "get_agent_graph": (".graph", "get_agent_graph"),
    "build_agent_graph": (".graph", "build_agent_graph"),
    "AgentRAGChain": (".agent_chain", "AgentRAGChain"),
}


def __getattr__(name: str) -> Any:
    """Lazy attribute loading for circular import safety."""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib
            module = importlib.import_module(module_path, package=__name__.rpartition('.')[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(
                f"Failed to lazy-import '{name}' from '{module_path}': {e}"
            ) from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """✅ NEW: Enable IDE/tab-completion for lazy exports."""
    return sorted(set(__all__))


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
        f"Agent module loaded | version={__version__} | arch={__agent_architecture__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()