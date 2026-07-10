
from __future__ import annotations
import asyncio
import gc
import logging
from functools import lru_cache
from pathlib import Path
from typing import Final, Iterator, Optional, Union, TYPE_CHECKING, Callable, Awaitable

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:
    cv2 = None
    logging.warning("⚠️ opencv-python not installed — image conversion will fail")

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None
    logging.warning("⚠️ pypdfium2 not installed — PDF page counting will fail")

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None
    logging.warning("⚠️ pdf2image not installed — PDF to image conversion will fail")

# DVMELTSS-M: Import centralized utilities
from app.core.ocr_utils import generate_ocr_correlation_id
from app.config import get_settings
from app.core.dead_letter import log_failed_page
from app.core.exceptions import VisionOCRError

from .preprocessor import DocumentPreprocessor
from .paddle_ocr import PaddleOCREngine, DocumentOCRResult, PageOCRResult
from .vision_ocr import VisionOCREngine

if TYPE_CHECKING:
    from .vision_analyzer import VisionAnalyzer, EnrichedDocument

logger = logging.getLogger(__name__)

# Pipeline defaults
_SUPPORTED_EXTENSIONS: Final = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"})
_DEFAULT_CONFIDENCE_THRESHOLD: Final[float] = 0.7
_MAX_PAGES: Final[int] = 500  # ✅ NEW: Prevent DoS via huge PDFs
_PAGE_LOAD_TIMEOUT: Final[float] = 30.0  # ✅ NEW: Per-page load timeout
_OCR_TIMEOUT: Final[float] = 120.0  # ✅ NEW: Per-page OCR timeout


class OCRPipeline:
    """Main OCR orchestrator for DocuMind AI.

    Features:
    - Multi-format support: PDF, PNG, JPG, TIFF, BMP
    - PaddleOCR primary engine with layout analysis
    - GPT-4o Vision fallback for low-confidence regions
    - Semantic enrichment for tables, diagrams, and metadata
    - Memory-safe processing with explicit array cleanup
    - Correlation ID propagation for end-to-end tracing
    - Async-safe wrappers for FastAPI integration
    """

    SUPPORTED_EXTENSIONS = _SUPPORTED_EXTENSIONS

    def __init__(
        self,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
        use_gpu: bool = False,
        ocr_languages: Optional[tuple[str, ...]] = None,  # ✅ FIXED: Immutable tuple for cache safety
    ):
        settings = get_settings()
        self.confidence_threshold = confidence_threshold or settings.ocr_confidence_threshold
        self.use_gpu = use_gpu or settings.ocr_use_gpu
        self.ocr_languages = tuple(ocr_languages) if ocr_languages else tuple(settings.ocr_language_list)

        self.preprocessor = DocumentPreprocessor()
        self.paddle_engine = PaddleOCREngine(
            languages=list(self.ocr_languages),  # Convert back to list for engine
            use_gpu=self.use_gpu,
            enable_layout=True,
        )

        # Vision engine is optional - only initialize if API key is available
        self.vision_engine: Optional[VisionOCREngine] = None
        if settings.openai_api_key:
            try:
                self.vision_engine = VisionOCREngine(
                    api_key=settings.openai_api_key,
                    model=settings.openai_chat_model,
                )
            except ValueError as e:
                logger.warning(f"Vision OCR disabled: {e}")

        self._vision_analyzer_cache: Optional["VisionAnalyzer"] = None

        logger.info(
            f"OCRPipeline ready: threshold={self.confidence_threshold}, "
            f"gpu={self.use_gpu}, vision={'enabled' if self.vision_engine else 'disabled'}, "
            f"langs={self.ocr_languages}"
        )

    def warmup(self) -> bool:
        """Trigger lazy PaddleOCR model loading for health checks."""
        try:
            # Create minimal dummy image to trigger model load
            dummy_img = np.zeros((32, 32, 3), dtype=np.uint8)

            if hasattr(self, "paddle_engine") and self.paddle_engine is not None:
                _ = self.paddle_engine.ocr(dummy_img, cls=False)
                logger.debug("✅ OCR models loaded successfully via warmup")

                if self.use_gpu:
                    try:
                        import torch

                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                            logger.debug("✅ GPU cache cleared after warmup")
                    except ImportError:
                        pass
                return True

            logger.warning("⚠️ OCR engine attribute not found for warmup")
            return False

        except Exception as e:
            logger.debug(f"⚠️ OCR warmup failed (non-critical, expected on first run): {e}")
            return False

    async def process_file_async(
        self,
        file_path: Union[str, Path],
        progress_callback: Optional[Callable[[int, int], Union[None, Awaitable[None]]]] = None,
        enable_ocr_fallback: bool = True,
        correlation_id: Optional[str] = None,
        timeout_seconds: float = _OCR_TIMEOUT,
    ) -> DocumentOCRResult:
        """
        Async: Process a document file with timeout protection.
        Runs blocking OCR work in thread pool to avoid event loop freeze.
        """
        corr_id = correlation_id or generate_ocr_correlation_id("ocr_pipeline")

        # Validate inputs first (fast, no I/O)
        file_path = Path(file_path)
        self._validate_file_input(file_path)

        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in 3.10+

        # Run blocking process_file in thread pool with timeout
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.process_file(
                        file_path=file_path,
                        progress_callback=progress_callback,
                        enable_ocr_fallback=enable_ocr_fallback,
                        correlation_id=corr_id,
                    ),
                ),
                timeout=timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] OCR processing timed out after {timeout_seconds}s")
            raise VisionOCRError(f"OCR timeout after {timeout_seconds}s")
        except Exception as e:
            logger.error(f"[{corr_id}] OCR processing failed: {type(e).__name__}: {e}")
            raise

    def process_file(
        self,
        file_path: Union[str, Path],
        progress_callback: Optional[Callable[[int, int], Union[None, Awaitable[None]]]] = None,
        enable_ocr_fallback: bool = True,
        correlation_id: Optional[str] = None,
    ) -> DocumentOCRResult:
        """Process a document file and return OCR results."""
        corr_id = correlation_id or generate_ocr_correlation_id("ocr_pipeline")
        file_path = Path(file_path)

        self._validate_file_input(file_path)

        logger.info(f"[{corr_id}] Processing file: {file_path.name}")
        total_pages = self._count_pages(file_path)

        if total_pages > _MAX_PAGES:
            raise ValueError(f"Document too large: {total_pages} pages (max {_MAX_PAGES})")

        processed_pages: list[PageOCRResult] = []
        vision_fallback_count = 0

        for page_num, page_image in enumerate(self._load_pages(file_path)):
            try:
                if progress_callback:
                    cb = progress_callback(page_num, total_pages)
                    if asyncio.iscoroutine(cb):
                        asyncio.run(cb)  # Safe because we're in sync context; for pure async, use process_file_async

                # Preprocess
                prep_result = self.preprocessor.preprocess(page_image, correlation_id=corr_id)
                preprocessed_img = prep_result.image
                del page_image  # Release original image memory

                # Primary OCR with Paddle
                paddle_result = self.paddle_engine.process_page(
                    preprocessed_img, page_num=page_num, correlation_id=corr_id
                )

                # Fallback to Vision if confidence low
                if (
                    enable_ocr_fallback
                    and self.vision_engine
                    and paddle_result.mean_confidence < self.confidence_threshold
                ):
                    logger.warning(
                        f"[{corr_id}] Page {page_num}: confidence {paddle_result.mean_confidence:.3f} < {self.confidence_threshold}. Fallback to GPT-4o."
                    )
                    try:
                        vision_result = self.vision_engine.process_page(
                            preprocessed_img, page_num=page_num, correlation_id=corr_id
                        )
                        final_result = self._merge_results_blockwise(paddle_result, vision_result)
                        vision_fallback_count += 1
                    except VisionOCRError as e:
                        logger.error(f"[{corr_id}] Vision fallback failed: {e}. Using PaddleOCR result.")
                        log_failed_page(file_path, page_num, str(e))
                        final_result = paddle_result
                else:
                    final_result = paddle_result

                processed_pages.append(final_result)
                del preprocessed_img  # Release preprocessed image memory

            except Exception as e:
                logger.error(f"[{corr_id}] Page {page_num} processing failed: {e}")
                log_failed_page(file_path, page_num, str(e))
                # Continue with other pages instead of failing entire doc
                continue

        doc_result = DocumentOCRResult(pages=processed_pages, correlation_id=corr_id)
        logger.info(
            f"[{corr_id}] Completed: {file_path.name} | {total_pages} pages | "
            f"vision_fallbacks={vision_fallback_count} | mean_confidence={doc_result.mean_confidence:.3f}"
        )
        return doc_result

    def _validate_file_input(self, file_path: Path) -> None:
        """Validate file input before processing."""
        if not file_path.exists():
            raise FileNotFoundError(f"Document not found: {file_path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")
        file_size = file_path.stat().st_size
        if file_size == 0:
            raise ValueError(f"File is empty: {file_path}")
        settings = get_settings()
        if file_size > settings.max_upload_size_bytes:
            raise ValueError(f"File too large: {file_size/1024/1024:.1f}MB (max {settings.max_upload_size_mb}MB)")
        suffix = file_path.suffix.lower()
        if suffix not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {suffix}. Supported: {self.SUPPORTED_EXTENSIONS}")

    async def process_file_enriched_async(
        self,
        file_path: Union[str, Path],
        progress_callback: Optional[Callable[[int, int], Union[None, Awaitable[None]]]] = None,
        enable_vision_enrichment: bool = True,
        enable_ocr_fallback: bool = True,
        correlation_id: Optional[str] = None,
        timeout_seconds: float = _OCR_TIMEOUT * 2,  # Enrichment takes longer
    ) -> "EnrichedDocument":
        """Async wrapper for enriched processing."""
        from .vision_analyzer import EnrichedDocument

        corr_id = correlation_id or generate_ocr_correlation_id("ocr_enriched")

        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in 3.10+
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.process_file_enriched(
                        file_path=file_path,
                        progress_callback=progress_callback,
                        enable_vision_enrichment=enable_vision_enrichment,
                        enable_ocr_fallback=enable_ocr_fallback,
                        correlation_id=corr_id,
                    ),
                ),
                timeout=timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Enriched OCR timed out after {timeout_seconds}s")
            # Return basic OCR result as fallback
            basic = await self.process_file_async(
                file_path,
                progress_callback,
                enable_ocr_fallback,
                corr_id,
                timeout_seconds / 2,
            )
            return EnrichedDocument(ocr_result=basic, correlation_id=corr_id)

    def process_file_enriched(
        self,
        file_path: Union[str, Path],
        progress_callback: Optional[Callable[[int, int], Union[None, Awaitable[None]]]] = None,
        enable_vision_enrichment: bool = True,
        enable_ocr_fallback: bool = True,
        correlation_id: Optional[str] = None,
    ) -> "EnrichedDocument":
        """Process file with optional Vision-based semantic enrichment."""
        from .vision_analyzer import VisionAnalyzer, EnrichedDocument

        corr_id = correlation_id or generate_ocr_correlation_id("ocr_enriched")
        settings = get_settings()
        file_path = Path(file_path)

        self._validate_file_input(file_path)

        preprocessed_pages: list[np.ndarray] = []
        processed_pages: list[PageOCRResult] = []
        total_pages = self._count_pages(file_path)

        if total_pages > _MAX_PAGES:
            raise ValueError(f"Document too large: {total_pages} pages (max {_MAX_PAGES})")

        vision_fallback_count = 0

        for page_num, page_image in enumerate(self._load_pages(file_path)):
            try:
                if progress_callback:
                    cb = progress_callback(page_num, total_pages)
                    if asyncio.iscoroutine(cb):
                        asyncio.run(cb)

                prep_result = self.preprocessor.preprocess(page_image, correlation_id=corr_id)
                preprocessed_img = prep_result.image
                preprocessed_pages.append(preprocessed_img)
                del page_image

                paddle_result = self.paddle_engine.process_page(
                    preprocessed_img, page_num=page_num, correlation_id=corr_id
                )

                if (
                    enable_ocr_fallback
                    and self.vision_engine
                    and paddle_result.mean_confidence < self.confidence_threshold
                ):
                    try:
                        vision_result = self.vision_engine.process_page(
                            preprocessed_img, page_num=page_num, correlation_id=corr_id
                        )
                        final_result = self._merge_results_blockwise(paddle_result, vision_result)
                        vision_fallback_count += 1
                    except VisionOCRError as e:
                        logger.error(f"[{corr_id}] Vision fallback failed page {page_num}: {e}")
                        log_failed_page(file_path, page_num, str(e))
                        final_result = paddle_result
                else:
                    final_result = paddle_result
                processed_pages.append(final_result)
            except Exception as e:
                logger.error(f"[{corr_id}] Page {page_num} enrichment prep failed: {e}")
                continue

        doc_result = DocumentOCRResult(pages=processed_pages, correlation_id=corr_id)

        if not enable_vision_enrichment or not self.vision_engine:
            logger.info(f"[{corr_id}] Vision enrichment disabled or unavailable.")
            return EnrichedDocument(ocr_result=doc_result, correlation_id=corr_id)

        if self._vision_analyzer_cache is None:
            self._vision_analyzer_cache = VisionAnalyzer(
                api_key=settings.openai_api_key,
                model=settings.openai_chat_model,
            )

        logger.info(f"[{corr_id}] Running GPT-4o Vision semantic enrichment...")
        enriched = self._vision_analyzer_cache.enrich_document(
            ocr_result=doc_result,
            page_images=preprocessed_pages,
            correlation_id=corr_id,
        )

        # Memory cleanup
        del preprocessed_pages
        gc.collect()
        if self.use_gpu:
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

        return enriched

    @staticmethod
    def _count_pages(file_path: Path) -> int:
        """Count pages in a PDF or return 1 for image files."""
        if file_path.suffix.lower() == ".pdf":
            if pdfium is None:
                logger.warning("⚠️ pypdfium2 not installed — assuming 1 page for PDF")
                return 1
            try:
                pdf = pdfium.PdfDocument(str(file_path))
                count = len(pdf)
                pdf.close()
                return min(count, _MAX_PAGES)  # ✅ Guard against huge PDFs
            except Exception as e:
                logger.warning(f"⚠️ Failed to count PDF pages: {e} — assuming 1 page")
                return 1
        return 1

    def _load_pages(self, file_path: Path) -> Iterator[np.ndarray]:
        """Yield pages as OpenCV BGR numpy arrays with timeout protection."""
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            # Prefer pypdfium2 (no poppler needed), fallback to pdf2image
            if pdfium is not None:
                try:
                    doc = pdfium.PdfDocument(str(file_path))
                    n_pages = min(len(doc), _MAX_PAGES)
                    for i in range(n_pages):
                        page = doc[i]
                        bitmap = page.render(scale=300 / 72)
                        pil_img = bitmap.to_pil()
                        yield self._pil_to_cv2(pil_img)
                        pil_img.close()
                    doc.close()
                except Exception as e:
                    logger.error(f"pypdfium2 PDF conversion failed: {e}")
                    raise
            elif convert_from_path is not None:
                try:
                    pil_images = convert_from_path(str(file_path), dpi=300, fmt="jpeg")
                    for pil_img in pil_images:
                        yield self._pil_to_cv2(pil_img)
                except Exception as e:
                    logger.error(f"pdf2image conversion failed: {e}")
                    raise
            else:
                raise ImportError("No PDF renderer available — install pypdfium2 or poppler")
        else:
            try:
                pil_img = Image.open(file_path)
                if hasattr(pil_img, "n_frames") and pil_img.n_frames > 1:
                    for frame in range(min(pil_img.n_frames, _MAX_PAGES)):  # ✅ Guard multi-frame images
                        pil_img.seek(frame)
                        yield self._pil_to_cv2(pil_img.copy())
                else:
                    yield self._pil_to_cv2(pil_img)
            finally:
                pil_img.close()  # ✅ Ensure file handle is released

    @staticmethod
    def _pil_to_cv2(pil_img: Image.Image) -> np.ndarray:
        """Convert PIL RGB image to OpenCV BGR numpy array."""
        if cv2 is None:
            raise ImportError("opencv-python not installed — cannot convert PIL to CV2")
        img_array = np.array(pil_img.convert("RGB"))
        return cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

    @staticmethod
    def _select_primary(paddle: PageOCRResult, vision: PageOCRResult) -> tuple[PageOCRResult, PageOCRResult]:
        """Select primary result based on block count and confidence."""
        if len(vision.blocks) >= len(paddle.blocks) * 0.8:
            return vision, paddle
        return paddle, vision

    @staticmethod
    def _inject_table_html(primary: PageOCRResult, secondary: PageOCRResult) -> PageOCRResult:
        """Inject table HTML from secondary result into primary if missing."""
        secondary_tables = {i: b for i, b in enumerate(secondary.blocks) if b.block_type == "table" and b.table_html}
        for block in primary.blocks:
            if block.block_type == "table" and not block.table_html and secondary_tables:
                block.table_html = next(iter(secondary_tables.values())).table_html
        primary.blocks.sort(key=lambda b: b.line_num)
        return primary

    @staticmethod
    def _merge_results_blockwise(paddle: PageOCRResult, vision: PageOCRResult) -> PageOCRResult:
        """
        Merge Paddle and Vision results with confidence-weighted block fusion.

        Strategy:
        1. Match blocks by bounding box overlap (IoU > 0.5)
        2. For matched blocks: keep higher-confidence text, merge metadata
        3. For unmatched blocks: include both (dedupe by text similarity)
        4. Sort by line_num for consistent ordering
        """
        from difflib import SequenceMatcher

        def _iou(box1: dict, box2: dict) -> float:
            """Calculate Intersection over Union for two bounding boxes."""
            x1 = max(box1.get("x0", 0), box2.get("x0", 0))
            y1 = max(box1.get("y0", 0), box2.get("y0", 0))
            x2 = min(box1.get("x1", 0), box2.get("x1", 0))
            y2 = min(box1.get("y1", 0), box2.get("y1", 0))

            if x2 <= x1 or y2 <= y1:
                return 0.0

            intersection = (x2 - x1) * (y2 - y1)
            area1 = (box1.get("x1", 0) - box1.get("x0", 0)) * (box1.get("y1", 0) - box1.get("y0", 0))
            area2 = (box2.get("x1", 0) - box2.get("x0", 0)) * (box2.get("y1", 0) - box2.get("y0", 0))
            union = area1 + area2 - intersection

            return intersection / union if union > 0 else 0.0

        def _text_similarity(a: str, b: str) -> float:
            """Return text similarity ratio [0, 1]."""
            return SequenceMatcher(None, a.lower(), b.lower()).ratio()

        # Index vision blocks by position for fast lookup
        vision_blocks_by_pos = []
        for vb in vision.blocks:
            bbox = getattr(vb, "bbox", {}) or {}
            vision_blocks_by_pos.append((vb, bbox))

        merged_blocks = []
        used_vision_indices = set()

        # Process Paddle blocks first (primary)
        for pb in paddle.blocks:
            pb_bbox = getattr(pb, "bbox", {}) or {}
            best_match = None
            best_iou = 0.0

            # Find best matching vision block by IoU
            for idx, (vb, vb_bbox) in enumerate(vision_blocks_by_pos):
                if idx in used_vision_indices:
                    continue
                iou = _iou(pb_bbox, vb_bbox)
                if iou > best_iou and iou > 0.5:  # Threshold for "same block"
                    best_iou = iou
                    best_match = (idx, vb)

            if best_match:
                # Merge matched blocks: prefer higher confidence text
                idx, vb = best_match
                used_vision_indices.add(idx)

                # Choose text with higher confidence
                if pb.confidence >= vb.confidence:
                    merged_text = pb.text
                    merged_conf = pb.confidence
                else:
                    merged_text = vb.text
                    merged_conf = vb.confidence

                # Merge metadata: prefer non-null values from either
                merged_meta = {
                    **getattr(pb, "metadata", {}),
                    **{k: v for k, v in getattr(vb, "metadata", {}).items() if v is not None},
                }

                try:
                    merged_block = type(pb)(
                        text=merged_text,
                        confidence=merged_conf,
                        bbox=pb_bbox if pb_bbox else vb_bbox,
                        block_type=pb.block_type if pb.block_type != "unknown" else vb.block_type,
                        line_num=pb.line_num,
                        table_html=pb.table_html or vb.table_html,
                        metadata=merged_meta,
                    )
                except TypeError:
                    # Fallback: create with basic params, then set metadata as attribute
                    merged_block = type(pb)(
                        text=merged_text,
                        confidence=merged_conf,
                        bbox=pb_bbox if pb_bbox else vb_bbox,
                        block_type=pb.block_type if pb.block_type != "unknown" else vb.block_type,
                        line_num=pb.line_num,
                        table_html=pb.table_html or vb.table_html,
                    )
                    if merged_meta:
                        object.__setattr__(merged_block, "metadata", merged_meta)

                merged_blocks.append(merged_block)
            else:
                # No match — keep Paddle block as-is
                merged_blocks.append(pb)

        # Add unmatched vision blocks (dedupe by text similarity)
        for idx, (vb, vb_bbox) in enumerate(vision_blocks_by_pos):
            if idx in used_vision_indices:
                continue
            # Check if text is too similar to any existing merged block
            is_duplicate = any(_text_similarity(vb.text, mb.text) > 0.95 for mb in merged_blocks)
            if not is_duplicate:
                merged_blocks.append(vb)

        # Sort by line_num for consistent ordering
        merged_blocks.sort(key=lambda b: getattr(b, "line_num", 0))

        # Compute aggregate confidence
        if merged_blocks:
            mean_conf = sum(b.confidence for b in merged_blocks) / len(merged_blocks)
        else:
            mean_conf = 0.0

        # Return new PageOCRResult with merged data
        return PageOCRResult(
            page_num=paddle.page_num,
            blocks=merged_blocks,
            mean_confidence=mean_conf,
            correlation_id=paddle.correlation_id or vision.correlation_id,
        )

    @staticmethod
    def _merge_results(paddle: PageOCRResult, vision: PageOCRResult) -> PageOCRResult:
        """Legacy merge — kept for backward compatibility. Use _merge_results_blockwise instead."""
        primary, secondary = OCRPipeline._select_primary(paddle, vision)
        return OCRPipeline._inject_table_html(primary, secondary)


@lru_cache(maxsize=1)
def get_ocr_pipeline(
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    use_gpu: bool = False,
    ocr_languages: Optional[Union[list[str], tuple[str, ...]]] = None,
) -> OCRPipeline:
    """
    Singleton OCR pipeline — PaddleOCR models load once.

    ✅ FIXED: Params converted to hashable types for safe caching.
    """
    # Convert mutable list to immutable tuple for cache key safety
    langs_tuple = tuple(ocr_languages) if isinstance(ocr_languages, list) else ocr_languages

    return OCRPipeline(
        confidence_threshold=confidence_threshold,
        use_gpu=use_gpu,
        ocr_languages=langs_tuple,
    )


def reset_ocr_pipeline_cache() -> None:
    """Clear the get_ocr_pipeline LRU cache — useful for tests or config reloads."""
    get_ocr_pipeline.cache_clear()


# DVMELTSS-M: Explicit module exports
__all__ = ["OCRPipeline", "get_ocr_pipeline", "reset_ocr_pipeline_cache"]


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.ocr.pipeline) ---------
# ========================================================================

