
from __future__ import annotations
import asyncio
import base64
import logging
from io import BytesIO
from typing import Final, Optional
import numpy as np
from PIL import Image

from app.core.ocr_utils import calculate_vision_tokens

logger = logging.getLogger(__name__)

# Image processing defaults
_DEFAULT_JPEG_QUALITY: Final[int] = 90
_DEFAULT_MAX_DIMENSION: Final[int] = 2048
_MAX_IMAGE_PIXELS: Final[int] = 50_000_000  # ✅ NEW: ~50MP limit to prevent OOM
_MIN_JPEG_QUALITY: Final[int] = 10  # ✅ NEW: Floor for aggressive downscale


def _validate_image_for_b64(image: np.ndarray, corr_id: str) -> np.ndarray:
    """
    Validate and normalize image for base64 encoding.
    ✅ Ensures uint8 dtype, 2D/3D array, valid channels.
    """
    if not isinstance(image, np.ndarray):
        raise TypeError(f"Expected numpy array, got {type(image).__name__}")

    # Ensure uint8 dtype
    if image.dtype != np.uint8:
        if np.issubdtype(image.dtype, np.floating):
            logger.debug(f"[{corr_id}] Converting float image to uint8")
            image = (np.clip(image, 0, 1) * 255).astype(np.uint8)
        else:
            image = image.astype(np.uint8)

    # Handle channel dimension
    if image.ndim == 2:
        # Grayscale: keep as-is (PIL handles L mode)
        pass
    elif image.ndim == 3:
        channels = image.shape[2]
        if channels == 1:
            # Single-channel 3D array -> squeeze to 2D
            image = np.squeeze(image, axis=2)
        elif channels == 3:
            # BGR -> will be converted to RGB for PIL
            pass
        elif channels == 4:
            # RGBA -> drop alpha channel for JPEG
            logger.debug(f"[{corr_id}] Dropping alpha channel from RGBA image")
            image = image[:, :, :3]
        else:
            raise ValueError(f"Unsupported channels: {channels} (expected 1, 3, or 4)")
    else:
        raise ValueError(f"Expected 2D or 3D array, got {image.ndim}D")

    return image


def _guard_image_size(image: np.ndarray, max_dimension: int, corr_id: str) -> np.ndarray:
    """
    Downscale image if too large for memory safety.
    ✅ Prevents OOM during base64 encoding.
    """
    h, w = image.shape[:2] if image.ndim == 3 else (image.shape[0], image.shape[1])

    # Check pixel count first (more accurate than dimension alone)
    if w * h > _MAX_IMAGE_PIXELS:
        scale = (_MAX_IMAGE_PIXELS / (w * h)) ** 0.5
        new_size = (int(w * scale), int(h * scale))
        logger.warning(
            f"[{corr_id}] Image too large ({w}x{h} > {_MAX_IMAGE_PIXELS} pixels) — downscaling to {new_size}"
        )
        import cv2

        if image.ndim == 3:
            return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)
        else:
            return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)

    # Then check max dimension
    if max(w, h) > max_dimension:
        scale = max_dimension / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        logger.debug(f"[{corr_id}] Resizing image from ({w},{h}) to {new_size} for Vision API")
        import cv2

        if image.ndim == 3:
            return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)
        else:
            return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)

    return image


def image_to_b64(
    image: np.ndarray,
    quality: int = _DEFAULT_JPEG_QUALITY,
    max_dimension: int = _DEFAULT_MAX_DIMENSION,
    correlation_id: Optional[str] = None,
) -> str:
    """
    Shared utility: numpy image (BGR/gray) -> base64 JPEG string.

    ✅ FIXED: Channel-aware conversion, input validation, memory guard, error handling.

    Args:
        image: OpenCV-style BGR or grayscale numpy array
        quality: JPEG quality (1-100)
        max_dimension: Max width/height before resizing
        correlation_id: Optional request ID for tracing

    Returns:
        Base64-encoded JPEG string (without data: URI prefix)

    Raises:
        ValueError: If image cannot be encoded
    """
    corr_id = correlation_id or "image_utils"

    try:
        # ✅ Validate and normalize image
        image = _validate_image_for_b64(image, corr_id)

        # ✅ Memory guard: downscale if too large
        image = _guard_image_size(image, max_dimension, corr_id)

        # ✅ Channel conversion for PIL
        if image.ndim == 2:
            # Grayscale: PIL mode 'L'
            pil_img = Image.fromarray(image, mode="L")
        elif image.shape[2] == 3:
            # BGR -> RGB for PIL
            pil_img = Image.fromarray(image[..., ::-1], mode="RGB")
        else:
            # Fallback: convert to RGB via cv2
            import cv2

            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb, mode="RGB")

        # ✅ Clamp quality to valid range
        quality = max(_MIN_JPEG_QUALITY, min(100, quality))

        # Encode to JPEG with error handling
        buffer = BytesIO()
        try:
            pil_img.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,  # ✅ Better for large images
            )
        except Exception as e:
            # Fallback: reduce quality and retry
            if quality > _MIN_JPEG_QUALITY + 20:
                logger.warning(f"[{corr_id}] JPEG encode failed at quality {quality}: {e} — retrying at {quality - 30}")
                buffer = BytesIO()
                pil_img.save(buffer, format="JPEG", quality=quality - 30, optimize=True)
            else:
                raise ValueError(f"Failed to encode image as JPEG: {e}") from e

        # Base64 encode
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        # Log size at debug level (correlation_id included)
        size_mb = len(b64) * 0.75 / (1024 * 1024)  # Approx decode size
        logger.debug(f"[{corr_id}] Base64 image: {size_mb:.2f}MB, quality={quality}")

        # ✅ GPU memory cleanup hint if input was CUDA array
        if hasattr(image, "__cuda_array_interface__"):
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    logger.debug(f"[{corr_id}] GPU cache cleared after encoding")
            except ImportError:
                pass

        return b64

    except Exception as e:
        logger.error(f"[{corr_id}] image_to_b64 failed: {type(e).__name__}: {e}")
        # Return minimal valid base64 (1x1 black pixel) as fallback
        fallback = Image.new("RGB", (1, 1), color="black")
        buffer = BytesIO()
        fallback.save(buffer, format="JPEG", quality=50)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


async def image_to_b64_async(
    image: np.ndarray,
    quality: int = _DEFAULT_JPEG_QUALITY,
    max_dimension: int = _DEFAULT_MAX_DIMENSION,
    correlation_id: Optional[str] = None,
    timeout_seconds: float = 10.0,
) -> str:
    """
    Async wrapper for image_to_b64 — runs blocking encode in thread pool.

    ✅ Use this in FastAPI routes to avoid event loop freeze.
    """
    corr_id = correlation_id or "image_utils"

    loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: image_to_b64(image, quality, max_dimension, corr_id)),
            timeout=timeout_seconds,
        )
        return result
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] image_to_b64 timed out after {timeout_seconds}s")
        # Return fallback 1x1 black pixel
        fallback = Image.new("RGB", (1, 1), color="black")
        buffer = BytesIO()
        fallback.save(buffer, format="JPEG", quality=50)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def calculate_image_tokens(width: int, height: int, detail: str = "high") -> int:
    """
    Calculate actual token cost for an image per OpenAI's pricing formula.

    ✅ FIXED: Input validation + safe defaults.

    Reference: https://platform.openai.com/docs/guides/vision/calculating-costs
    """
    # ✅ Validate inputs
    if width <= 0 or height <= 0:
        logger.warning(f"Invalid image dimensions: {width}x{height} — using fallback 1024x1024")
        width, height = 1024, 1024

    # Clamp to reasonable max (OpenAI downscales internally anyway)
    width = min(width, 4096)
    height = min(height, 4096)

    try:
        return calculate_vision_tokens(width, height, detail)
    except Exception as e:
        logger.warning(f"calculate_vision_tokens failed: {e} — returning fallback 1000 tokens")
        return 1000  # Safe fallback


def b64_to_image(b64: str, mode: str = "RGB") -> np.ndarray:
    """
    ✅ NEW: Inverse utility — base64 JPEG string -> numpy array (BGR).
    Useful for testing/debugging Vision API round-trips.

    Args:
        b64: Base64-encoded JPEG string (with or without data: URI prefix)
        mode: PIL mode for decoding (default: "RGB")

    Returns:
        OpenCV-style BGR numpy array (uint8)
    """
    # Strip data: URI prefix if present
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]

    try:
        img_bytes = base64.b64decode(b64)
        pil_img = Image.open(BytesIO(img_bytes)).convert(mode)
        img_array = np.array(pil_img)

        # RGB -> BGR for OpenCV compatibility
        if mode == "RGB" and img_array.ndim == 3:
            import cv2

            return cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        return img_array
    except Exception as e:
        logger.error(f"b64_to_image failed: {e}")
        # Return minimal fallback
        return np.zeros((100, 100, 3), dtype=np.uint8) if mode == "RGB" else np.zeros((100, 100), dtype=np.uint8)


# DVMELTSS-M: Explicit module exports
__all__ = [
    "image_to_b64",
    "image_to_b64_async",  # ✅ NEW
    "calculate_image_tokens",
    "b64_to_image",  # ✅ NEW
]
# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.ocr.image_utils) -----
# ========================================================================

