from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Final, Optional
import numpy as np
from PIL import Image
from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError
from pydantic import BaseModel, ValidationError, Field

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.ingest_utils import generate_ingest_correlation_id
from app.core.retry import retry_async, RetryConfig
from app.core.openai_errors import classify_openai_error
from app.ocr.image_utils import image_to_b64

logger = logging.getLogger(__name__)
_MIN_IMAGE_DIM: Final = 50
_MAX_IMAGE_DIM: Final = 4096
_MAX_IMAGE_CHANNELS: Final = 4
_TROC_LOAD_TIMEOUT_SEC: Final = 60.0
_TROC_INFERENCE_TIMEOUT_SEC: Final = 30.0
_MAX_RETRIES: Final = 2
_RETRY_BASE_DELAY: Final = 1.0
_RETRY_MAX_DELAY: Final = 15.0


class HandwritingSchema(BaseModel):
    text: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    language: str = Field(default="en", min_length=2, max_length=5)
    notes: str = Field(default="", max_length=300)

    class Config:
        extra = "forbid"


@dataclass  # FIXED: Removed frozen=True
class HandwritingResult:
    """Result from handwriting OCR."""

    text: str
    confidence: float
    language: str = "en"
    model_used: str = "gpt-4o-vision"
    page_num: int = 0
    notes: str = ""
    error: Optional[str] = None
    correlation_id: Optional[str] = None  # FIXED: Added for tracing

    @property
    def is_legible(self) -> bool:
        return self.confidence >= 0.5 and len(self.text.strip()) > 0

    def to_dict(self) -> dict:
        return {
            "text_preview": self.text[:200] + ("..." if len(self.text) > 200 else ""),
            "confidence": round(self.confidence, 3),
            "language": self.language,
            "model_used": self.model_used,
            "page_num": self.page_num,
            "is_legible": self.is_legible,
            "error": self.error,
            "correlation_id": self.correlation_id,  # FIXED: Include
        }


HANDWRITING_SYSTEM_PROMPT = """You are an expert handwriting OCR system.
Transcribe ALL handwritten text from the image with maximum accuracy.
Return ONLY valid JSON matching this schema:
{{
  "text": "complete transcription preserving line breaks",
  "confidence": 0.92, "language": "en",
  "notes": "any observations about handwriting quality, crossed-out text, etc."
}}
Rules:
- Transcribe EVERY word, even if partially illegible; Preserve line structure with \\n
- For illegible words: use [illegible] placeholder; For crossed-out text: include as ~~crossed out~~
- confidence: 0.0 (completely illegible) to 1.0 (perfectly clear)
- Preserve numbers, dates, and punctuation exactly; Note if text is printed vs cursive vs mixed
"""


class HandwritingOCR:
    """Two-stage handwriting OCR pipeline: GPT-4o Vision + TrOCR fallback."""

    def __init__(
        self,
        use_vision_primary: bool = True,
        trocr_model_path: str = "",
        max_retries: int = _MAX_RETRIES,
    ):
        settings = get_settings()
        api_key = settings.openai_api_key
        if not api_key:
            raise ValueError("OpenAI API key required for handwriting OCR")
        self.client = AsyncOpenAI(api_key=api_key, timeout=30.0)
        self.model = settings.openai_chat_model
        self.use_vision = use_vision_primary
        self.trocr_model_path = trocr_model_path or getattr(settings, "trocr_model_path", "")
        self.max_retries = max_retries
        self._trocr_pipeline = None
        self._trocr_loading = False
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=max_retries,
                backoff_base=_RETRY_BASE_DELAY,
                backoff_max=_RETRY_MAX_DELAY,
                exceptions=(Exception,),
            )
        )
        logger.info(f"HandwritingOCR initialized: vision={self.use_vision}, model={self.model}")

    def _validate_image(self, image: np.ndarray) -> bool:
        """DVMELTSS-V: Validate image dimensions and channels."""
        if image is None or image.size == 0:
            return False
        if len(image.shape) == 2:
            h, w, c = image.shape[0], image.shape[1], 1
        elif len(image.shape) == 3:
            h, w, c = image.shape
        else:
            return False
        if c > _MAX_IMAGE_CHANNELS:
            return False
        if min(h, w) < _MIN_IMAGE_DIM or max(h, w) > _MAX_IMAGE_DIM:
            return False
        return True

    async def _call_vision_with_retry(self, image_b64: str, correlation_id: str) -> Optional[HandwritingSchema]:
        """DVMELTSS-E: Async Vision API call with retry + validation."""
        corr_id = correlation_id
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": HANDWRITING_SYSTEM_PROMPT},
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
                                    "text": "Transcribe all handwritten text from this image.",
                                },
                            ],
                        },
                    ],
                    temperature=0,
                    max_tokens=2000,
                    response_format={"type": "json_object"},
                )
                raw = response.choices[0].message.content
                if not raw:
                    return None
                data = json.loads(raw)
                HandwritingSchema.model_validate(data)
                return HandwritingSchema(**data)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                err = classify_openai_error(e)
                if err.error_type == "quota":
                    logger.warning(f"[{corr_id}] Vision OCR: quota exceeded")
                    return None
                if attempt < self.max_retries:
                    wait = min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY)
                    logger.warning(f"[{corr_id}] Vision OCR retry {attempt+1} in {wait}s")
                    await asyncio.sleep(wait)
                else:
                    return None
            except (ValidationError, json.JSONDecodeError) as e:
                logger.warning(f"[{corr_id}] Vision OCR JSON/validation error: {e}")
                return None
            except Exception as e:
                logger.warning(f"[{corr_id}] Vision OCR unexpected error: {type(e).__name__}")
                if attempt < self.max_retries:
                    await asyncio.sleep(_RETRY_BASE_DELAY)
                else:
                    return None
        return None

    async def transcribe_page_async(
        self, image: np.ndarray, page_num: int = 0, correlation_id: Optional[str] = None
    ) -> HandwritingResult:
        """Async version: Transcribe a single handwritten page image."""
        corr_id = correlation_id or generate_ingest_correlation_id("handwriting")
        if not self._validate_image(image):
            return HandwritingResult(
                text="",
                confidence=0.0,
                page_num=page_num,
                error="Invalid image dimensions/channels",
                correlation_id=corr_id,
            )
        if self.use_vision:
            result = await self._vision_transcribe_async(image, page_num, corr_id)
            if result.is_legible:
                return result
            logger.warning(f"[{corr_id}] Vision low confidence ({result.confidence:.2f}) — trying TrOCR fallback")
        return await self._trocr_transcribe_async(image, page_num, corr_id)

    async def _vision_transcribe_async(
        self, image: np.ndarray, page_num: int, correlation_id: str
    ) -> HandwritingResult:
        """Async: Transcribe using GPT-4o Vision API."""
        corr_id = correlation_id
        try:
            b64 = await asyncio.to_thread(image_to_b64, image, quality=92, max_dimension=2048)
            if not b64:
                return HandwritingResult(
                    text="",
                    confidence=0.0,
                    page_num=page_num,
                    error="Image encoding failed",
                    correlation_id=corr_id,
                )
            schema = await self._call_vision_with_retry(b64, corr_id)
            if not schema:
                return HandwritingResult(
                    text="",
                    confidence=0.0,
                    page_num=page_num,
                    error="Vision API returned no valid response",
                    model_used="gpt-4o-vision",
                    correlation_id=corr_id,
                )
            return HandwritingResult(
                text=schema.text.strip(),
                confidence=schema.confidence,
                language=schema.language,
                model_used="gpt-4o-vision",
                page_num=page_num,
                notes=schema.notes,
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Vision transcription failed page {page_num}: {type(e).__name__}")
            return HandwritingResult(
                text="",
                confidence=0.0,
                page_num=page_num,
                error=f"Vision error: {type(e).__name__}",
                model_used="gpt-4o-vision",
                correlation_id=corr_id,
            )

    async def _trocr_transcribe_async(self, image: np.ndarray, page_num: int, correlation_id: str) -> HandwritingResult:
        """Async: Fallback transcription using TrOCR model."""
        corr_id = correlation_id
        try:
            pipeline = await self._get_trocr_pipeline_async()
            if pipeline is None:
                return HandwritingResult(
                    text="[TrOCR not available]",
                    confidence=0.0,
                    page_num=page_num,
                    model_used="trocr",
                    error="TrOCR pipeline not loaded",
                    correlation_id=corr_id,
                )
            pil_img = await asyncio.to_thread(lambda: Image.fromarray(image[..., ::-1]))
            loop = asyncio.get_running_loop()
            text = await asyncio.wait_for(
                loop.run_in_executor(None, self._trocr_segment_transcribe, pipeline, pil_img),
                timeout=_TROC_INFERENCE_TIMEOUT_SEC,
            )
            return HandwritingResult(
                text=text,
                confidence=0.7 if text.strip() else 0.0,
                language="en",
                model_used="trocr-fallback",
                page_num=page_num,
                correlation_id=corr_id,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{corr_id}] TrOCR inference timed out after {_TROC_INFERENCE_TIMEOUT_SEC}s")
            return HandwritingResult(
                text="",
                confidence=0.0,
                page_num=page_num,
                error="TrOCR timeout",
                model_used="trocr",
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] TrOCR fallback failed page {page_num}: {type(e).__name__}")
            return HandwritingResult(
                text="",
                confidence=0.0,
                page_num=page_num,
                error=f"TrOCR error: {type(e).__name__}",
                model_used="trocr",
                correlation_id=corr_id,
            )

    async def _get_trocr_pipeline_async(self):
        """Async-safe lazy-load TrOCR pipeline with timeout & lock."""
        if self._trocr_pipeline is not None:
            return self._trocr_pipeline
        if self._trocr_loading:
            return None
        self._trocr_loading = True
        try:
            loop = asyncio.get_running_loop()
            pipeline = await asyncio.wait_for(
                loop.run_in_executor(None, self._load_trocr_sync),
                timeout=_TROC_LOAD_TIMEOUT_SEC,
            )
            self._trocr_pipeline = pipeline
            return pipeline
        except asyncio.TimeoutError:
            logger.warning(f"TrOCR loading timed out after {_TROC_LOAD_TIMEOUT_SEC}s")
            return None
        except Exception as e:
            logger.error(f"TrOCR load failed: {type(e).__name__}")
            return None
        finally:
            self._trocr_loading = False

    @staticmethod
    def _load_trocr_sync():
        """Sync model loading (called via executor)."""
        try:
            from transformers import pipeline as hf_pipeline

            settings = get_settings()
            model_id = getattr(settings, "trocr_model_path", "") or "microsoft/trocr-large-handwritten"
            logger.info(f"Loading TrOCR: {model_id}")
            return hf_pipeline("image-to-text", model=model_id, device=-1)
        except ImportError:
            logger.warning("transformers not installed — TrOCR unavailable")
            return None
        except Exception as e:
            logger.error(f"TrOCR load failed: {e}")
            return None

    @staticmethod
    def _trocr_segment_transcribe(pipeline, pil_img: Image.Image) -> str:
        """Split image into horizontal strips and transcribe each."""
        w, h = pil_img.size
        strip_height = max(60, h // 20)
        lines = []
        for y in range(0, h, strip_height):
            strip = pil_img.crop((0, y, w, min(y + strip_height, h)))
            try:
                result = pipeline(strip)
                if result and result[0].get("generated_text"):
                    text = result[0]["generated_text"].strip()
                    if text:
                        lines.append(text)
            except Exception:
                continue
        return "\n".join(lines)

    async def transcribe_document_async(
        self,
        images: list[np.ndarray],
        source_file: str = "",
        correlation_id: Optional[str] = None,
    ) -> list[HandwritingResult]:
        """Async version: Transcribe all pages of a handwritten document."""
        corr_id = correlation_id or generate_ingest_correlation_id("handwriting_doc")
        semaphore = asyncio.Semaphore(3)
        results = []

        async def process_page(i: int, img: np.ndarray) -> HandwritingResult:
            async with semaphore:
                return await self.transcribe_page_async(img, page_num=i, correlation_id=corr_id)

        tasks = [process_page(i, img) for i, img in enumerate(images)]
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
        successful = sum(1 for r in results if r.is_legible)
        logger.info(f"[{corr_id}] Handwriting OCR complete: {source_file} | {successful}/{len(results)} pages legible")
        return results

    def transcribe_page(
        self, image: np.ndarray, page_num: int = 0, correlation_id: Optional[str] = None
    ) -> HandwritingResult:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(
                self.transcribe_page_async(image, page_num, correlation_id), loop
            ).result()
        except RuntimeError:
            return asyncio.run(self.transcribe_page_async(image, page_num, correlation_id))

    def transcribe_document(
        self,
        images: list[np.ndarray],
        source_file: str = "",
        correlation_id: Optional[str] = None,
    ) -> list[HandwritingResult]:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(
                self.transcribe_document_async(images, source_file, correlation_id),
                loop,
            ).result()
        except RuntimeError:
            return asyncio.run(self.transcribe_document_async(images, source_file, correlation_id))


# DVMELTSS-M: Explicit module exports
__all__ = ["HandwritingOCR", "HandwritingResult"]
# Local smoke test entry point. Run: python -m

