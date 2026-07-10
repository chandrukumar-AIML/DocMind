
from __future__ import annotations

import asyncio
import base64
import gc
import io
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Final, Optional, Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, ValidationError, Field

# DVMELTSS-M: Import centralized utilities
from app.core.vision_llm import get_vision_llm
from app.core.retry import retry_async, RetryConfig
from app.core.prompts import escape_prompt_content
from app.core.openai_errors import classify_openai_error

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-A) -------------------------
# ========================================================================

_VALID_CHART_TYPES: Final = frozenset(
    {
        "bar_chart",
        "line_chart",
        "pie_chart",
        "scatter_plot",
        "flowchart",
        "org_chart",
        "table",
        "diagram",
        "image",
        "other",
    }
)

# BATMAN-M: Memory safety limits
_MAX_IMAGE_DIMENSION: Final = 2048
_MAX_IMAGE_QUALITY: Final = 92
_MAX_IMAGE_MEMORY_MB: Final = 50  # Memory limit for image encoding

# DVMELTSS-E: Retry configuration
_MAX_RETRIES: Final = 3
_RETRY_BASE_DELAY: Final = 1.0
_RETRY_MAX_DELAY: Final = 30.0


# DVMELTSS-V: Pydantic schemas for structured output
class AxisSchema(BaseModel):
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    x_values: list[str] = Field(default_factory=list)
    y_range: Optional[str] = None


class DataPointSchema(BaseModel):
    label: str
    value: str
    note: Optional[str] = None


class ChartExtractionSchema(BaseModel):
    chart_type: str = Field(..., pattern=f"^({'|'.join(_VALID_CHART_TYPES)})$")
    title: Optional[str] = None
    description: str
    axes: Optional[AxisSchema] = None
    data_points: list[DataPointSchema] = Field(default_factory=list, max_length=50)
    key_takeaway: str = ""
    all_text: str = ""

    model_config = {"extra": "forbid"}  # ✅ FIXED: Pydantic v2 config


# ========================================================================
# -- IMMUTABLE DATA MODEL (DVMELTSS-M, V) -------------------------------
# ========================================================================


@dataclass
class ExtractedChart:
    """
    Structured representation of a chart or figure.
    ✅ FIXED: Proper field defaults + validation in __post_init__.
    """

    chart_id: str
    source_file: str
    page_number: int
    chunk_id: str

    # GPT-4o extracted content
    chart_type: str
    title: Optional[str]
    description: str
    data_points: list[dict] = field(default_factory=list)
    axes: dict = field(default_factory=dict)
    key_takeaway: str = ""
    all_text: str = ""
    correlation_id: Optional[str] = None

    def __post_init__(self):
        # ✅ Validate chart_type against allowed values
        if self.chart_type not in _VALID_CHART_TYPES:
            object.__setattr__(self, "chart_type", "other")
        # ✅ Clamp data_points to max 50 for embedding safety
        if len(self.data_points) > 50:
            object.__setattr__(self, "data_points", self.data_points[:50])

    def to_embed_text(self) -> str:
        """Rich text for embedding — combines all extracted information."""
        parts = []
        if self.title:
            parts.append(f"Chart title: {self.title}")
        parts.append(f"Chart type: {self.chart_type.replace('_', ' ')}")
        parts.append(f"Description: {self.description}")
        if self.key_takeaway:
            parts.append(f"Key insight: {self.key_takeaway}")
        if self.data_points:
            dp_text = "; ".join(f"{dp.get('label', 'item')}: {dp.get('value', '')}" for dp in self.data_points[:10])
            parts.append(f"Data points: {dp_text}")
        axes = self.axes or {}
        if axes.get("x_label") or axes.get("y_label"):
            parts.append(f"Axes: x={axes.get('x_label', '')} y={axes.get('y_label', '')}")
        if self.all_text:
            parts.append(f"All visible text: {self.all_text}")
        return "\n".join(parts)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "chart_id": self.chart_id,
            "block_type": "figure",
            "chart_type": self.chart_type,
            "has_data_points": len(self.data_points) > 0,
            "data_point_count": len(self.data_points),
            "correlation_id": self.correlation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """✅ NEW: Convert to dict for API serialization."""
        return {
            "chart_id": self.chart_id,
            "source_file": self.source_file,
            "page_number": self.page_number,
            "chunk_id": self.chunk_id,
            "chart_type": self.chart_type,
            "title": self.title,
            "description": self.description,
            "data_points": self.data_points,
            "axes": self.axes,
            "key_takeaway": self.key_takeaway,
            "all_text": self.all_text,
            "correlation_id": self.correlation_id,
        }


# ========================================================================
# -- PROMPT TEMPLATE (OWASP-1: Structured, safe) -----------------------
# ========================================================================

CHART_EXTRACTION_PROMPT = """You are a data visualization expert analyzing a chart or figure.
Extract all information and return ONLY valid JSON matching this schema:

{{
  "chart_type": "bar_chart|line_chart|pie_chart|scatter_plot|flowchart|org_chart|table|diagram|image|other",
  "title": "chart title if visible, else null",
  "description": "comprehensive natural language description of what this chart shows",
  "axes": {{"x_label": "...", "y_label": "...", "x_values": [...], "y_range": "..."}},
  "data_points": [{{"label": "Category A", "value": "42%", "note": "highest"}}],
  "key_takeaway": "one sentence summary of the most important insight",
  "all_text": "every piece of text visible in the image concatenated"
}}

Rules:
- Extract ALL visible numbers and labels
- data_points: list every data series or category with its value
- all_text: critical for search — include every visible word
- If chart is unclear, describe what you can see
"""


# ========================================================================
# -- EXTRACTOR CLASS (DVMELTSS-V, BATMAN-A, OWASP-1) -------------------
# ========================================================================


class ChartExtractor:
    """
    Extracts structured data from charts and figures using GPT-4o Vision.

    Features:
    - Centralized vision LLM client via app.core.vision_llm
    - Safe image encoding with memory limits
    - Pydantic structured output validation
    - Centralized retry decorator for rate limits
    - Correlation ID tracing for audit trails
    - Async-safe interface for FastAPI integration
    """

    _VALID_DTYPES: Final = {np.uint8, np.float32}
    _VALID_CHANNELS: Final = {1, 3, 4}

    def __init__(self, model: str = "gpt-4o", max_retries: int = _MAX_RETRIES):
        self.client = get_vision_llm(model_override=model, timeout=30.0)
        self.model = model
        self.max_retries = max_retries

        logger.info(f"ChartExtractor initialized: model={model}, async=True")

    def _validate_chart_image(self, image: np.ndarray, corr_id: str) -> np.ndarray:
        """Validate and normalize image for chart extraction."""
        if not isinstance(image, np.ndarray):
            raise TypeError(f"Expected numpy array, got {type(image).__name__}")
        if image.dtype not in self._VALID_DTYPES:
            if np.issubdtype(image.dtype, np.floating):
                image = (np.clip(image, 0, 1) * 255).astype(np.uint8)
            else:
                image = image.astype(np.uint8)
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
        return image

    @retry_async(
        config=RetryConfig(
            max_attempts=_MAX_RETRIES,
            backoff_base=_RETRY_BASE_DELAY,
            backoff_max=_RETRY_MAX_DELAY,
            exceptions=(Exception,),
        )
    )
    async def _call_vision_api(self, prompt: str, image_b64: str, corr_id: str):
        """Call vision LLM with retry logic."""
        if sys.version_info >= (3, 9):
            return await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}",
                                    "detail": "high",
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
                temperature=0,
                max_tokens=1200,
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
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}",
                                        "detail": "high",
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": prompt,
                                },
                            ],
                        }
                    ],
                    temperature=0,
                    max_tokens=1200,
                    response_format={"type": "json_object"},
                    extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
                ),
            )

    def _estimate_tokens(self, text: str) -> int:
        """BATMAN-A: Rough token estimation for prompt safety."""
        return len(text) // 4

    def _image_to_b64_safe(self, image: np.ndarray, corr_id: str) -> Optional[str]:
        """
        DVMELTSS-S: Safe image encoding with PIL + memory limits.
        - Resizes if too large
        - Uses quality compression
        - Handles BGR->RGB conversion
        - Enforces memory limit to prevent OOM
        """
        try:
            if image is None or image.size == 0:
                return None

            # ✅ Validate and normalize image
            image = self._validate_chart_image(image, corr_id)

            # Convert BGR (OpenCV) to RGB (PIL) if needed
            if image.ndim == 3 and image.shape[2] == 3:
                image_rgb = np.ascontiguousarray(image[:, :, ::-1])
            else:
                image_rgb = image

            if image_rgb.nbytes > _MAX_IMAGE_MEMORY_MB * 1024 * 1024:
                logger.warning(f"[{corr_id}] Image too large ({image_rgb.nbytes / 1024 / 1024:.1f}MB) — downsampling")
                # Progressive downsampling until under limit
                scale = 0.5
                while image_rgb.nbytes > _MAX_IMAGE_MEMORY_MB * 1024 * 1024 and scale > 0.1:
                    h, w = image_rgb.shape[:2]
                    image_rgb = np.array(
                        Image.fromarray(image_rgb).resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                    )
                    scale *= 0.8

            # Resize if dimensions too large
            h, w = image_rgb.shape[:2]
            if h > _MAX_IMAGE_DIMENSION or w > _MAX_IMAGE_DIMENSION:
                scale = _MAX_IMAGE_DIMENSION / max(h, w)
                new_size = (int(w * scale), int(h * scale))
                pil_img = Image.fromarray(image_rgb)
                pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)
            else:
                pil_img = Image.fromarray(image_rgb)

            # Compress and encode
            buffer = io.BytesIO()
            pil_img.save(buffer, format="JPEG", quality=_MAX_IMAGE_QUALITY, optimize=True)
            buffer.seek(0)
            return base64.b64encode(buffer.read()).decode("utf-8")

        except Exception as e:
            logger.warning(f"[{corr_id}] Chart image encode failed: {e}")
            return None

    async def _call_llm_with_retry(
        self,
        prompt: str,
        image_b64: str,
        correlation_id: Optional[str] = None,
    ) -> Optional[dict]:
        """DVMELTSS-E: Async LLM call with centralized retry + structured validation."""
        corr_id = correlation_id or "chart_unknown"

        safe_prompt = escape_prompt_content(prompt)

        # Token safety
        if self._estimate_tokens(safe_prompt) > 6000:
            safe_prompt = safe_prompt[: 6000 * 4]

        try:
            response = await self._call_vision_api(safe_prompt, image_b64, corr_id)
            content = response.choices[0].message.content
            if not content:
                return None

            data = json.loads(content)
            # DVMELTSS-V: Validate via Pydantic
            ChartExtractionSchema.model_validate(data)
            return data

        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning(f"[{corr_id}] Chart extraction JSON/validation error: {e}")
            return None
        except Exception as e:
            err = classify_openai_error(e)
            if err and err.error_type == "quota":
                logger.warning(f"[{corr_id}] Chart extraction: quota exceeded")
                return None
            logger.warning(f"[{corr_id}] Chart extraction unexpected error: {type(e).__name__}: {e}")
            return None

    async def extract_from_image_async(
        self,
        image: np.ndarray,
        chart_id: str,
        source_file: str,
        page_number: int,
        chunk_id: str = "",
        correlation_id: Optional[str] = None,
    ) -> Optional[ExtractedChart]:
        """
        Async version: Extract chart data from a cropped image region.
        BATMAN-A: Non-blocking, yields to event loop.
        ✅ FIXED: Input validation + safe axes conversion + memory cleanup.
        """
        corr_id = correlation_id or "chart_unknown"

        # ✅ Validate image first
        try:
            image = self._validate_chart_image(image, corr_id)
        except Exception as e:
            logger.error(f"[{corr_id}] Invalid chart image: {e}")
            return None

        if image is None or image.size == 0:
            return None

        # Resize check before encoding
        h, w = image.shape[:2]
        if h < 30 or w < 30:
            logger.debug(f"[{corr_id}] Chart region too small: {h}x{w}")
            return None

        # Safe encoding with memory limits
        b64 = self._image_to_b64_safe(image, corr_id)
        if not b64:
            return None

        prompt = escape_prompt_content(CHART_EXTRACTION_PROMPT)

        try:
            data = await self._call_llm_with_retry(prompt, b64, corr_id)
            if not data:
                return self._empty_chart(chart_id, source_file, page_number, chunk_id, corr_id)

            axes_raw = data.get("axes")
            if axes_raw is None:
                axes_dict = {}
            elif isinstance(axes_raw, dict):
                axes_dict = axes_raw
            elif hasattr(axes_raw, "model_dump"):
                axes_dict = axes_raw.model_dump()
            else:
                axes_dict = {}

            data_points_raw = data.get("data_points", [])
            if hasattr(data_points_raw[0], "model_dump") if data_points_raw else False:
                data_points = [dp.model_dump() for dp in data_points_raw[:20]]
            else:
                data_points = [
                    dp if isinstance(dp, dict) else {"label": str(dp), "value": ""} for dp in data_points_raw[:20]
                ]

            return ExtractedChart(
                chart_id=chart_id,
                source_file=source_file,
                page_number=page_number,
                chunk_id=chunk_id,
                chart_type=data.get("chart_type", "other"),
                title=data.get("title"),
                description=data.get("description", ""),
                data_points=data_points,
                axes=axes_dict,
                key_takeaway=data.get("key_takeaway", ""),
                all_text=data.get("all_text", ""),
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Chart extraction API failed: {type(e).__name__}: {e}")
            return None
        finally:
            # ✅ Memory cleanup hint — check CUDA handle before deletion
            if hasattr(image, "__cuda_array_interface__"):
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except ImportError:
                    pass
            del image, b64
            gc.collect()

    @staticmethod
    def _empty_chart(
        chart_id: str,
        source_file: str,
        page_number: int,
        chunk_id: str,
        correlation_id: Optional[str] = None,
    ) -> ExtractedChart:
        return ExtractedChart(
            chart_id=chart_id,
            source_file=source_file,
            page_number=page_number,
            chunk_id=chunk_id,
            chart_type="other",
            title=None,
            description="Figure could not be analyzed.",
            correlation_id=correlation_id,
        )

    def extract_from_image(
        self,
        image: np.ndarray,
        chart_id: str,
        source_file: str,
        page_number: int,
        chunk_id: str = "",
        correlation_id: Optional[str] = None,
    ) -> Optional[ExtractedChart]:
        """
        Sync wrapper — use extract_from_image_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return None
            logger.warning(
                "⚠️ ChartExtractor.extract_from_image() called from async context — "
                "use extract_from_image_async() instead. Returning None."
            )
            return None
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(
                self.extract_from_image_async(image, chart_id, source_file, page_number, chunk_id, correlation_id)
            )


# DVMELTSS-M: Explicit module exports
__all__ = ["ChartExtractor", "ExtractedChart"]
# Local smoke test entry point. Run: python -m

