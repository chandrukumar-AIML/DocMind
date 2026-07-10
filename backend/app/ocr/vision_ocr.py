
from __future__ import annotations
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from typing import Final, Optional, TypedDict, List, Any
import numpy as np

from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError
import httpx

# DVMELTSS-M: Import centralized utilities
from app.core.ocr_utils import (
    scrub_pii_for_ocr,
    normalize_bbox,
    generate_ocr_correlation_id,
)
from app.core.retry import retry_async, RetryConfig
from app.core.openai_errors import is_insufficient_quota_error
from app.core.exceptions import VisionOCRError
from .image_utils import image_to_b64
from .paddle_ocr import TextBlock, PageOCRResult

logger = logging.getLogger(__name__)

# Vision OCR system prompt
VISION_SYSTEM_PROMPT: Final = """You are a precise document OCR system. Extract ALL text from the provided document image.
Return ONLY valid JSON — no markdown, no explanation.
JSON schema: {"blocks": [{"text": "extracted", "block_type": "paragraph|title|table|header|footer|figure_caption", "confidence": 0.95, "reading_order": 0, "bbox": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]}]}
Rules: Preserve exact text/spelling/numbers/punctuation. For tables: extract as pipe-separated rows. Assign block_type visually. reading_order: 0-indexed top-to-bottom. confidence: 0.0–1.0. If illegible, include with confidence < 0.5. Include bbox for each block."""


class VisionBlockSchema(TypedDict, total=False):
    text: str
    block_type: str
    confidence: float
    reading_order: int
    bbox: List[List[float]]
    language: str


class VisionResponseSchema(TypedDict, total=False):
    blocks: List[VisionBlockSchema]


_VISION_COST_PER_1K_TOKENS: Final = 0.01  # GPT-4o pricing (adjust as needed)
_AVG_TOKENS_PER_PAGE: Final = 2000  # Estimate for cost tracking


@dataclass(frozen=True)
class VisionOCRMetrics:
    """Metrics for Vision OCR calls — useful for monitoring/cost tracking."""

    page_num: int
    tokens_used: Optional[int] = None
    estimated_cost: Optional[float] = None
    latency_ms: Optional[float] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_num": self.page_num,
            "tokens_used": self.tokens_used,
            "estimated_cost": round(self.estimated_cost, 4) if self.estimated_cost else None,
            "latency_ms": round(self.latency_ms, 2) if self.latency_ms else None,
            "correlation_id": self.correlation_id,
        }


class VisionOCREngine:
    """
    GPT-4o Vision fallback OCR engine for low-confidence regions.

    Features:
    - High-detail image analysis for accurate text extraction
    - Structured JSON output with block types and bounding boxes
    - PII scrubbing before API calls for GDPR/HIPAA compliance
    - Exponential backoff retry for transient errors
    - Correlation ID tracing for distributed debugging
    - Async-safe interface for FastAPI integration
    """

    _MAX_IMAGE_DIM: Final = 2048
    _MAX_IMAGE_SIZE_MB: Final = 20  # OpenAI limit
    _JPEG_QUALITY: Final = 85  # Balance quality/size

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_retries: int = 3,
        timeout_seconds: float = 120.0,
        track_costs: bool = False,  # ✅ NEW: Optional cost tracking
    ):
        if not api_key or not api_key.startswith("sk-"):
            raise ValueError("Invalid OpenAI API key format. Must start with 'sk-'")

        self.client = OpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(timeout_seconds),
            max_retries=0,  # We handle retries manually
        )
        self.model = model
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.track_costs = track_costs

        logger.info(
            f"VisionOCR initialized: model={model}, timeout={timeout_seconds}s, " f"cost_tracking={track_costs}"
        )

    async def process_page_async(
        self,
        image: np.ndarray,
        page_num: int = 0,
        context: str = "",
        correlation_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> PageOCRResult:
        """
        Async: Process a single page using GPT-4o Vision OCR with timeout protection.
        Runs blocking OpenAI call in thread pool to avoid event loop freeze.
        """
        corr_id = correlation_id or generate_ocr_correlation_id("vision_ocr")
        timeout = timeout_seconds or self.timeout_seconds

        # Validate + prepare image first (fast, no I/O)
        h, w = image.shape[:2]
        image_safe = self._validate_and_prepare_image(image, corr_id)

        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in 3.10+

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._process_page_sync(
                        image=image_safe,
                        page_num=page_num,
                        context=context,
                        correlation_id=corr_id,
                    ),
                ),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] VisionOCR timed out after {timeout}s")
            # Return minimal result instead of crashing
            return PageOCRResult(
                page_num=page_num,
                blocks=[],
                width=w,
                height=h,
                correlation_id=corr_id,
            )
        finally:
            # ✅ GPU memory cleanup hint
            if hasattr(image, "__cuda_array_interface__"):
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except ImportError:
                    pass

    def process_page(
        self,
        image: np.ndarray,
        page_num: int = 0,
        context: str = "",
        correlation_id: Optional[str] = None,
    ) -> PageOCRResult:
        """
        Sync: Process a single page using GPT-4o Vision OCR.
        Use process_page_async() in async contexts to avoid blocking.
        """
        return self._process_page_sync(image, page_num, context, correlation_id)

    def _process_page_sync(
        self,
        image: np.ndarray,
        page_num: int = 0,
        context: str = "",
        correlation_id: Optional[str] = None,
    ) -> PageOCRResult:
        """Internal sync implementation — called by both sync and async wrappers."""
        corr_id = correlation_id or generate_ocr_correlation_id("vision_ocr")
        h, w = image.shape[:2]

        # ✅ Validate + prepare image
        image_safe = self._validate_and_prepare_image(image, corr_id)
        b64_image = image_to_b64(
            image_safe,
            quality=self._JPEG_QUALITY,
            max_dimension=self._MAX_IMAGE_DIM,
            correlation_id=corr_id,
        )

        # ✅ Check base64 size against OpenAI limit
        b64_size_mb = len(b64_image) * 0.75 / (1024 * 1024)  # Approx decode size
        if b64_size_mb > self._MAX_IMAGE_SIZE_MB:
            logger.warning(
                f"[{corr_id}] Image too large after encoding: {b64_size_mb:.1f}MB > {self._MAX_IMAGE_SIZE_MB}MB — downscaling"
            )
            # Downscale and re-encode
            import cv2

            scale = (self._MAX_IMAGE_SIZE_MB / b64_size_mb) ** 0.5
            new_dim = (int(w * scale), int(h * scale))
            image_safe = cv2.resize(image_safe, new_dim, interpolation=cv2.INTER_AREA)
            b64_image = image_to_b64(image_safe, quality=self._JPEG_QUALITY, correlation_id=corr_id)

        user_content = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_image}",
                    "detail": "high",
                },
            },
            {
                "type": "text",
                "text": f"Extract all text. {'Context: ' + context if context else ''} Return structured JSON.",
            },
        ]
        # Scrub PII from text content (image PII cannot be scrubbed client-side)
        for item in user_content:
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                item["text"] = scrub_pii_for_ocr(item["text"], domain="all")

        start_time = time.perf_counter()
        blocks = self._call_with_retry_sync(user_content, page_num, corr_id)
        latency_ms = (time.perf_counter() - start_time) * 1000

        # ✅ Optional cost tracking
        metrics = None
        if self.track_costs:
            tokens_est = _AVG_TOKENS_PER_PAGE
            cost_est = tokens_est / 1000 * _VISION_COST_PER_1K_TOKENS
            metrics = VisionOCRMetrics(
                page_num=page_num,
                tokens_used=tokens_est,
                estimated_cost=cost_est,
                latency_ms=latency_ms,
                correlation_id=corr_id,
            )
            logger.debug(f"[{corr_id}] VisionOCR cost estimate: ${cost_est:.4f}")

        result = PageOCRResult(
            page_num=page_num,
            blocks=blocks,
            width=w,
            height=h,
            correlation_id=corr_id,
        )
        logger.info(
            f"[{corr_id}] VisionOCR page {page_num}: {len(blocks)} blocks, "
            f"confidence={result.mean_confidence:.3f}, latency={latency_ms:.0f}ms"
        )
        return result

    def _validate_and_prepare_image(self, image: np.ndarray, corr_id: str) -> np.ndarray:
        """Validate and prepare image for OpenAI Vision API."""
        if not isinstance(image, np.ndarray):
            raise TypeError(f"Expected numpy array, got {type(image).__name__}")
        if image.dtype != np.uint8:
            # Convert to uint8 if needed
            if image.dtype == np.float32:
                image = (image * 255).astype(np.uint8)
            else:
                raise ValueError(f"Unsupported image dtype: {image.dtype}")
        if image.ndim == 2:
            # Grayscale -> RGB
            import cv2

            image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        elif image.ndim == 3 and image.shape[2] == 4:
            # RGBA -> RGB
            import cv2

            image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
        elif image.ndim == 3 and image.shape[2] not in (1, 3):
            raise ValueError(f"Unsupported channels: {image.shape[2]}")

        # Downscale if too large
        h, w = image.shape[:2]
        if max(h, w) > self._MAX_IMAGE_DIM:
            scale = self._MAX_IMAGE_DIM / max(h, w)
            new_size = (int(w * scale), int(h * scale))
            import cv2

            image = cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)
            logger.debug(f"[{corr_id}] Image downscaled to {new_size}")

        return image

    # ====================================================================
    # -- SYNC RETRY LOGIC (for _process_page_sync) -----------------------
    # ====================================================================

    def _call_vision_api_sync(self, user_content: list[dict], corr_id: str):
        """Sync call to OpenAI Vision API — used by _call_with_retry_sync."""
        return self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
            temperature=0,
            response_format={"type": "json_object"},
            extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
        )

    def _call_with_retry_sync(
        self,
        user_content: list[dict],
        page_num: int,
        correlation_id: str,
    ) -> list[TextBlock]:
        """Sync retry logic for Vision OCR API calls."""
        delay = 1.0
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = self._call_vision_api_sync(user_content, correlation_id)
                raw = response.choices[0].message.content
                if not raw:
                    raise VisionOCRError("VisionOCR returned empty content")
                return self._parse_response(raw, page_num, correlation_id)

            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                last_error = e
                if is_insufficient_quota_error(e):
                    raise VisionOCRError("VisionOCR quota exceeded. Check billing.") from e
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"[{correlation_id}] VisionOCR transient error (attempt {attempt+1}/{self.max_retries}): {type(e).__name__}. Retry in {delay}s"
                    )
                    time.sleep(delay)  # ✅ Sync sleep for sync path
                    delay = min(delay * 2, 30.0)  # Cap at 30s
                else:
                    raise VisionOCRError(f"VisionOCR failed after {self.max_retries} retries: {e}") from e
            except Exception as e:
                last_error = e
                raise VisionOCRError(f"VisionOCR unexpected error on page {page_num}: {type(e).__name__}: {e}") from e

        # Should not reach here, but safe fallback
        logger.error(f"[{correlation_id}] VisionOCR exhausted retries — returning empty")
        return []

    # ====================================================================
    # -- ASYNC RETRY LOGIC (for async contexts) --------------------------
    # ====================================================================

    @retry_async(
        config=RetryConfig(
            max_attempts=3,
            backoff_base=1.0,
            backoff_max=30.0,
            exceptions=(RateLimitError, APITimeoutError, APIConnectionError),
        )
    )
    async def _call_vision_api(self, user_content: list[dict], corr_id: str):
        """Async call to OpenAI Vision API with retry logic."""
        if sys.version_info >= (3, 9):
            return await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[
                    {"role": "system", "content": VISION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=4096,
                temperature=0,
                response_format={"type": "json_object"},
                extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
            )
        else:
            # Python 3.8 fallback
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
            return await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": VISION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=4096,
                    temperature=0,
                    response_format={"type": "json_object"},
                    extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
                ),
            )

    async def _call_with_retry(
        self,
        user_content: list[dict],
        page_num: int,
        correlation_id: str,
    ) -> list[TextBlock]:
        """Async retry logic for Vision OCR API calls."""
        delay = 1.0
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = await self._call_vision_api(user_content, correlation_id)
                raw = response.choices[0].message.content
                if not raw:
                    raise VisionOCRError("VisionOCR returned empty content")
                return self._parse_response(raw, page_num, correlation_id)

            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                last_error = e
                if is_insufficient_quota_error(e):
                    raise VisionOCRError("VisionOCR quota exceeded. Check billing.") from e
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"[{correlation_id}] VisionOCR transient error (attempt {attempt+1}/{self.max_retries}): {type(e).__name__}. Retry in {delay}s"
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)  # Cap at 30s
                else:
                    raise VisionOCRError(f"VisionOCR failed after {self.max_retries} retries: {e}") from e
            except Exception as e:
                last_error = e
                raise VisionOCRError(f"VisionOCR unexpected error on page {page_num}: {type(e).__name__}: {e}") from e

        # Should not reach here, but safe fallback
        logger.error(f"[{correlation_id}] VisionOCR exhausted retries — returning empty")
        return []

    def _parse_response(self, raw_json: str, page_num: int, correlation_id: str) -> list[TextBlock]:
        """Parse Vision OCR JSON response into TextBlock list with schema validation."""
        try:
            data: VisionResponseSchema = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.error(f"[{correlation_id}] Failed to parse VisionOCR JSON: {e}")
            return []

        blocks = []
        for i, item in enumerate(data.get("blocks", [])):
            # ✅ Validate required fields with safe fallbacks
            text = item.get("text", "").strip()
            if not text:
                continue

            # ✅ Validate bbox with normalization + fallback
            bbox_raw = item.get("bbox")
            if bbox_raw:
                try:
                    bbox = normalize_bbox(bbox_raw)
                except Exception as e:
                    logger.warning(
                        f"[{correlation_id}] Block {i} page {page_num}: bbox normalization failed: {e} — using placeholder"
                    )
                    bbox = [[0, 0], [100, 0], [100, 20], [0, 20]]
            else:
                logger.warning(f"[{correlation_id}] Block {i} page {page_num} missing bbox — using placeholder")
                bbox = [[0, 0], [100, 0], [100, 20], [0, 20]]

            # ✅ Safe type conversions with defaults
            try:
                confidence = float(item.get("confidence", 0.9))
                confidence = max(0.0, min(1.0, confidence))  # Clamp to [0, 1]
            except (TypeError, ValueError):
                confidence = 0.9

            try:
                reading_order = int(item.get("reading_order", i))
            except (TypeError, ValueError):
                reading_order = i

            blocks.append(
                TextBlock(
                    text=text,
                    confidence=confidence,
                    bbox=bbox,
                    block_type=item.get("block_type", "paragraph"),
                    page_num=page_num,
                    language=item.get("language", "en"),
                    line_num=reading_order,
                    correlation_id=correlation_id,
                )
            )

        return sorted(blocks, key=lambda b: b.line_num)

    def get_cost_estimate(self, num_pages: int) -> dict[str, float]:
        """✅ NEW: Estimate Vision API cost for a document."""
        tokens_est = num_pages * _AVG_TOKENS_PER_PAGE
        cost_est = tokens_est / 1000 * _VISION_COST_PER_1K_TOKENS
        return {
            "pages": num_pages,
            "estimated_tokens": tokens_est,
            "estimated_cost_usd": round(cost_est, 4),
            "model": self.model,
        }


# DVMELTSS-M: Explicit module exports
__all__ = ["VisionOCREngine", "VisionOCRMetrics"]


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.ocr.vision_ocr) -------
# ========================================================================

