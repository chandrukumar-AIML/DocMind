# backend/app/models/common_schemas.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Security
# ASCALE-FIX: S - Separation, C - Coupling

from __future__ import annotations

import re
from enum import Enum
from typing import Any, List, Optional, Tuple, Union, TYPE_CHECKING
from pydantic import BaseModel, Field, field_validator, ConfigDict

# DVMELTSS-M: Import centralized utilities
from app.core.schema_utils import (
    sanitize_text,
    validate_correlation_id,
    validate_tags,
    validate_language_code,
    validate_page_range,
    CorrelationIdField,
    QuestionField,
    TagsField,
    LanguageField,
    dataclass_to_pydantic,
)

# Type checking import to avoid circular dependency
if TYPE_CHECKING:
    from app.rag.chain import Citation  # Internal dataclass


class ProcessingStatus(str, Enum):
    """Document processing status enum."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ChatMessage(BaseModel):
    """Structured chat message for conversation history."""
    role: str = Field(
        ...,
        pattern="^(user|assistant)$",
        description="Message role: 'user' or 'assistant'",
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Message content",
    )

    model_config = ConfigDict(
        json_schema_extra={"example": {"role": "user", "content": "What is the total revenue in Q3?"}}
    )


class IngestRequest(BaseModel):
    """Document ingestion configuration options."""
    enable_vision_enrichment: bool = Field(
        default=False,
        description="Enable GPT-4o Vision semantic enrichment for tables/diagrams",
    )
    enable_ocr_fallback: bool = Field(
        default=False,
        description="Enable GPT-4o Vision as fallback when PaddleOCR confidence is low",
    )
    document_language: Optional[str] = LanguageField
    tags: List[str] = TagsField

    @field_validator("document_language")
    @classmethod
    def validate_language(cls, v: Optional[str]) -> Optional[str]:
        # FIXED: Use centralized validator
        return validate_language_code(v)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        # FIXED: Use centralized validator
        return validate_tags(v)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"enable_vision_enrichment": True, "document_language": "en", "tags": ["invoice", "finance", "Q3-2024"]}
        }
    )


class QueryRequest(BaseModel):
    """Natural language query with optional filters and chat history."""
    question: str = QuestionField
    session_id: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional session ID for conversation history persistence",
    )
    filter_source_file: Optional[str] = Field(default=None, description="Filter results to specific source filename")
    filter_document_type: Optional[str] = Field(default=None, description="Filter results to specific document type")
    filter_page_range: Optional[Tuple[int, int]] = Field(
        default=None,
        description="Page range [start, end] (0-indexed, inclusive) for filtering",
    )
    top_k_retrieve: int = Field(default=20, ge=1, le=50, description="Number of chunks to retrieve before reranking")
    top_k_rerank: int = Field(default=3, ge=1, le=10, description="Number of chunks to return after reranking")
    chat_history: List[ChatMessage] = Field(default_factory=list, max_length=20, description="Previous conversation messages")
    stream: bool = Field(default=True, description="Return streaming SSE response (True) or batch JSON (False)")
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing

    @field_validator("question")
    @classmethod
    def sanitize_question(cls, v: str) -> str:
        # FIXED: Use centralized sanitizer
        return sanitize_text(v, max_length=2000, min_length=3)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("session_id may contain only letters, numbers, hyphens, and underscores")
        return v

    @field_validator("filter_page_range")
    @classmethod
    def validate_page_range(cls, v: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
        # FIXED: Use centralized validator
        return validate_page_range(v)

    @field_validator("chat_history")
    @classmethod
    def validate_chat_history(cls, v: List[ChatMessage]) -> List[ChatMessage]:
        if len(v) > 20:
            raise ValueError("chat_history cannot exceed 20 messages")
        for i, msg in enumerate(v):
            expected = "user" if i % 2 == 0 else "assistant"
            if msg.role != expected:
                raise ValueError(f"chat_history message {i} has role '{msg.role}' but expected '{expected}' (must alternate user/assistant)")
        return v

    def build_filter_dict(self) -> Optional[dict[str, Any]]:
        """Convert filter fields to vector store filter dict."""
        filters: dict[str, Any] = {}
        if self.filter_source_file:
            filters["source_file"] = self.filter_source_file
        if self.filter_document_type:
            filters["document_type"] = self.filter_document_type
        if self.filter_page_range:
            start, end = self.filter_page_range
            filters["page_number"] = {"$gte": start, "$lte": end}
        return filters if filters else None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "question": "What was the total revenue in Q3 2024?",
                "session_id": "user-123-session",
                "filter_document_type": "financial_report",
                "top_k_retrieve": 20,
                "top_k_rerank": 3,
                "stream": True,
                "correlation_id": "req-abc123"  # FIXED: Include in example
            }
        }
    )


# -- RESPONSE MODELS ------------------------------------------------------
class IngestResponse(BaseModel):
    filename: str
    status: str = Field(..., description="'indexed' or 'error'")
    page_count: int = Field(..., ge=0)
    child_chunks: int = Field(..., ge=0)
    parent_chunks: int = Field(..., ge=0)
    ocr_confidence: float = Field(..., ge=0.0, le=1.0)
    document_type: str
    latency_seconds: float = Field(..., ge=0.0)
    message: str = "Document successfully indexed."
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class CitationModel(BaseModel):
    """Structured citation with source attribution for API responses."""
    source_file: str
    page_number: int = Field(..., ge=0)
    page_display: int = Field(..., ge=1)
    block_type: str
    chunk_text: str = Field(..., max_length=300)
    rerank_score: float = Field(..., ge=0.0, le=1.0)

    @classmethod
    def from_citation(cls, c: "Citation") -> "CitationModel":
        """
        Convert internal Citation dataclass to API response model.
        Computes derived fields (page_display, truncated chunk_text, rounded
        rerank_score) that are not stored verbatim on the internal dataclass.
        """
        raw_text = getattr(c, "chunk_text", "") or ""
        truncated = raw_text[:297] + "..." if len(raw_text) > 300 else raw_text
        return cls(
            source_file=c.source_file,
            page_number=c.page_number,
            page_display=c.page_number + 1,  # 0-indexed → 1-indexed for UI
            block_type=c.block_type,
            chunk_text=truncated,
            rerank_score=round(c.rerank_score, 4),
        )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_file": "invoice_2024_Q3.pdf",
                "page_number": 3,
                "page_display": 4,
                "block_type": "table",
                "chunk_text": "Q3 Revenue: $2.4M...",
                "rerank_score": 0.89
            }
        }
    )


class QueryResponse(BaseModel):
    """Complete RAG query response with answer and citations."""
    answer: str
    citations: List[CitationModel]
    question: str
    hyde_hypothesis: str
    retrieved_count: int = Field(..., ge=0)
    reranked_count: int = Field(..., ge=0)
    latency_seconds: float = Field(..., ge=0.0)
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "answer": "The total revenue in Q3 2024 was $2.4 million...",
                "citations": [
                    {
                        "source_file": "invoice_2024_Q3.pdf",
                        "page_number": 3,
                        "page_display": 4,
                        "block_type": "table",
                        "chunk_text": "Q3 Revenue: $2.4M...",
                        "rerank_score": 0.89
                    }
                ],
                "question": "What was the total revenue in Q3 2024?",
                "hyde_hypothesis": "The quarterly financial report shows revenue figures...",
                "retrieved_count": 20,
                "reranked_count": 3,
                "latency_seconds": 2.34,
                "correlation_id": "req-abc123"  # FIXED: Include in example
            }
        }
    )


class DocumentMetaResponse(BaseModel):
    """Metadata summary for an indexed document."""
    source_file: str
    document_type: str
    language: str
    page_count: int = Field(..., ge=0)
    chunk_count: int = Field(..., ge=0)
    mean_ocr_confidence: float = Field(..., ge=0.0, le=1.0)
    ingest_timestamp: str
    tags: List[str] = []
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class DocumentListResponse(BaseModel):
    """Paginated list of indexed documents."""
    documents: List[DocumentMetaResponse]
    total_count: int = Field(..., ge=0)


class DeleteResponse(BaseModel):
    """Response after document deletion."""
    source_file: str
    deleted_chunks: int = Field(..., ge=0)
    status: str = "deleted"
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class HealthResponse(BaseModel):
    """Service health check response with component status."""
    status: str = Field(..., description="'ok', 'degraded', or 'error'")
    version: str
    vector_store: dict[str, Any]
    ocr_ready: bool
    rag_ready: bool
    timestamp: str
    startup_errors: List[str] = []
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class ErrorResponse(BaseModel):
    """Standardized error response for API errors."""
    error: str
    detail: str
    code: Optional[str] = None
    reference_id: Optional[str] = None  # For support ticket correlation
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "validation_error",
                "detail": "Question cannot be empty or whitespace only",
                "code": "EMPTY_QUESTION",
                "reference_id": "abc12345",
                "correlation_id": "req-abc123"  # FIXED: Include in example
            }
        }
    )


# DVMELTSS-M: Explicit module exports
__all__ = [
    "ChatMessage", "IngestRequest", "QueryRequest",
    "IngestResponse", "CitationModel", "QueryResponse",
    "DocumentMetaResponse", "DocumentListResponse",
    "DeleteResponse", "HealthResponse", "ErrorResponse",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

