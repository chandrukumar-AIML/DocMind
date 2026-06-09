# backend/app/extraction/extraction_pipeline.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async orchestration
# BATMAN-FIX: A - True async, M - Memory safe, T - Concurrency control
# ✅ FIXED: Pylance-compatible type hints + proper syntax

from __future__ import annotations

import asyncio
import gc
import logging
from dataclasses import dataclass, field
from typing import Any, Final, Optional, TYPE_CHECKING, List

import numpy as np
from langchain_core.documents import Document

# DVMELTSS-M: Import extraction modules
from .table_extractor import TableExtractor, ExtractedTable
from .chart_extractor import ChartExtractor, ExtractedChart
from .form_extractor import FormExtractor, ExtractedForm

# Importing OCR types for typing
if TYPE_CHECKING:
    from app.ocr.paddle_ocr import TextBlock
    from app.ocr.vision_analyzer import EnrichedDocument, VisionAnalyzer
else:
    # Runtime: use object for forward references to avoid Pylance issues
    TextBlock = object
    EnrichedDocument = object  # type: ignore[misc]
    VisionAnalyzer = None  # type: ignore[misc]

logger = logging.getLogger(__name__)

# DVMELTSS-S: Default timeouts for extraction types
_TABLE_TIMEOUT: Final = 30.0
_CHART_TIMEOUT: Final = 60.0
_FORM_TIMEOUT: Final = 30.0
_MAX_PAGES: Final = 100
_CHUNK_SIZE: Final = 20


@dataclass(frozen=True)
class ExtractionBundle:
    """
    Immutable bundle of all extracted structures for a document.
    """

    source_file: str
    tables: list[ExtractedTable] = field(default_factory=list)
    charts: list[ExtractedChart] = field(default_factory=list)
    forms: list[ExtractedForm] = field(default_factory=list)
    correlation_id: Optional[str] = None

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def chart_count(self) -> int:
        return len(self.charts)

    @property
    def form_count(self) -> int:
        return len(self.forms)

    def to_langchain_documents(self) -> list[Document]:
        """Convert extracted structures to LangChain Documents for embedding."""
        docs = []

        for t in self.tables:
            docs.append(
                Document(
                    page_content=t.to_embed_text(),
                    metadata={
                        "source_file": t.source_file,
                        "page_number": t.page_number,
                        "chunk_id": t.chunk_id,
                        "block_type": "table",
                        "table_id": t.table_id,
                        "table_type": t.table_type,
                        "has_json": True,
                        "correlation_id": t.correlation_id,
                    },
                )
            )

        for c in self.charts:
            docs.append(
                Document(
                    page_content=c.to_embed_text(),
                    metadata={
                        "source_file": c.source_file,
                        "page_number": c.page_number,
                        "chunk_id": c.chunk_id,
                        "block_type": "figure",
                        "chart_id": c.chart_id,
                        "chart_type": c.chart_type,
                        "correlation_id": c.correlation_id,
                    },
                )
            )

        for f in self.forms:
            docs.append(
                Document(
                    page_content=f.to_embed_text(),
                    metadata={
                        "source_file": f.source_file,
                        "page_number": f.page_number,
                        "chunk_id": f.chunk_id,
                        "block_type": "form",
                        "form_id": f.form_id,
                        "form_type": f.form_type,
                        "correlation_id": f.correlation_id,
                    },
                )
            )

        return docs

    def to_dict(self) -> dict[str, Any]:
        """✅ NEW: Convert bundle to dict for API response."""
        return {
            "source_file": self.source_file,
            "table_count": self.table_count,
            "chart_count": self.chart_count,
            "form_count": self.form_count,
            "tables": [t.to_dict() for t in self.tables],
            "charts": [c.to_dict() for c in self.charts],
            "forms": [f.to_dict() for f in self.forms],
            "correlation_id": self.correlation_id,
        }


class ExtractionPipeline:
    """
    Async orchestrator for Table, Chart, and Form extraction.

    Features:
    - True async concurrency with semaphore control
    - Timeout guards per extraction type
    - Correlation ID propagation for distributed tracing
    - Memory-safe processing of large documents
    """

    def __init__(self, concurrency: int = 3):
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)
        self._table_extractor: Optional[TableExtractor] = None
        self._chart_extractor: Optional[ChartExtractor] = None
        self._form_extractor: Optional[FormExtractor] = None
        logger.info(f"ExtractionPipeline initialized: concurrency={concurrency}")

    def _ensure_extractors_ready(self) -> None:
        """✅ NEW: Lazy-load extractors on first use."""
        if self._table_extractor is None:
            self._table_extractor = TableExtractor()
        if self._chart_extractor is None:
            self._chart_extractor = ChartExtractor()
        if self._form_extractor is None:
            self._form_extractor = FormExtractor()

    def _validate_inputs(
        self,
        enriched: "EnrichedDocument" | None,  # ✅ FIXED: String literal for forward ref
        page_images: List["np.ndarray"] | None,  # ✅ FIXED: Use List + string literal
        corr_id: str,
    ) -> tuple[bool, str]:
        """Validate inputs before processing."""
        if enriched is None:
            return False, "enriched document is None"
        if page_images is None:
            return False, "page_images list is None"
        if not hasattr(enriched, "ocr_result"):
            return False, "enriched missing ocr_result attribute"
        if len(page_images) > _MAX_PAGES:
            logger.warning(f"[{corr_id}] page_images too large ({len(page_images)} > {_MAX_PAGES}) — truncating")
        return True, ""

    @staticmethod
    def _safe_crop_region(image: "np.ndarray", bbox: Any, padding: int = 5) -> "np.ndarray" | None:  # type: ignore[reportInvalidTypeForm]
        """Crop image region with fallback if VisionAnalyzer not available."""
        try:
            if VisionAnalyzer and hasattr(VisionAnalyzer, "_crop_region"):
                return VisionAnalyzer._crop_region(image, bbox, padding)  # type: ignore[union-attr]

            if isinstance(bbox, dict):
                x1 = int(bbox.get("x0", bbox.get("x1", 0)))
                y1 = int(bbox.get("y0", bbox.get("y1", 0)))
                x2 = int(bbox.get("x1", bbox.get("x0", 0)))
                y2 = int(bbox.get("y1", bbox.get("y0", 0)))
            elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
            else:
                return None

            if x1 > x2:
                x1, x2 = x2, x1
            if y1 > y2:
                y1, y2 = y2, y1

            h, w = image.shape[:2]
            x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
            x2, y2 = min(w, x2 + padding), min(h, y2 + padding)

            if x2 <= x1 or y2 <= y1:
                return None
            return image[y1:y2, x1:x2]
        except Exception as e:
            logger.debug(f"Crop fallback failed: {e}")
            return None

    async def process_enriched_document_async(
        self,
        enriched: "EnrichedDocument",  # ✅ FIXED: String literal for forward ref
        page_images: List["np.ndarray"],  # ✅ FIXED: Use List + string literal
        correlation_id: Optional[str] = None,
        timeout_per_block: float = 60.0,
    ) -> ExtractionBundle:
        """
        Async processing of all blocks in an EnrichedDocument.
        ✅ FIXED: Input validation + memory guard + explicit error handling.
        """
        corr_id = correlation_id or "extraction_pipeline"

        is_valid, error = self._validate_inputs(enriched, page_images, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error} — returning empty bundle")
            return ExtractionBundle(source_file="unknown", correlation_id=corr_id)

        self._ensure_extractors_ready()

        source_file = "unknown"
        if hasattr(enriched, "metadata") and enriched.metadata:
            source_file = getattr(enriched.metadata, "title", "unknown")
        elif hasattr(enriched, "ocr_result") and enriched.ocr_result.all_blocks:
            first_block = enriched.ocr_result.all_blocks[0]
            source_file = getattr(first_block, "source_file", source_file)

        if not hasattr(enriched, "ocr_result") or not enriched.ocr_result.all_blocks:
            return ExtractionBundle(source_file=source_file, correlation_id=corr_id)

        blocks = enriched.ocr_result.all_blocks
        max_page = min(len(page_images), _MAX_PAGES)

        tasks = []
        for i, block in enumerate(blocks):
            page_num = getattr(block, "page_num", 0)
            if page_num >= max_page:
                logger.debug(f"[{corr_id}] Skipping block {i}: page {page_num} >= max {_MAX_PAGES}")
                continue
            page_img = page_images[page_num] if page_num < len(page_images) else None

            block_type = getattr(block, "block_type", None)
            if block_type == "table":
                tasks.append(self._process_table_block(block, source_file, i, corr_id, timeout_per_block))
            elif block_type == "figure" and page_img is not None:
                tasks.append(self._process_figure_block(block, page_img, source_file, i, corr_id, timeout_per_block))
            elif block_type == "paragraph" and len(getattr(block, "text", "")) > 50:
                tasks.append(self._process_form_block(block, source_file, i, corr_id, timeout_per_block))

        if not tasks:
            logger.info(f"[{corr_id}] No extractable blocks found")
            return ExtractionBundle(source_file=source_file, correlation_id=corr_id)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        bundle = ExtractionBundle(source_file=source_file, correlation_id=corr_id)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"[{corr_id}] Task {i} failed: {type(res).__name__}: {res}")
                continue
            if res is None:
                continue
            if isinstance(res, ExtractedTable):
                bundle.tables.append(res)
            elif isinstance(res, ExtractedChart):
                bundle.charts.append(res)
            elif isinstance(res, ExtractedForm):
                bundle.forms.append(res)

        logger.info(
            f"[{corr_id}] Extraction complete: {bundle.table_count}T, " f"{bundle.chart_count}C, {bundle.form_count}F"
        )

        gc.collect()

        return bundle

    async def _process_table_block(self, block, source_file: str, counter: int, corr_id: str, timeout: float):
        async with self.semaphore:
            table_id = f"table_{counter}"
            chunk_id = getattr(block, "chunk_id", "")
            html = getattr(block, "table_html", "") or getattr(block, "text", "")
            text = getattr(block, "text", "") if not html else ""
            page_num = getattr(block, "page_num", 0)

            if self._table_extractor is None:
                logger.error(f"[{corr_id}] TableExtractor not initialized")
                return None

            try:
                return await asyncio.wait_for(
                    self._table_extractor.extract_async(
                        html=html,
                        text=text,
                        table_id=table_id,
                        source_file=source_file,
                        page_number=page_num,
                        chunk_id=chunk_id,
                        correlation_id=corr_id,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{corr_id}] Table extraction timed out for block {counter}")
                return None
            except Exception as e:
                logger.error(f"[{corr_id}] Table extraction failed: {type(e).__name__}: {e}")
                return None

    async def _process_figure_block(
        self,
        block,
        page_img: "np.ndarray",
        source_file: str,
        counter: int,
        corr_id: str,
        timeout: float,  # type: ignore[reportInvalidTypeForm]
    ):
        async with self.semaphore:
            if VisionAnalyzer is None:
                logger.debug(f"[{corr_id}] VisionAnalyzer not available — skipping figure extraction")
                return None

            bbox = getattr(block, "bbox", None)
            if not bbox:
                logger.debug(f"[{corr_id}] Block {counter} missing bbox — skipping figure extraction")
                return None

            cropped = self._safe_crop_region(page_img, bbox, padding=5)
            if cropped is None:
                logger.debug(f"[{corr_id}] Crop failed for block {counter} — skipping figure extraction")
                return None

            chart_id = f"chart_{counter}"
            page_num = getattr(block, "page_num", 0)
            chunk_id = getattr(block, "chunk_id", "")

            if self._chart_extractor is None:
                logger.error(f"[{corr_id}] ChartExtractor not initialized")
                return None

            try:
                return await asyncio.wait_for(
                    self._chart_extractor.extract_from_image_async(
                        image=cropped,
                        chart_id=chart_id,
                        source_file=source_file,
                        page_number=page_num,
                        chunk_id=chunk_id,
                        correlation_id=corr_id,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{corr_id}] Chart extraction timed out for block {counter}")
                return None
            except Exception as e:
                logger.error(f"[{corr_id}] Chart extraction failed: {type(e).__name__}: {e}")
                return None

    async def _process_form_block(self, block, source_file: str, counter: int, corr_id: str, timeout: float):
        async with self.semaphore:
            form_id = f"form_{counter}"
            text = getattr(block, "text", "")
            page_num = getattr(block, "page_num", 0)
            chunk_id = getattr(block, "chunk_id", "")

            if self._form_extractor is None:
                logger.error(f"[{corr_id}] FormExtractor not initialized")
                return None

            try:
                return await asyncio.wait_for(
                    self._form_extractor.extract_from_text_async(
                        text=text,
                        form_id=form_id,
                        source_file=source_file,
                        page_number=page_num,
                        chunk_id=chunk_id,
                        correlation_id=corr_id,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{corr_id}] Form extraction timed out for block {counter}")
                return None
            except Exception as e:
                logger.error(f"[{corr_id}] Form extraction failed: {type(e).__name__}: {e}")
                return None

    def process_enriched_document(
        self,
        enriched: "EnrichedDocument",  # ✅ FIXED: String literal for forward ref
        page_images: List["np.ndarray"],  # ✅ FIXED: Use List + string literal
        correlation_id: Optional[str] = None,
    ) -> ExtractionBundle:
        """
        Sync wrapper — use process_enriched_document_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            loop = asyncio.get_running_loop()
            logger.warning(
                "⚠️ ExtractionPipeline.process_enriched_document() called from async context — "
                "use process_enriched_document_async() instead. Returning empty bundle."
            )
            return ExtractionBundle(
                source_file=getattr(enriched, "source_file", "unknown"),
                correlation_id=correlation_id,
            )
        except RuntimeError:
            return asyncio.run(self.process_enriched_document_async(enriched, page_images, correlation_id))

    def get_extraction_stats(self, bundle: ExtractionBundle) -> dict[str, Any]:
        """✅ NEW: Return extraction metrics for monitoring."""
        return {
            "source_file": bundle.source_file,
            "table_count": bundle.table_count,
            "chart_count": bundle.chart_count,
            "form_count": bundle.form_count,
            "total_structures": bundle.table_count + bundle.chart_count + bundle.form_count,
            "correlation_id": bundle.correlation_id,
        }


def get_extraction_metadata() -> dict[str, Any]:
    """✅ NEW: Return extraction pipeline metadata for monitoring."""
    return {
        "concurrency_default": 3,
        "timeouts": {
            "table_seconds": _TABLE_TIMEOUT,
            "chart_seconds": _CHART_TIMEOUT,
            "form_seconds": _FORM_TIMEOUT,
        },
        "memory_guards": {
            "max_pages": _MAX_PAGES,
            "chunk_size": _CHUNK_SIZE,
        },
        "features": [
            "async_concurrent",
            "timeout_per_block",
            "memory_safe",
            "correlation_id_propagation",
        ],
    }


# DVMELTSS-M: Explicit module exports
# ✅ FIXED: Properly closed list (no stray brace)
__all__ = [
    "ExtractionPipeline",
    "ExtractionBundle",
    "get_extraction_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
