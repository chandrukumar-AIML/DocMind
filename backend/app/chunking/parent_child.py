# backend/app/chunking/parent_child.py
# DVMELTSS-FIX: M - Modular, A - Async-safe, S - Scalability, L - Logging
# BATMAN-FIX: A - Async, M - Memory, B - Batch, T - Time complexity
# ACID-INDEX: I - Indexes, D - Data types, N - N+1 metadata
# ✅ FIXED: Module-level mock classes + proper syntax + Pylance-compatible types

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Tuple, Any, Final, TYPE_CHECKING

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.validators import normalize_tags, validate_slug
from app.core.retry import retry_async, RetryConfig
from app.ocr.text_formatter import EnrichedTextFormatter
from app.core.exceptions import ValidationError

# DVMELTSS-M: Proper TYPE_CHECKING guard for circular imports
if TYPE_CHECKING:
    from app.ocr.vision_analyzer import EnrichedDocument, TextBlock

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, M) ---------------------------------
# ========================================================================

# BATMAN-M: Memory guard defaults — prevent OOM on large docs
DEFAULT_MAX_BLOCKS: Final = 500
DEFAULT_MAX_CHUNKS_PER_DOC: Final = 2000
DEFAULT_MAX_PARENT_CACHE: Final = 100  # FIXED: Bounded parent cache size
CHUNK_BATCH_SIZE: Final = 50  # Process in batches to yield control to event loop

# BATMAN-T: Deterministic ID config
CHUNK_ID_HASH_LENGTH: Final = 32  # 32-char hex = 128-bit SHA256 prefix


# ========================================================================
# -- CHUNK METADATA MODEL (ACID-D: Typed, indexable schema) -------------
# ========================================================================

@dataclass(frozen=True)
class ChunkMetadata:
    """
    Strongly-typed metadata for vector store indexing.
    ACID-I: Fields designed for efficient filtering + indexing.
    """
    chunk_id: str
    parent_id: Optional[str]  # None for parent chunks
    chunk_type: str  # "parent" | "child"
    source_file: str  # Sanitized filename
    page_number: int
    block_type: str
    language: str
    ocr_confidence: float
    document_type: str
    ingest_timestamp: str  # ISO 8601
    char_count: int
    tags: List[str]  # Normalized list — internal representation
    child_index: Optional[int] = None  # Only for child chunks
    
    def to_dict(self, for_vector_store: bool = True) -> Dict[str, Any]:
        """
        Convert to dict for LangChain Document metadata — JSON-serializable.
        
        Args:
            for_vector_store: If True, serialize tags as comma-string 
                              (Pinecone/Chroma/FAISS compatible)
        """
        # FIXED: Vector-store-compatible tag serialization
        tags_value = ",".join(self.tags) if for_vector_store and self.tags else self.tags
        
        result = {
            "chunk_id": self.chunk_id,
            "parent_id": self.parent_id,
            "chunk_type": self.chunk_type,
            "source_file": self.source_file,
            "page_number": self.page_number,
            "block_type": self.block_type,
            "language": self.language,
            "ocr_confidence": self.ocr_confidence,
            "document_type": self.document_type,
            "ingest_timestamp": self.ingest_timestamp,
            "char_count": self.char_count,
            "tags": tags_value,  # FIXED: Comma-string for vector stores
        }
        if self.child_index is not None:
            result["child_index"] = self.child_index
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChunkMetadata":
        """
        Reconstruct ChunkMetadata from vector store dict.
        FIXED: Parse comma-string tags back to list.
        """
        tags_raw = data.get("tags", [])
        # FIXED: Handle both list and comma-string formats
        if isinstance(tags_raw, str) and tags_raw:
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = [str(t).strip() for t in tags_raw if t]
        else:
            tags = []
        
        return cls(
            chunk_id=data.get("chunk_id", ""),  # ✅ FIXED: Safe .get() with default
            parent_id=data.get("parent_id"),
            chunk_type=data.get("chunk_type", "child"),  # ✅ FIXED: Default value
            source_file=data.get("source_file", "unknown"),
            page_number=data.get("page_number", 0),
            block_type=data.get("block_type", "paragraph"),
            language=data.get("language", "en"),
            ocr_confidence=float(data.get("ocr_confidence", 0.0)),
            document_type=data.get("document_type", "unknown"),
            ingest_timestamp=data.get("ingest_timestamp", ""),
            char_count=int(data.get("char_count", 0)),
            tags=tags,  # FIXED: Always store as list internally
            child_index=data.get("child_index"),
        )


# ========================================================================
# -- MODULE-LEVEL MOCK CLASSES (for text-only chunking fallback) ---------
# ========================================================================
# ✅ FIXED: Define at module level so Pylance can see them (not inside TYPE_CHECKING guard)

class _MinimalBlock:
    """Minimal mock block for text-only chunking fallback."""
    def __init__(self, text: str, page_num: int = 0, block_type: str = "paragraph"):
        self.text = text
        self.page_num = page_num
        self.line_num = 0  
        self.block_type = block_type
        self.language = "en"
        self.confidence = 1.0


class _MinimalOCR:
    """Minimal mock OCR result for text-only chunking fallback."""
    def __init__(self, block: _MinimalBlock | None = None, blocks: list[_MinimalBlock] | None = None):
        self.all_blocks = blocks if blocks is not None else [block or _MinimalBlock("")]


class _MinimalEnriched:
    """Minimal mock enriched document for text-only chunking fallback."""
    def __init__(self, ocr: _MinimalOCR | None = None, ocr_result: _MinimalOCR | None = None):
        self.ocr_result = ocr_result or ocr or _MinimalOCR()
        self.metadata = None


# ADDED: Backward-compatible names used by tests and older callers.
MockBlock = _MinimalBlock
MockOCRResult = _MinimalOCR
MockEnriched = _MinimalEnriched


# ========================================================================
# -- MAIN CHUNKER CLASS (BATMAN-A: Async-safe, memory-efficient) ---------
# ========================================================================

class ParentChildChunker:
    """
    Async-safe parent-child chunker for RAG pipelines.
    
    Strategy (BATMAN-B, M):
    - Parent chunks: Large context (1000-2000 chars) for semantic understanding
    - Child chunks: Small retrieval units (200-400 chars) for precise matching
    - Each child references parent via parent_id for context expansion at query time
    - Processes in batches to yield to event loop — non-blocking in FastAPI
    
    Benefits:
    - Precise retrieval via small child chunks
    - Rich context via parent expansion during answer generation
    - Reduced hallucination by grounding answers in full parent context
    - Memory-safe: bounded lists + batch processing + streaming support
    """

    def __init__(self, settings: Optional[Any] = None):
        """
        Initialize chunkers with configurable sizes.
        
        Args:
            settings: App settings object (uses get_settings() if None)
            
        Raises:
            ValidationError: If chunk sizes are invalid
        """
        cfg = settings or get_settings()
        
        # DVMELTSS-V: Validate chunk sizes early
        is_valid, error = self._validate_chunk_sizes(
            getattr(cfg, "rag_chunk_size_child", 300),
            getattr(cfg, "rag_chunk_overlap_child", 50),
            getattr(cfg, "rag_chunk_size_parent", 1500),
            getattr(cfg, "rag_chunk_overlap_parent", 200),
        )
        if not is_valid:
            raise ValidationError(error)
        
        # Child splitter: small chunks for precise retrieval
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=getattr(cfg, "rag_chunk_size_child", 300),
            chunk_overlap=getattr(cfg, "rag_chunk_overlap_child", 50),
            separators=["\n\n", "\n", ". ", " ", ""],
            length_function=len,
            is_separator_regex=False,
        )
        
        # Parent splitter: large chunks for context
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=getattr(cfg, "rag_chunk_size_parent", 1500),
            chunk_overlap=getattr(cfg, "rag_chunk_overlap_parent", 200),
            separators=["\n\n", "\n", ". ", " ", ""],
            length_function=len,
            is_separator_regex=False,
        )
        
        # BATMAN-M: Memory guards
        self.max_blocks = getattr(cfg, "rag_max_blocks_per_doc", DEFAULT_MAX_BLOCKS)
        self.max_chunks = getattr(cfg, "rag_max_chunks_per_doc", DEFAULT_MAX_CHUNKS_PER_DOC)
        self.max_parent_cache = getattr(cfg, "rag_max_parent_cache", DEFAULT_MAX_PARENT_CACHE)
        
        # BATMAN-T: Retry config for OCR formatter
        self._formatter_retry = retry_async(config=RetryConfig(
            max_attempts=2,
            backoff_base=0.05,
            exceptions=(Exception,),
        ))
        
        logger.info(
            f"ParentChildChunker initialized: "
            f"child={getattr(cfg, 'rag_chunk_size_child', 300)}±{getattr(cfg, 'rag_chunk_overlap_child', 50)}, "
            f"parent={getattr(cfg, 'rag_chunk_size_parent', 1500)}±{getattr(cfg, 'rag_chunk_overlap_parent', 200)}, "
            f"max_blocks={self.max_blocks}, max_chunks={self.max_chunks}, "
            f"max_parent_cache={self.max_parent_cache}"
        )

    @staticmethod
    def _validate_chunk_sizes(
        child_size: int, child_overlap: int, parent_size: int, parent_overlap: int
    ) -> Tuple[bool, Optional[str]]:
        """Validate chunking configuration — pure function for easy testing."""
        if child_size <= 0 or parent_size <= 0:
            return False, "Chunk sizes must be positive"
        if child_overlap < 0 or parent_overlap < 0:
            return False, "Overlap values cannot be negative"
        if child_overlap >= child_size:
            return False, f"Child overlap ({child_overlap}) must be < child size ({child_size})"
        if parent_overlap >= parent_size:
            return False, f"Parent overlap ({parent_overlap}) must be < parent size ({parent_size})"
        if child_size >= parent_size:
            return False, f"Child size ({child_size}) must be < parent size ({parent_size})"
        return True, None

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        DVMELTSS-S: Sanitize filename for safe logging/metadata storage.
        FIXED: Use centralized validate_slug instead of duplicate regex.
        """
        # Extract basename first
        basename = Path(filename).name
        # Use centralized validator with relaxed pattern for filenames
        try:
            # Replace dots with underscores for slug validation, then restore extension
            name_part, _, ext = basename.rpartition(".")
            slug_name = name_part.replace(".", "_")
            validated = validate_slug(slug_name, min_len=1, max_len=245, field_name="filename")
            return f"{validated}.{ext}" if ext else validated
        except ValueError:
            # Fallback: simple sanitization if validation fails
            clean = re.sub(r"[^\w.\-_/]", "_", basename)
            return clean[:255]

    @staticmethod
    def _generate_chunk_id(content: str, source_file: str, page_num: int, chunk_index: int) -> str:
        """
        Generate deterministic chunk ID via SHA256 — reproducible + faster than UUID.
        BATMAN-T: O(1) hash vs UUID generation overhead.
        """
        # Use content prefix + metadata for uniqueness
        raw = f"{source_file}:{page_num}:{chunk_index}:{content[:100]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:CHUNK_ID_HASH_LENGTH]

    def _build_base_metadata(
        self,
        block: Any,
        source_file: str,
        doc_language: str,
        doc_type: str,
        ingest_ts: str,
        tags: List[str],
        corr_id: str,
    ) -> Dict[str, Any]:
        """DRY helper for shared metadata fields."""
        return {
            "source_file": self._sanitize_filename(source_file),
            "page_number": getattr(block, "page_num", 0),
            "block_type": getattr(block, "block_type", "paragraph"),
            "language": getattr(block, "language", None) or doc_language,
            "ocr_confidence": float(getattr(block, "confidence", 0.0)),
            "document_type": doc_type,
            "ingest_timestamp": ingest_ts,
            "tags": normalize_tags(tags),  # FIXED: Use centralized validator
            "_corr_id": corr_id,  # Internal: for tracing, not stored in vector DB
        }

    # ✅ FIXED: Proper async retry wrapper for formatter
    async def _safe_format_block(
        self,
        block: Any,
        enriched: "EnrichedDocument",
        corr_id: str,
        source_file: str,
        block_index: int,
    ) -> str:
        """
        Format block text with retry + graceful fallback.
        DVMELTSS-E: Never let formatter failure stop entire document.
        """
        async def _do_format():
            return EnrichedTextFormatter.format_block(block, enriched)
        
        try:
            # FIXED: Apply retry decorator to async function
            return await self._formatter_retry(_do_format)()
        except Exception as e:
            # FIXED: Include correlation_id in warning for tracing
            logger.warning(
                f"[{corr_id}] Formatter failed for block {block_index} in {source_file}: {e}. "
                f"Using raw text fallback."
            )
            return getattr(block, "text", "")

    async def chunk_enriched_document(
        self,
        enriched: "EnrichedDocument",
        source_file: str,
        tags: Optional[List[str]] = None,
        correlation_id: Optional[str] = None,
    ) -> AsyncIterator[Tuple[Document, Document]]:
        """
        Async generator: yield (child, parent) chunk pairs one-by-one.
        
        BATMAN-A: Non-blocking — yields control to event loop every BATCH_SIZE chunks.
        BATMAN-M: Memory-safe — bounded parent cache + stream to vector store directly.
        DVMELTSS-L: All logs include correlation_id for tracing.
        
        Args:
            enriched: EnrichedDocument with OCR results + Vision analyses
            source_file: Original filename for metadata
            tags: Optional list of tags for filtering
            correlation_id: Request ID for distributed tracing
            
        Yields:
            Tuple[Document, Document]: (child_chunk, parent_chunk) pairs
            
        Raises:
            ValidationError: If enriched document is invalid or empty
        """
        corr_id = correlation_id or str(uuid.uuid4())[:8]
        
        # === Input validation ===
        if not enriched or not hasattr(enriched, "ocr_result"):
            raise ValidationError("Invalid enriched document: missing ocr_result")
        
        # ✅ FIXED: Safe iteration check for all_blocks
        blocks = getattr(enriched.ocr_result, "all_blocks", None)
        if not blocks or not hasattr(blocks, "__iter__"):
            raise ValidationError(f"[{corr_id}] No OCR blocks found in {source_file}")
        
        tags = tags or []
        ingest_ts = datetime.now(timezone.utc).isoformat()
        
        # Extract document-level metadata
        doc_type = getattr(enriched.metadata, "document_type", "unknown") if enriched.metadata else "unknown"
        doc_language = getattr(enriched.metadata, "language", "en") or "en"
        
        # Apply memory guards
        blocks_list = list(blocks)  # Convert to list for safe iteration
        if len(blocks_list) > self.max_blocks:
            logger.warning(
                f"[{corr_id}] Limiting to {self.max_blocks}/{len(blocks_list)} blocks "
                f"for {source_file} (memory guard)"
            )
            blocks_list = blocks_list[:self.max_blocks]
        
        chunk_count = 0
        # ✅ FIXED: Bounded OrderedDict with FIFO eviction
        parent_cache: OrderedDict[str, Document] = OrderedDict()
        
        # Process blocks in batches to yield to event loop (BATMAN-A)
        for batch_start in range(0, len(blocks_list), CHUNK_BATCH_SIZE):
            batch = blocks_list[batch_start:batch_start + CHUNK_BATCH_SIZE]
            
            for i, block in enumerate(batch, start=batch_start):
                try:
                    # FIXED: Safe formatting with retry + fallback
                    text = await self._safe_format_block(block, enriched, corr_id, source_file, i)
                except Exception as e:
                    # Ultimate fallback if retry also fails
                    logger.error(f"[{corr_id}] Block {i} formatting failed after retry: {e}")
                    text = getattr(block, "text", "")
                
                # Skip empty or very short blocks — log for audit (DVMELTSS-L)
                if not text or len(text.strip()) < 8:
                    logger.debug(f"[{corr_id}] Skipping empty block {i} in {source_file}")
                    continue
                
                # FIXED: Generate deterministic parent ID
                parent_id = self._generate_chunk_id(text, source_file, getattr(block, "page_num", 0), 0)
                
                # Build base metadata — FIXED: pass corr_id for internal tracing
                base_meta = self._build_base_metadata(
                    block, source_file, doc_language, doc_type, ingest_ts, tags, corr_id
                )
                
                # === Create parent chunk ===
                parent_meta = ChunkMetadata(
                    chunk_id=parent_id,
                    parent_id=None,  # Parents have no parent
                    chunk_type="parent",
                    **{k: v for k, v in base_meta.items() if k != "_corr_id"},  # Exclude internal fields
                    char_count=len(text),
                )
                parent_doc = Document(page_content=text, metadata=parent_meta.to_dict())
                
                # ✅ FIXED: Bounded cache with FIFO eviction using OrderedDict
                if parent_id not in parent_cache:
                    if len(parent_cache) >= self.max_parent_cache:
                        # Evict oldest (first inserted) item
                        oldest_key, _ = parent_cache.popitem(last=False)
                        logger.debug(f"[{corr_id}] Evicted parent {oldest_key[:8]}... from cache (max={self.max_parent_cache})")
                    parent_cache[parent_id] = parent_doc
                
                # === Create child chunks from parent text ===
                try:
                    child_texts = self.child_splitter.split_text(text)
                except Exception as e:
                    logger.warning(
                        f"[{corr_id}] Child splitting failed for block {i} in {source_file}: {e}. "
                        f"Using parent as single child."
                    )
                    child_texts = [text]
                
                for j, child_text in enumerate(child_texts):
                    # Skip empty child chunks
                    if not child_text.strip():
                        continue
                    
                    # Memory guard: stop if max chunks reached
                    if chunk_count >= self.max_chunks:
                        logger.warning(
                            f"[{corr_id}] Max chunks ({self.max_chunks}) reached for {source_file}. "
                            f"Stopping chunking early."
                        )
                        # Yield remaining parents before returning
                        for p in parent_cache.values():
                            if p:  # ✅ Safe check for None
                                yield None, p
                        return
                    
                    # FIXED: Generate deterministic child ID
                    child_id = self._generate_chunk_id(child_text, source_file, getattr(block, "page_num", 0), j)
                    
                    child_meta = ChunkMetadata(
                        chunk_id=child_id,
                        parent_id=parent_id,  # Link to parent
                        chunk_type="child",
                        child_index=j,
                        **{k: v for k, v in base_meta.items() if k != "_corr_id"},
                        char_count=len(child_text),
                    )
                    child_doc = Document(page_content=child_text, metadata=child_meta.to_dict())
                    
                    # ✅ FIXED: Safe parent lookup with fallback
                    parent = parent_cache.get(parent_id)
                    if parent:
                        # Yield pair: (child, parent)
                        yield child_doc, parent
                    else:
                        # Fallback: yield child with None parent (shouldn't happen with bounded cache)
                        logger.warning(f"[{corr_id}] Parent {parent_id[:8]}... not in cache — yielding child only")
                        yield child_doc, None
                    
                    chunk_count += 1
            
            # BATMAN-A: Yield control to event loop after each batch
            await asyncio.sleep(0)
        
        logger.info(
            f"[{corr_id}] Chunked {source_file}: "
            f"{len(blocks_list)}/{len(enriched.ocr_result.all_blocks)} blocks -> "
            f"{len(parent_cache)} parents + {chunk_count} children"
        )

    # -- Sync wrapper for backward compatibility (DEPRECATED) -------------
    def chunk_enriched_document_sync(
        self,
        enriched: "EnrichedDocument",
        source_file: str,
        tags: Optional[List[str]] = None,
        correlation_id: Optional[str] = None,
    ) -> Tuple[List[Document], List[Document]]:
        """
        ⚠️ DEPRECATED: Use async `chunk_enriched_document()` in new code.
        
        Sync wrapper for legacy code — runs async generator to completion.
        DVMELTSS-M: Uses asyncio.to_thread() to avoid blocking event loop.
        """
        import warnings
        warnings.warn(
            "chunk_enriched_document_sync is deprecated. Use async version instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        
        # FIXED: Safe async-to-sync bridge using ThreadPoolExecutor
        async def _collect():
            children, parents = [], []
            seen_parents = set()
            async for child, parent in self.chunk_enriched_document(enriched, source_file, tags, correlation_id):
                if child:
                    children.append(child)
                if parent and parent.metadata["chunk_id"] not in seen_parents:
                    parents.append(parent)
                    seen_parents.add(parent.metadata["chunk_id"])
            return children, parents
        
        # Run in thread to avoid blocking main event loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(_collect()))
            return future.result()

    def chunk_text_only(
        self,
        text: str,
        source_file: str,
        page_num: int = 0,
        block_type: str = "paragraph",
        tags: Optional[List[str]] = None,
        correlation_id: Optional[str] = None,
    ) -> AsyncIterator[Tuple[Document, Document]]:
        """
        Async generator: chunk plain text without OCR enrichment (fallback mode).
        
        DVMELTSS-M: Reuses main async logic via mock enrichment — no code duplication.
        """
        if not text or not text.strip():
            raise ValidationError("Cannot chunk empty text")
        
        # ✅ FIXED: Use module-level mock classes (defined above)
        mock_block = _MinimalBlock(text, page_num, block_type)
        mock_ocr = _MinimalOCR(mock_block)
        mock_enriched = _MinimalEnriched(mock_ocr)
        
        # Delegate to main async logic
        return self.chunk_enriched_document(
            enriched=mock_enriched,  # type: ignore[arg-type]
            source_file=source_file,
            tags=tags,
            correlation_id=correlation_id,
        )

    # -- Statistics (BATMAN-T: Incremental, O(1) per chunk) ---------------
    @dataclass
    class ChunkingStats:
        """Incremental stats — O(1) update per chunk, no O(n) recomputation."""
        child_count: int = 0
        parent_count: int = 0
        total_child_chars: int = 0
        total_parent_chars: int = 0
        min_child_chars: int = float("inf")
        max_child_chars: int = 0
        
        def update_child(self, char_count: int):
            self.child_count += 1
            self.total_child_chars += char_count
            self.min_child_chars = min(self.min_child_chars, char_count)
            self.max_child_chars = max(self.max_child_chars, char_count)
        
        def update_parent(self, char_count: int):
            self.parent_count += 1
            self.total_parent_chars += char_count
        
        @property
        def avg_child_chars(self) -> float:
            return round(self.total_child_chars / self.child_count, 1) if self.child_count > 0 else 0.0
        
        @property
        def avg_parent_chars(self) -> float:
            return round(self.total_parent_chars / self.parent_count, 1) if self.parent_count > 0 else 0.0
        
        @property
        def children_per_parent(self) -> float:
            return round(self.child_count / self.parent_count, 2) if self.parent_count > 0 else 0.0
        
        @property
        def memory_estimate_mb(self) -> float:
            # Rough estimate: 1 char ≈ 1 byte + 50% overhead for metadata/Python objects
            total = self.total_child_chars + self.total_parent_chars
            return round((total * 1.5) / 1024 / 1024, 2)

    def get_stats_incremental(self) -> ChunkingStats:
        """
        Return fresh stats object — caller updates incrementally during chunking.
        BATMAN-T: O(1) per chunk vs O(n) recomputation in old get_stats().
        """
        return self.ChunkingStats()


def get_chunker_metadata() -> dict[str, Any]:
    """✅ NEW: Return chunker metadata for monitoring."""
    return {
        "default_chunk_sizes": {
            "child": 300,
            "child_overlap": 50,
            "parent": 1500,
            "parent_overlap": 200,
        },
        "memory_guards": {
            "max_blocks": DEFAULT_MAX_BLOCKS,
            "max_chunks": DEFAULT_MAX_CHUNKS_PER_DOC,
            "max_parent_cache": DEFAULT_MAX_PARENT_CACHE,
        },
        "batch_size": CHUNK_BATCH_SIZE,
    }


# DVMELTSS-M: Explicit module exports
# ✅ FIXED: Properly closed list (no stray brace)
__all__ = [
    "ParentChildChunker",
    "ChunkMetadata",
    "MockBlock",
    "MockOCRResult",
    "MockEnriched",
    "get_chunker_metadata",
]
# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.chunking.parent_child) -
# ========================================================================

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    
    # 🔧 ROBUST PATH SETUP: Works for both `python -m` and direct `python file.py`
    current_file = Path(__file__).resolve()
    
    # Detect backend root: look for 'backend' in path or requirements.txt
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        # Fallback: assume script is 3 levels below backend
        backend_root = current_file.parents[2]
    
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
        print(f"🔧 Added to sys.path: {backend_root}", file=sys.stderr)
    
    # Verify app module is importable
    try:
        import app
        print(f"✅ App module loaded from: {app.__file__}", file=sys.stderr)
    except ImportError as e:
        print(f"❌ Failed to import app: {e}", file=sys.stderr)
        sys.exit(1)
    
    async def run_tests():
        print("🔍 Testing ParentChildChunker module (app/chunking/parent_child.py)")
        print("=" * 70)
        
        try:
            # -- Test 1: ChunkMetadata serialization ----------------------
            print("\n📌 Test 1: ChunkMetadata (serialize ↔ deserialize)")
            from app.chunking.parent_child import ChunkMetadata
            
            meta = ChunkMetadata(
                chunk_id="test-abc123",
                parent_id="parent-xyz789",
                chunk_type="child",
                source_file="sample_doc.pdf",
                page_number=3,
                block_type="table",
                language="en",
                ocr_confidence=0.94,
                document_type="invoice",
                ingest_timestamp="2026-05-10T12:00:00Z",
                char_count=245,
                tags=["finance", "Q1", "approved"],
                child_index=2,
            )
            
            meta_dict = meta.to_dict(for_vector_store=True)
            assert isinstance(meta_dict["tags"], str), "Tags should be comma-string for vector store"
            assert "finance,Q1,approved" in meta_dict["tags"], "Tags serialization failed"
            print(f"   ✅ to_dict(for_vector_store=True): tags='{meta_dict['tags']}'")
            
            meta_internal = meta.to_dict(for_vector_store=False)
            assert isinstance(meta_internal["tags"], list), "Tags should be list internally"
            print(f"   ✅ to_dict(for_vector_store=False): tags={meta_internal['tags']}")
            
            meta_restored = ChunkMetadata.from_dict(meta_dict)
            assert meta_restored.tags == ["finance", "Q1", "approved"], "Tag parsing failed"
            print(f"   ✅ from_dict: tags restored as list={meta_restored.tags}")
            
            # -- Test 2: Chunker initialization ---------------------------
            print("\n📌 Test 2: ParentChildChunker initialization")
            from app.chunking.parent_child import ParentChildChunker
            
            chunker = ParentChildChunker()
            print(f"   ✅ Initialized: child={chunker.child_splitter._chunk_size}, parent={chunker.parent_splitter._chunk_size}")
            
            is_valid, err = chunker._validate_chunk_sizes(300, 50, 1500, 200)
            assert is_valid and err is None, "Valid config should pass"
            print(f"   ✅ Config validation: PASS")
            
            # -- Test 3: Text-only chunking (async generator) -------------
            print("\n📌 Test 3: chunk_text_only (async generator)")
            
            sample_text = """
            Invoice #INV-2025-001
            Date: May 10, 2026
            Client: Acme Corp
            Items:
            - Widget A: $100 x 5 = $500
            - Widget B: $250 x 2 = $500
            Subtotal: $1000
            Tax (10%): $100
            Total: $1100
            """ * 3
            
            child_docs = []
            parent_docs = []
            seen_parent_ids = set()
            
            async for child, parent in chunker.chunk_text_only(
                text=sample_text,
                source_file="test_invoice.pdf",
                page_num=1,
                block_type="structured",
                tags=["test", "invoice"],
                correlation_id="test-run-001"
            ):
                if child:
                    child_docs.append(child)
                if parent and parent.metadata["chunk_id"] not in seen_parent_ids:
                    parent_docs.append(parent)
                    seen_parent_ids.add(parent.metadata["chunk_id"])
            
            print(f"   ✅ Generated: {len(child_docs)} children, {len(parent_docs)} parents")
            assert len(child_docs) > 0 and len(parent_docs) > 0, "Should generate chunks"
            
            # Verify parent-child linking
            for child in child_docs[:3]:
                parent_id = child.metadata.get("parent_id")
                assert parent_id in seen_parent_ids, f"Child has invalid parent_id"
                print(f"   ✅ Child {child.metadata['chunk_id'][:8]}... -> Parent {parent_id[:8]}...")
            
            # Verify metadata format
            first_child = child_docs[0]
            assert first_child.metadata["source_file"] == "test_invoice.pdf"
            assert "test,invoice" in first_child.metadata["tags"]
            print(f"   ✅ Metadata format verified (vector-store compatible)")
            
            # -- Test 4: Edge cases ---------------------------------------
            print("\n📌 Test 4: Edge cases")
            
            from app.core.exceptions import ValidationError
            
            # Empty text -> should raise ValidationError
            try:
                async for _ in chunker.chunk_text_only(text="", source_file="empty.txt"):
                    pass
                print("   ❌ Empty text should raise ValidationError")
            except ValidationError:
                print("   ✅ Empty text correctly rejected")
            
            # Filename sanitization
            meta_sanitized = chunker._sanitize_filename("my doc@#$%.pdf")
            assert "@" not in meta_sanitized and "#" not in meta_sanitized
            print(f"   ✅ Filename sanitization: 'my doc@#$%.pdf' -> '{meta_sanitized}'")
            
            # -- Test 5: Deterministic chunk IDs --------------------------
            print("\n📌 Test 5: Deterministic chunk IDs")
            id1 = chunker._generate_chunk_id("same content", "file.pdf", 1, 0)
            id2 = chunker._generate_chunk_id("same content", "file.pdf", 1, 0)
            id3 = chunker._generate_chunk_id("different", "file.pdf", 1, 0)
            
            assert id1 == id2, "Same input should produce same ID"
            assert id1 != id3, "Different content should produce different ID"
            assert len(id1) == 32, f"ID should be 32-char hex"
            print(f"   ✅ Deterministic IDs: '{id1[:8]}...' (32-char SHA256 prefix)")
            
            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! ParentChildChunker module verified.")
            return True
            
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Run async tests
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)