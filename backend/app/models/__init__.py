# backend/app/models/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling

"""
DocuMind AI - Pydantic Request/Response Models
Used by FastAPI for validation, OpenAPI generation, and type safety.

Public API:
    from app.models import QueryRequest, QueryResponse, IngestRequest, DocumentMetadata, AgentQueryRequest, UserRole
"""
from __future__ import annotations

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Common
    "ChatMessage", "IngestRequest", "QueryRequest",
    "IngestResponse", "CitationModel", "QueryResponse",
    "DocumentMetaResponse", "DocumentListResponse",
    "DeleteResponse", "HealthResponse", "ErrorResponse",
    "ProcessingStatus",
    # ✅ NEW: Auth/User enums
    "UserRole",
    # Table
    "TableQueryRequest", "TableQueryResponse", "ExtractionStatsResponse",
    # Graph
    "GraphRetrievalMode", "GraphQueryRequest", "GraphNode",
    "GraphEdge", "GraphVisualizationData", "GraphQueryResponse", "GraphSchemaResponse",
    # Document
    "DocumentMetadata", "PaginationParams", "DocumentUpdateRequest",
    # ✅ NEW: Agent schemas
    "AgentMode", "AgentQueryRequest", "AgentCitation", "AgentQueryResponse", "AgentStreamChunk",
]

# ASCALE-S: Module metadata
__version__ = "2.1.0"
__description__ = "DocuMind AI Pydantic API Schemas"


def __getattr__(name: str):
    """DVMELTSS-T: Lazy imports to prevent circular dependencies."""
    
    # ✅ NEW: Auth/User enums - import from auth.models
    if name == "UserRole":
        from app.auth.models import UserRole
        return UserRole
    
    # Common schemas
    if name in ("ChatMessage", "IngestRequest", "QueryRequest", "IngestResponse", 
                "CitationModel", "QueryResponse", "DocumentMetaResponse", 
                "DocumentListResponse", "DeleteResponse", "HealthResponse", "ErrorResponse",
                "ProcessingStatus"):
        from .common_schemas import (
            ChatMessage, IngestRequest, QueryRequest, IngestResponse,
            CitationModel, QueryResponse, DocumentMetaResponse,
            DocumentListResponse, DeleteResponse, HealthResponse, ErrorResponse,
            ProcessingStatus,
        )
        return locals()[name]
    
    # Table schemas
    if name in ("TableQueryRequest", "TableQueryResponse", "ExtractionStatsResponse"):
        from .table_schemas import TableQueryRequest, TableQueryResponse, ExtractionStatsResponse
        return locals()[name]
    
    # Graph schemas
    if name in ("GraphRetrievalMode", "GraphQueryRequest", "GraphNode", 
                "GraphEdge", "GraphVisualizationData", "GraphQueryResponse", "GraphSchemaResponse"):
        from .graph_schemas import (
            GraphRetrievalMode, GraphQueryRequest, GraphNode,
            GraphEdge, GraphVisualizationData, GraphQueryResponse, GraphSchemaResponse,
        )
        return locals()[name]
    
    # Document schemas
    if name in ("DocumentMetadata", "PaginationParams", "DocumentUpdateRequest"):
        from .document_schemas import (
            DocumentMetadata, PaginationParams, DocumentUpdateRequest
        )
        return locals()[name]
    
    # ✅ NEW: Agent schemas
    if name in ("AgentMode", "AgentQueryRequest", "AgentCitation", "AgentQueryResponse", "AgentStreamChunk"):
        from .agent_schemas import (
            AgentMode, AgentQueryRequest, AgentCitation, AgentQueryResponse, AgentStreamChunk
        )
        return locals()[name]
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reset_caches_for_tests() -> None:
    """Reset internal caches for clean pytest runs."""
    import importlib
    for mod_name in [".common_schemas", ".table_schemas", ".graph_schemas", ".document_schemas", ".agent_schemas"]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass


def _log_module_init() -> None:
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Models module loaded | version={__version__} | {__description__}")

_log_module_init()