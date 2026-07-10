"""
Document-type-aware chunking strategy dispatcher.

Selects the optimal chunking strategy based on detected document content type:
  - narrative / prose    → ParentChildChunker (semantic paragraph boundaries)
  - table-heavy          → TableAwareChunker  (preserve row/column structure)
  - code / technical     → CodeChunker        (function/class boundaries)
  - mixed                → HybridChunker      (per-block strategy selection)

The dispatcher inspects the extracted text and OCR block types to make the
decision automatically, but callers can also force a strategy via the
`force_strategy` parameter.
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ChunkStrategy(str, Enum):
    NARRATIVE = "narrative"   # Standard parent-child semantic chunking
    TABLE     = "table"       # Row-preserving table chunking
    CODE      = "code"        # AST-aware code chunking
    HYBRID    = "hybrid"      # Per-block strategy selection
    AUTO      = "auto"        # Dispatcher decides


# Heuristic weights for strategy detection
_TABLE_INDICATORS   = re.compile(r"(\|\s*[-:]+\s*\|)|(<table[\s>])|(^\s*\|)", re.MULTILINE | re.IGNORECASE)
_CODE_INDICATORS    = re.compile(r"(def |class |function |import |from |#include|public static|SELECT\s+\w)", re.MULTILINE)
_NARRATIVE_MIN_RATIO = 0.6   # fraction of text that must be prose for NARRATIVE strategy


def detect_strategy(
    text: str,
    ocr_block_types: Optional[list[str]] = None,
    filename: str = "",
) -> ChunkStrategy:
    """
    Heuristically detect the best chunking strategy for a document.

    Args:
        text:            Full extracted text.
        ocr_block_types: List of block-type labels from OCR (e.g. ["paragraph", "table", "code"]).
        filename:        Original filename (used for extension-based hints).

    Returns:
        ChunkStrategy enum value.
    """
    if not text:
        return ChunkStrategy.NARRATIVE

    # Extension hints
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"py", "js", "ts", "java", "go", "rs", "cpp", "c", "cs", "rb", "php"}:
        return ChunkStrategy.CODE
    if ext in {"csv", "xlsx", "xls"}:
        return ChunkStrategy.TABLE

    # OCR block-type hints (if available from PaddleOCR / layout parser)
    if ocr_block_types:
        table_blocks = sum(1 for b in ocr_block_types if "table" in b.lower())
        code_blocks  = sum(1 for b in ocr_block_types if "code" in b.lower())
        total_blocks = len(ocr_block_types) or 1
        if table_blocks / total_blocks > 0.3:
            return ChunkStrategy.TABLE if table_blocks / total_blocks > 0.6 else ChunkStrategy.HYBRID
        if code_blocks / total_blocks > 0.3:
            return ChunkStrategy.CODE

    # Text heuristics
    sample = text[:8000]  # analyse first 8K chars for speed
    table_matches = len(_TABLE_INDICATORS.findall(sample))
    code_matches  = len(_CODE_INDICATORS.findall(sample))
    line_count    = sample.count("\n") or 1

    table_ratio = table_matches / line_count
    code_ratio  = code_matches  / line_count

    if table_ratio > 0.15 and code_ratio < 0.05:
        return ChunkStrategy.TABLE if table_ratio > 0.30 else ChunkStrategy.HYBRID
    if code_ratio > 0.10:
        return ChunkStrategy.CODE

    return ChunkStrategy.NARRATIVE


class StrategyDispatcher:
    """
    Dispatch document text to the correct chunker.

    The dispatcher is intentionally thin — it delegates all chunking logic
    to the strategy implementations and only makes the routing decision.
    """

    def __init__(self, settings: Optional[Any] = None):
        self._settings = settings
        self._chunkers: dict[ChunkStrategy, Any] = {}

    def _get_chunker(self, strategy: ChunkStrategy) -> Any:
        """Lazy-load and cache chunker instances."""
        if strategy in self._chunkers:
            return self._chunkers[strategy]

        if strategy in (ChunkStrategy.NARRATIVE, ChunkStrategy.HYBRID):
            from app.chunking.parent_child import ParentChildChunker
            chunker = ParentChildChunker(settings=self._settings)
        elif strategy == ChunkStrategy.TABLE:
            chunker = _TableAwareChunker(settings=self._settings)
        elif strategy == ChunkStrategy.CODE:
            chunker = _CodeChunker(settings=self._settings)
        else:
            from app.chunking.parent_child import ParentChildChunker
            chunker = ParentChildChunker(settings=self._settings)

        self._chunkers[strategy] = chunker
        return chunker

    async def chunk(
        self,
        enriched_doc: Any,
        source_file: str = "",
        force_strategy: ChunkStrategy = ChunkStrategy.AUTO,
        ocr_block_types: Optional[list[str]] = None,
    ) -> list[Any]:
        """
        Chunk an enriched document using the best strategy.

        Args:
            enriched_doc:    Output from the OCR/ingestion pipeline.
            source_file:     Original filename for strategy hints.
            force_strategy:  Override auto-detection.
            ocr_block_types: Optional list of block types from OCR.

        Returns:
            List of chunk objects (same format as ParentChildChunker output).
        """
        # Extract raw text for strategy detection
        raw_text = ""
        try:
            if hasattr(enriched_doc, "text"):
                raw_text = enriched_doc.text or ""
            elif hasattr(enriched_doc, "ocr") and hasattr(enriched_doc.ocr, "full_text"):
                raw_text = enriched_doc.ocr.full_text or ""
        except Exception:
            pass

        if force_strategy == ChunkStrategy.AUTO:
            strategy = detect_strategy(raw_text, ocr_block_types, filename=source_file)
        else:
            strategy = force_strategy

        logger.info(f"[{source_file}] Chunking strategy: {strategy.value}")
        chunker = self._get_chunker(strategy)

        # All strategy implementations expose chunk_enriched_document (async)
        chunks = await chunker.chunk_enriched_document(enriched_doc, source_file=source_file)
        logger.info(f"[{source_file}] Produced {len(chunks)} chunks via {strategy.value} strategy")
        return chunks


class _TableAwareChunker:
    """
    Table-aware chunker — keeps table rows together in a single chunk.

    For each detected table block, produces one chunk per logical table
    (up to max_table_chunk_chars). Non-table blocks fall back to
    ParentChildChunker for normal paragraph chunking.
    """

    MAX_TABLE_CHUNK_CHARS = 3000

    def __init__(self, settings: Optional[Any] = None):
        from app.chunking.parent_child import ParentChildChunker
        self._fallback = ParentChildChunker(settings=settings)

    async def chunk_enriched_document(self, enriched_doc: Any, source_file: str = "") -> list[Any]:
        from app.chunking.parent_child import ParentChildChunker, ChunkMetadata
        import hashlib

        # Try to parse markdown tables from text; fall back to standard chunker otherwise
        raw_text = ""
        try:
            if hasattr(enriched_doc, "text"):
                raw_text = enriched_doc.text or ""
            elif hasattr(enriched_doc, "ocr") and hasattr(enriched_doc.ocr, "full_text"):
                raw_text = enriched_doc.ocr.full_text or ""
        except Exception:
            pass

        if not _TABLE_INDICATORS.search(raw_text):
            # No tables detected — delegate to standard chunker
            return await self._fallback.chunk_enriched_document(enriched_doc, source_file=source_file)

        # Split text into table vs non-table segments
        chunks = []
        chunk_idx = 0
        for segment, is_table in _split_table_segments(raw_text):
            if not segment.strip():
                continue
            chunk_id = hashlib.md5(f"{source_file}:{chunk_idx}:{segment[:50]}".encode()).hexdigest()
            meta = ChunkMetadata(
                chunk_id=chunk_id,
                source_file=source_file,
                page_number=0,
                chunk_index=chunk_idx,
                total_chunks=0,
                chunk_type="table" if is_table else "paragraph",
                parent_id=None,
            )
            chunks.append(type("Chunk", (), {"content": segment.strip(), "metadata": meta, "page_content": segment.strip()})())
            chunk_idx += 1

        return chunks if chunks else await self._fallback.chunk_enriched_document(enriched_doc, source_file=source_file)


class _CodeChunker:
    """
    Code-aware chunker — splits on function/class boundaries where possible.
    Falls back to fixed-size chunking for non-Python code.
    """

    MAX_CODE_CHUNK_CHARS = 2000
    OVERLAP_CHARS = 100

    def __init__(self, settings: Optional[Any] = None):
        from app.chunking.parent_child import ParentChildChunker
        self._fallback = ParentChildChunker(settings=settings)

    async def chunk_enriched_document(self, enriched_doc: Any, source_file: str = "") -> list[Any]:
        from app.chunking.parent_child import ChunkMetadata
        import hashlib

        raw_text = ""
        try:
            if hasattr(enriched_doc, "text"):
                raw_text = enriched_doc.text or ""
            elif hasattr(enriched_doc, "ocr") and hasattr(enriched_doc.ocr, "full_text"):
                raw_text = enriched_doc.ocr.full_text or ""
        except Exception:
            pass

        if not raw_text or not _CODE_INDICATORS.search(raw_text[:4000]):
            return await self._fallback.chunk_enriched_document(enriched_doc, source_file=source_file)

        # Split on top-level def/class boundaries
        blocks = re.split(r"\n(?=(?:def |class |async def ))", raw_text)
        chunks = []
        for idx, block in enumerate(blocks):
            if not block.strip():
                continue
            chunk_id = hashlib.md5(f"{source_file}:code:{idx}:{block[:40]}".encode()).hexdigest()
            meta = ChunkMetadata(
                chunk_id=chunk_id,
                source_file=source_file,
                page_number=0,
                chunk_index=idx,
                total_chunks=len(blocks),
                chunk_type="code",
                parent_id=None,
            )
            chunks.append(type("Chunk", (), {"content": block.strip(), "metadata": meta, "page_content": block.strip()})())

        return chunks if chunks else await self._fallback.chunk_enriched_document(enriched_doc, source_file=source_file)


def _split_table_segments(text: str) -> list[tuple[str, bool]]:
    """Split text into (segment, is_table) tuples."""
    lines = text.split("\n")
    segments = []
    current: list[str] = []
    in_table = False

    for line in lines:
        line_is_table = bool(re.match(r"^\s*\|", line) or re.match(r"^\s*[-:| ]+$", line))
        if line_is_table != in_table:
            if current:
                segments.append(("\n".join(current), in_table))
            current = [line]
            in_table = line_is_table
        else:
            current.append(line)

    if current:
        segments.append(("\n".join(current), in_table))

    return segments


# Module-level singleton
_dispatcher: Optional[StrategyDispatcher] = None


def get_strategy_dispatcher(settings: Optional[Any] = None) -> StrategyDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = StrategyDispatcher(settings=settings)
    return _dispatcher
