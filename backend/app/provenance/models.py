# ACID-INDEX: C - Constraints, I - Indexes, D - Data types

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
    UniqueConstraint,
    CheckConstraint,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, relationship

# DVMELTSS-M: Use centralized Base from database.base
from app.database.base import Base

# DVMELTSS-M: Import centralized time utility
from app.core.time_utils import utcnow


class HighlightColor(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    @classmethod
    def _missing_(cls, value: str) -> "HighlightColor | None":
        """Allow case-insensitive lookup."""
        for member in cls:
            if member.value.lower() == value.lower():
                return member
        return None


class Answer(Base):
    """
    Every generated answer is stored with its full context.

    Rationale: enables audit trail, repeat-query detection,
    and future A/B testing (same question, different models).
    """

    __tablename__ = "answers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question = Column(Text, nullable=False)
    answer_text = Column(Text, nullable=False)
    workspace_id = Column(String(128), nullable=False, default="default", index=True)
    thread_id = Column(String(128), nullable=True)  # conversation thread
    retrieval_mode = Column(String(32), nullable=True)  # vector/graph/hybrid
    query_type = Column(String(32), nullable=True)  # factual/relational/etc

    confidence_score = Column(Float, nullable=True)
    latency_seconds = Column(Float, nullable=True)
    model_name = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: utcnow(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: utcnow(),
        onupdate=lambda: utcnow(),
        nullable=True,
    )

    correlation_id = Column(String(128), nullable=True, index=True)

    # Relationships
    citations: Mapped[list["Citation"]] = relationship(
        "Citation",
        back_populates="answer",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_answers_workspace_created", "workspace_id", "created_at"),
        Index("ix_answers_thread", "thread_id"),
        Index("ix_answers_correlation", "correlation_id"),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0.0 AND confidence_score <= 1.0)",
            name="ck_answer_confidence_range",
        ),
    )

    def __repr__(self) -> str:
        return f"<Answer(id={self.id}, question='{self.question[:50]}...', workspace={self.workspace_id})>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-ready dict."""
        return {
            "answer_id": str(self.id),
            "question": self.question,
            "answer_text": self.answer_text,
            "workspace_id": self.workspace_id,
            "thread_id": self.thread_id,
            "retrieval_mode": self.retrieval_mode,
            "query_type": self.query_type,
            "confidence_score": self.confidence_score,
            "latency_seconds": self.latency_seconds,
            "model_name": self.model_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "correlation_id": self.correlation_id,
        }


class Citation(Base):
    """
    Every source reference for an answer, with exact text location.

    char_offset_start/end: character positions within the full document text
    (computed by highlight.py — enables react-pdf text layer highlighting)
    """

    __tablename__ = "citations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    answer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("answers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_file = Column(String(1024), nullable=False, index=True)
    page_number = Column(Integer, nullable=False, default=0)  # 0-indexed
    chunk_id = Column(String(128), nullable=True)
    chunk_text = Column(String(4000), nullable=False)

    confidence_score = Column(Float, nullable=False, default=0.0)
    block_type = Column(String(32), nullable=True)

    # Text location for highlight
    char_offset_start = Column(Integer, nullable=True)
    char_offset_end = Column(Integer, nullable=True)
    highlight_color = Column(SQLEnum(HighlightColor), nullable=True)

    # Phase I ready
    workspace_id = Column(String(128), nullable=False, default="default")

    correlation_id = Column(String(128), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=lambda: utcnow(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: utcnow(),
        onupdate=lambda: utcnow(),
        nullable=True,
    )

    # Relationships
    answer = relationship("Answer", back_populates="citations")

    __table_args__ = (
        Index("ix_citations_source_page", "source_file", "page_number"),
        Index("ix_citations_workspace", "workspace_id"),
        Index("ix_citations_correlation", "correlation_id"),
        CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_citation_confidence_range",
        ),
    )

    def __repr__(self) -> str:
        return f"<Citation(id={self.id}, source='{self.source_file}', page={self.page_number})>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-ready dict."""
        return {
            "citation_id": str(self.id),
            "answer_id": str(self.answer_id),
            "source_file": self.source_file,
            "page_number": self.page_number,
            "page_display": self.page_number + 1,
            "chunk_id": self.chunk_id,
            "chunk_text": self.chunk_text,
            "confidence_score": self.confidence_score,
            "block_type": self.block_type,
            "char_offset_start": self.char_offset_start,
            "char_offset_end": self.char_offset_end,
            "highlight_color": self.highlight_color.value if self.highlight_color else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "correlation_id": self.correlation_id,
        }


class DocumentStore(Base):
    """
    Registry of all ingested documents — source of truth for the PDF viewer.
    Stores the document file path so the viewer can load it.

    Phase J: add version column here.
    """

    __tablename__ = "document_store"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_file = Column(String(1024), nullable=False)
    workspace_id = Column(String(128), nullable=False, default="default")
    file_path = Column(Text, nullable=True)  # server-side path
    document_type = Column(String(64), nullable=True)
    page_count = Column(Integer, nullable=True)

    ingested_at = Column(DateTime(timezone=True), default=lambda: utcnow(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: utcnow(),
        onupdate=lambda: utcnow(),
        nullable=True,
    )

    correlation_id = Column(String(128), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("source_file", "workspace_id", name="uq_document_workspace"),
        Index("ix_docstore_workspace", "workspace_id"),
        Index("ix_docstore_correlation", "correlation_id"),
    )

    def __repr__(self) -> str:
        return f"<DocumentStore(id={self.id}, source='{self.source_file}', workspace={self.workspace_id})>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-ready dict."""
        return {
            "id": str(self.id),
            "source_file": self.source_file,
            "workspace_id": self.workspace_id,
            "file_path": self.file_path,
            "document_type": self.document_type,
            "page_count": self.page_count,
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "correlation_id": self.correlation_id,
        }


# DVMELTSS-M: Explicit module exports
__all__ = ["Base", "Answer", "Citation", "DocumentStore", "HighlightColor"]
# Local smoke test entry point. Run: python -m

