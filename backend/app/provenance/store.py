# ACID-INDEX: C - Constraints, I - Indexes, N - N+1, D - Data types

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional, Any, Dict, Final

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import select, desc, func, delete, text
from sqlalchemy.orm import selectinload

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.time_utils import format_iso
from app.provenance.highlight import compute_highlight_color
from .models import Base, Answer, Citation, DocumentStore

logger = logging.getLogger(__name__)

_engines: Dict[str, AsyncEngine] = {}
_session_factories: Dict[str, async_sessionmaker[AsyncSession]] = {}

# DVMELTSS-S: Timeout for DB operations
_DB_TIMEOUT: Final = 30.0


def _get_engine_version_key(settings) -> str:
    """Generate version key from settings to invalidate cache if config changes."""
    return (
        f"{getattr(settings, 'postgres_host', 'localhost')}:"
        f"{getattr(settings, 'postgres_port', 5432)}:"
        f"{getattr(settings, 'postgres_db', 'documind')}"
    )


def get_engine() -> AsyncEngine:
    """
    Singleton async SQLAlchemy engine.
    ✅ FIXED: Use dict cache with version key for proper singleton behavior.
    """
    settings = get_settings()
    version_key = _get_engine_version_key(settings)

    if version_key not in _engines:
        db_url = getattr(settings, "database_url", "")

        if not db_url:
            # Build from components
            db_url = (
                f"postgresql+asyncpg://"
                f"{getattr(settings, 'postgres_user', 'documind')}:"
                f"{getattr(settings, 'postgres_password', 'documind_pass')}@"
                f"{getattr(settings, 'postgres_host', 'localhost')}:"
                f"{getattr(settings, 'postgres_port', 5432)}/"
                f"{getattr(settings, 'postgres_db', 'documind')}"
            )

        _engines[version_key] = create_async_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            echo=False,
        )
        logger.info(f"PostgreSQL engine created: {db_url.split('@')[-1]}")

    return _engines[version_key]


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get cached async session factory."""
    settings = get_settings()
    version_key = _get_engine_version_key(settings)

    if version_key not in _session_factories:
        _session_factories[version_key] = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )

    return _session_factories[version_key]


async def init_db():
    """
    Create all tables if they don't exist.
    Called at application startup.
    Safe to run multiple times — CREATE TABLE IF NOT EXISTS.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("PostgreSQL tables initialized.")


class ProvenanceStore:
    """
    Async PostgreSQL store for answer provenance and citations.

    All methods are async and use connection pooling.
    Designed to be instantiated per-request (lightweight — no state).
    """

    def __init__(self):
        self._session_factory = get_session_factory()

    def _validate_inputs(
        self,
        question: Optional[str],
        answer_text: Optional[str],
        source_file: Optional[str],
        workspace_id: str,
        corr_id: str,
    ) -> tuple[bool, str]:
        """Validate inputs before processing."""
        if question is not None and not isinstance(question, str):
            return False, "question must be a string or None"
        if answer_text is not None and not isinstance(answer_text, str):
            return False, "answer_text must be a string or None"
        if source_file is not None and not isinstance(source_file, str):
            return False, "source_file must be a string or None"
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return False, "workspace_id must be a non-empty string"
        return True, ""

    # -- Write operations ------------------------------------------------------

    async def save_answer(
        self,
        question: str,
        answer_text: str,
        citations: list[dict],
        workspace_id: str = "default",
        thread_id: Optional[str] = None,
        retrieval_mode: str = "vector",
        query_type: str = "factual",
        confidence_score: float = 0.0,
        latency_seconds: float = 0.0,
        model_name: str = "",
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Persist a generated answer with all its citations.

        Args:
            question: user's question
            answer_text: generated answer
            citations: list of citation dicts from the RAG pipeline
            workspace_id: tenant namespace
            thread_id: conversation thread
            correlation_id: Request ID for distributed tracing

        Returns:
            answer_id (UUID string) for reference
        """
        answer_id = uuid.uuid4()
        corr_id = correlation_id or "provenance_unknown"

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(question, answer_text, None, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            raise ValueError(error)

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    # Create answer record
                    answer_obj = Answer(
                        id=answer_id,
                        question=question,
                        answer_text=answer_text,
                        workspace_id=workspace_id,
                        thread_id=thread_id,
                        retrieval_mode=retrieval_mode,
                        query_type=query_type,
                        confidence_score=confidence_score,
                        latency_seconds=latency_seconds,
                        model_name=model_name or "gpt-4o",
                        correlation_id=corr_id,
                    )
                    session.add(answer_obj)

                    # Create citation records
                    for i, cit in enumerate(citations):
                        confidence = float(cit.get("confidence_score", cit.get("rerank_score", 0.0)))
                        highlight_color = compute_highlight_color(confidence)

                        page_num_raw = cit.get("page_number", cit.get("page_display", 1))
                        page_number = int(page_num_raw) - 1 if page_num_raw else 0

                        citation_obj = Citation(
                            id=uuid.uuid4(),
                            answer_id=answer_id,
                            source_file=str(cit.get("source_file", "")),
                            page_number=page_number,
                            chunk_id=str(cit.get("chunk_id", "")),
                            chunk_text=str(cit.get("chunk_text", ""))[:2000],  # FIXED: Truncate
                            confidence_score=confidence,
                            block_type=str(cit.get("block_type", "paragraph")),
                            char_offset_start=cit.get("char_offset_start"),
                            char_offset_end=cit.get("char_offset_end"),
                            highlight_color=highlight_color,
                            workspace_id=workspace_id,
                            correlation_id=corr_id,
                        )
                        session.add(citation_obj)
            logger.info(
                f"[{corr_id}] ProvenanceStore: saved answer {answer_id} | "
                f"{len(citations)} citations | workspace={workspace_id}"
            )
            return str(answer_id)
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to save answer: {e}")
            raise

    async def register_document(
        self,
        source_file: str,
        workspace_id: str = "default",
        file_path: str = "",
        document_type: str = "other",
        page_count: int = 0,
        correlation_id: Optional[str] = None,
    ) -> str:
        """Register a document for PDF viewer access."""
        corr_id = correlation_id or "doc_register"

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(None, None, source_file, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            raise ValueError(error)

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    # Check if exists
                    stmt = select(DocumentStore).where(
                        DocumentStore.source_file == source_file,
                        DocumentStore.workspace_id == workspace_id,
                    )
                    existing = await asyncio.wait_for(
                        session.scalar(stmt),
                        timeout=_DB_TIMEOUT,
                    )

                    if existing:
                        existing.file_path = file_path
                        existing.document_type = document_type
                        existing.page_count = page_count
                        existing.correlation_id = corr_id
                        return str(existing.id)

                    doc = DocumentStore(
                        source_file=source_file,
                        workspace_id=workspace_id,
                        file_path=file_path,
                        document_type=document_type,
                        page_count=page_count,
                        correlation_id=corr_id,
                    )
                    session.add(doc)
                    return str(doc.id)
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Document registration timed out after {_DB_TIMEOUT}s")
            raise
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to register document: {e}")
            raise

    # -- Read operations -------------------------------------------------------

    async def get_answer(
        self,
        answer_id: str,
        workspace_id: str = "default",
        correlation_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Get a single answer with all its citations."""
        corr_id = correlation_id or "provenance_get"

        try:
            answer_uuid = uuid.UUID(answer_id)
        except ValueError:
            logger.warning(f"[{corr_id}] Invalid answer_id format: {answer_id}")
            return None

        try:
            async with self._session_factory() as session:
                stmt = (
                    select(Answer)
                    .where(Answer.id == answer_uuid, Answer.workspace_id == workspace_id)
                    .options(selectinload(Answer.citations))
                )
                answer = await asyncio.wait_for(
                    session.scalar(stmt),
                    timeout=_DB_TIMEOUT,
                )
                if not answer:
                    return None
                return self._answer_to_dict(answer, corr_id)
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Get answer timed out after {_DB_TIMEOUT}s")
            return None
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to get answer: {e}")
            return None

    async def list_answers(
        self,
        workspace_id: str = "default",
        source_file: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        correlation_id: Optional[str] = None,
    ) -> list[dict]:
        """
        List answers with their citations, optionally filtered by source file.
        """
        corr_id = correlation_id or "list_answers"

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(None, None, source_file, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return []

        try:
            async with self._session_factory() as session:
                stmt = (
                    select(Answer)
                    .where(Answer.workspace_id == workspace_id)
                    .order_by(desc(Answer.created_at))
                    .limit(limit)
                    .offset(offset)
                )
                if source_file:
                    # Filter to answers that cited this document
                    stmt = stmt.join(Citation).where(Citation.source_file == source_file).distinct()

                results = await asyncio.wait_for(
                    session.scalars(stmt),
                    timeout=_DB_TIMEOUT,
                )
                return [self._answer_to_dict(a, corr_id) for a in results]
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] List answers timed out after {_DB_TIMEOUT}s")
            return []
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to list answers: {e}")
            return []

    async def get_citations_for_document(
        self,
        source_file: str,
        workspace_id: str = "default",
        page_number: Optional[int] = None,
        limit: int = 50,
        correlation_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Get all citations that reference a specific document.
        Used by the PDF viewer to show which answers cited each page.
        """
        corr_id = correlation_id or "citations_doc"

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(None, None, source_file, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return []

        try:
            async with self._session_factory() as session:
                stmt = (
                    select(Citation)
                    .where(
                        Citation.source_file == source_file,
                        Citation.workspace_id == workspace_id,
                    )
                    .order_by(desc(Citation.created_at))
                    .limit(limit)
                )
                if page_number is not None:
                    stmt = stmt.where(Citation.page_number == page_number)

                results = await asyncio.wait_for(
                    session.scalars(stmt),
                    timeout=_DB_TIMEOUT,
                )
                return [self._citation_to_dict(c, corr_id) for c in results]
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Get citations timed out after {_DB_TIMEOUT}s")
            return []
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to get citations: {e}")
            return []

    async def get_document_citation_stats(
        self,
        source_file: str,
        workspace_id: str = "default",
        correlation_id: Optional[str] = None,
    ) -> dict:
        """
        Citation statistics for a document.
        Shows which pages are most frequently cited.
        """
        corr_id = correlation_id or "citation_stats"

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(None, None, source_file, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return {
                "source_file": source_file,
                "total_citations": 0,
                "pages": [],
                "correlation_id": corr_id,
            }

        try:
            async with self._session_factory() as session:
                # Total citations
                total_stmt = select(func.count(Citation.id)).where(
                    Citation.source_file == source_file,
                    Citation.workspace_id == workspace_id,
                )
                total = (
                    await asyncio.wait_for(
                        session.scalar(total_stmt),
                        timeout=_DB_TIMEOUT,
                    )
                    or 0
                )

                # Citations per page
                page_stmt = (
                    select(
                        Citation.page_number,
                        func.count(Citation.id).label("citation_count"),
                        func.avg(Citation.confidence_score).label("avg_confidence"),
                    )
                    .where(
                        Citation.source_file == source_file,
                        Citation.workspace_id == workspace_id,
                    )
                    .group_by(Citation.page_number)
                    .order_by(desc("citation_count"))
                )
                page_results = await asyncio.wait_for(
                    session.execute(page_stmt),
                    timeout=_DB_TIMEOUT,
                )
                pages = [
                    {
                        "page_number": row.page_number,
                        "page_display": row.page_number + 1,
                        "citation_count": row.citation_count,
                        "avg_confidence": round(float(row.avg_confidence or 0), 3),
                    }
                    for row in page_results
                ]

                return {
                    "source_file": source_file,
                    "total_citations": total,
                    "pages": pages,
                    "correlation_id": corr_id,
                }
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Citation stats timed out after {_DB_TIMEOUT}s")
            return {
                "source_file": source_file,
                "total_citations": 0,
                "pages": [],
                "correlation_id": corr_id,
            }
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to get citation stats: {e}")
            return {
                "source_file": source_file,
                "total_citations": 0,
                "pages": [],
                "correlation_id": corr_id,
            }

    async def search_citations(
        self,
        query_text: str,
        workspace_id: str = "default",
        limit: int = 20,
        correlation_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Full-text search across stored citation chunks.
        ✅ FIXED: Use parameterized query to prevent SQL injection.
        """
        corr_id = correlation_id or "search_citations"

        # ✅ Validate inputs
        if not isinstance(query_text, str) or not query_text.strip():
            logger.error(f"[{corr_id}] query_text must be a non-empty string")
            return []

        is_valid, error = self._validate_inputs(None, None, None, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return []

        try:
            async with self._session_factory() as session:
                stmt = text(
                    "SELECT * FROM citations WHERE workspace_id = :ws AND chunk_text ILIKE :query "
                    "ORDER BY confidence_score DESC LIMIT :lim"
                ).bindparams(
                    ws=workspace_id,
                    query=f"%{query_text}%",  # Safe: parameterized, not string interpolation
                    lim=limit,
                )
                # Note: For full SQLAlchemy ORM support, use proper ORM query with ilike
                # This is a simplified version — in production, use ORM with proper escaping
                results = await asyncio.wait_for(
                    session.execute(stmt),
                    timeout=_DB_TIMEOUT,
                )
                # Convert rows to Citation objects for serialization
                citations = []
                for row in results:
                    # This is a simplified approach — in production, use proper ORM mapping
                    citations.append(row._mapping)
                return [self._citation_to_dict(c, corr_id) for c in citations]
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Search citations timed out after {_DB_TIMEOUT}s")
            return []
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to search citations: {e}")
            return []

    async def delete_document_provenance(
        self,
        source_file: str,
        workspace_id: str = "default",
        correlation_id: Optional[str] = None,
    ) -> int:
        """
        Delete all citations for a document when it's removed.
        Returns number of citations deleted.
        """
        corr_id = correlation_id or "delete_provenance"

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(None, None, source_file, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return 0

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    stmt = delete(Citation).where(
                        Citation.source_file == source_file,
                        Citation.workspace_id == workspace_id,
                    )
                    result = await asyncio.wait_for(
                        session.execute(stmt),
                        timeout=_DB_TIMEOUT,
                    )
                    deleted = result.rowcount

                    # Also remove document store entry
                    doc_stmt = delete(DocumentStore).where(
                        DocumentStore.source_file == source_file,
                        DocumentStore.workspace_id == workspace_id,
                    )
                    await session.execute(doc_stmt)

            logger.info(f"[{corr_id}] Provenance deleted: {source_file} | {deleted} citations")
            return deleted
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Delete provenance timed out after {_DB_TIMEOUT}s")
            return 0
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to delete provenance: {e}")
            return 0

    # -- Serialization ---------------------------------------------------------

    @staticmethod
    def _answer_to_dict(answer: Answer, correlation_id: Optional[str] = None) -> dict:
        """Convert Answer model to API-ready dict."""
        return {
            "answer_id": str(answer.id),
            "question": answer.question,
            "answer_text": answer.answer_text,
            "workspace_id": answer.workspace_id,
            "thread_id": answer.thread_id,
            "retrieval_mode": answer.retrieval_mode,
            "confidence_score": answer.confidence_score,
            "latency_seconds": answer.latency_seconds,
            "created_at": format_iso(answer.created_at),
            "correlation_id": answer.correlation_id or correlation_id,
            "citations": [ProvenanceStore._citation_to_dict(c, correlation_id) for c in (answer.citations or [])],
        }

    @staticmethod
    def _citation_to_dict(citation: Citation, correlation_id: Optional[str] = None) -> dict:
        """Convert Citation model to API-ready dict."""
        return {
            "citation_id": str(citation.id),
            "answer_id": str(citation.answer_id),
            "source_file": citation.source_file,
            "page_number": citation.page_number,
            "page_display": citation.page_number + 1,
            "chunk_id": citation.chunk_id,
            "chunk_text": citation.chunk_text,
            "confidence_score": citation.confidence_score,
            "block_type": citation.block_type,
            "char_offset_start": citation.char_offset_start,
            "char_offset_end": citation.char_offset_end,
            "highlight_color": citation.highlight_color,
            "created_at": format_iso(citation.created_at),
            "correlation_id": citation.correlation_id or correlation_id,
        }


def get_provenance_metadata() -> dict[str, Any]:
    """✅ NEW: Return provenance metadata for monitoring."""
    return {
        "db_timeout": _DB_TIMEOUT,
        "engine_cache_size": len(_engines),
        "session_factory_cache_size": len(_session_factories),
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "ProvenanceStore",
    "init_db",
    "get_engine",
    "get_session_factory",
    "get_provenance_metadata",
]
# Local smoke test entry point. Run: python -m

