# backend/app/models/agent_schemas.py

"""Agent-related Pydantic models for FastAPI validation."""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator

# DVMELTSS-M: Import centralized utilities
from app.core.schema_utils import CorrelationIdField, sanitize_text


class AgentMode(str, Enum):
    """Supported agent operation modes."""
    RAG = "rag"              # Standard retrieval-augmented generation
    CRAG = "crag"            # Corrective RAG with web search fallback
    SELF_RAG = "self_rag"    # Self-reflective RAG with critique
    GRAPH = "graph"          # Graph-based retrieval
    HYBRID = "hybrid"        # Vector + graph hybrid retrieval


class AgentQueryRequest(BaseModel):
    """Request for agent-based query processing."""
    question: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Natural language question",
    )
    mode: AgentMode = Field(
        default=AgentMode.RAG,
        description="Agent operation mode",
    )
    session_id: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional session ID for conversation history",
    )
    filter_source_file: Optional[str] = Field(
        default=None,
        description="Filter results to specific source file",
    )
    filter_document_type: Optional[str] = Field(
        default=None,
        description="Filter results to specific document type",
    )
    top_k_retrieve: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Number of chunks to retrieve before reranking",
    )
    top_k_rerank: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of chunks to return after reranking",
    )
    enable_web_search: bool = Field(
        default=False,
        description="Enable web search fallback (CRAG mode)",
    )
    enable_self_critique: bool = Field(
        default=False,
        description="Enable self-reflection critique (Self-RAG mode)",
    )
    correlation_id: Optional[str] = CorrelationIdField

    @field_validator("question")
    @classmethod
    def sanitize_question(cls, v: str) -> str:
        return sanitize_text(v, max_length=2000, min_length=3)

    class Config:
        json_schema_extra = {
            "example": {
                "question": "What was the total revenue in Q3 2024?",
                "mode": "crag",
                "top_k_retrieve": 20,
                "top_k_rerank": 3,
                "enable_web_search": True,
                "correlation_id": "req-abc123",
            }
        }


class AgentCitation(BaseModel):
    """Citation with source attribution for agent responses."""
    source_file: str
    page_number: int = Field(..., ge=0)
    page_display: int = Field(..., ge=1)
    block_type: str
    chunk_text: str = Field(..., max_length=300)
    relevance_score: float = Field(..., ge=0.0, le=1.0)
    retrieval_mode: str = Field(..., description="vector|graph|web|hybrid")


class AgentQueryResponse(BaseModel):
    """Complete agent query response with answer and citations."""
    answer: str
    citations: List[AgentCitation]
    question: str
    mode: AgentMode
    retrieved_count: int = Field(..., ge=0)
    reranked_count: int = Field(..., ge=0)
    web_search_used: bool = Field(default=False)
    self_critique_applied: bool = Field(default=False)
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    latency_seconds: float = Field(..., ge=0.0)
    correlation_id: Optional[str] = CorrelationIdField

    class Config:
        json_schema_extra = {
            "example": {
                "answer": "The total revenue in Q3 2024 was $2.4 million...",
                "citations": [
                    {
                        "source_file": "invoice_2024_Q3.pdf",
                        "page_number": 3,
                        "page_display": 4,
                        "block_type": "table",
                        "chunk_text": "Q3 Revenue: $2.4M...",
                        "relevance_score": 0.89,
                        "retrieval_mode": "vector",
                    }
                ],
                "question": "What was the total revenue in Q3 2024?",
                "mode": "crag",
                "retrieved_count": 20,
                "reranked_count": 3,
                "web_search_used": False,
                "self_critique_applied": False,
                "confidence_score": 0.92,
                "latency_seconds": 2.34,
                "correlation_id": "req-abc123",
            }
        }


class AgentStreamChunk(BaseModel):
    """Single chunk for streaming SSE responses."""
    type: str = Field(..., description="chunk|answer|citation|error|done")
    content: Optional[str] = Field(default=None)
    citation: Optional[AgentCitation] = Field(default=None)
    metadata: Optional[dict[str, Any]] = Field(default=None)
    correlation_id: Optional[str] = CorrelationIdField


# -- Module Exports ---------------------------------------------------------
__all__ = [
    "AgentMode",
    "AgentQueryRequest",
    "AgentCitation",
    "AgentQueryResponse",
    "AgentStreamChunk",
]
# Local smoke test entry point. Run: python -m 

