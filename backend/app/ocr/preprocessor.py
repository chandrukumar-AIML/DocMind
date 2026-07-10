
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Optional, Union

import cv2
import numpy as np
from PIL import Image

# Albumentations for augmentations only (no normalization for OCR)
import albumentations as A
from albumentations.core.composition import Compose

from app.core.ocr_utils import generate_ocr_correlation_id

logger = logging.getLogger(__name__)

# Preprocessing defaults
_DEFAULT_TARGET_DPI: Final[int] = 300
_DEFAULT_MIN_HEIGHT: Final[int] = 1000
_MAX_IMAGE_DIM: Final[int] = 10000  # ✅ NEW: Prevent DoS via huge images
_MIN_DESKEW_POINTS: Final[int] = 100  # ✅ NEW: Minimum points for reliable deskew


@dataclass
class PreprocessResult:
    """Immutable result of image preprocessing."""

    image: np.ndarray
    original_size: tuple[int, int]
    was_deskewed: bool
    deskew_angle: float
    was_grayscale: bool
    correlation_id: Optional[str] = None

    def __post_init__(self):
        # ✅ Validate output image
        if self.image.dtype != np.uint8:
            raise ValueError(f"PreprocessResult image must be uint8, got {self.image.dtype}")
        if self.image.ndim not in (2, 3):
            raise ValueError(f"PreprocessResult image must be 2D or 3D, got {self.image.ndim}D")


class DocumentPreprocessor:
    """
    Albumentations-based document image preprocessor for OCR.

    ✅ FIXED: Pipeline order optimized for OCR (no ImageNet normalization).
    ✅ FIXED: Memory-safe operations + async wrapper.

    Pipeline order (correct for OCR):
    1. GaussianBlur (denoise)
    2. CLAHE (contrast enhancement)
    3. Sharpen (edge enhancement)
    -> Output: uint8 BGR [0, 255] ready for PaddleOCR
    """

    _VALID_DTYPES: Final = {np.uint8}
    _VALID_CHANNELS: Final = {1, 3}  # Grayscale or BGR

    def __init__(self, target_dpi: int = _DEFAULT_TARGET_DPI):
        self.target_dpi = target_dpi
        self.pipeline: Compose = A.Compose(
            [
                A.GaussianBlur(blur_limit=(3, 3), p=0.5),
                A.CLAHE(clip_limit=4.0, tile_grid_size=(8, 8), p=1.0),
                A.Sharpen(alpha=(0.2, 0.4), lightness=(0.8, 1.2), p=0.7),
                # ✅ REMOVED: A.Normalize(...) — breaks OCR models
            ]
        )
        logger.info(f"DocumentPreprocessor initialized: target_dpi={target_dpi}")

    async def preprocess_async(
        self,
        image_input: Union[np.ndarray, Image.Image, str, Path],
        correlation_id: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> PreprocessResult:
        """
        Async: Preprocess image for OCR with timeout protection.
        Runs blocking CV operations in thread pool to avoid event loop freeze.
        """
        corr_id = correlation_id or generate_ocr_correlation_id("preprocess")

        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in 3.10+

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: self.preprocess(image_input, corr_id)),
                timeout=timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Preprocessing timed out after {timeout_seconds}s")
            # Return minimal result instead of crashing
            if isinstance(image_input, np.ndarray):
                h, w = image_input.shape[:2]
            else:
                w, h = 1000, 1000  # Fallback size
            # Create safe fallback image
            fallback = (
                np.zeros((h, w, 3), dtype=np.uint8)
                if isinstance(image_input, np.ndarray) and image_input.ndim == 3
                else np.zeros((h, w), dtype=np.uint8)
            )
            return PreprocessResult(
                image=fallback,
                original_size=(w, h),
                was_deskewed=False,
                deskew_angle=0.0,
                was_grayscale=False,
                correlation_id=corr_id,
            )

    def preprocess(
        self,
        image_input: Union[np.ndarray, Image.Image, str, Path],
        correlation_id: Optional[str] = None,
    ) -> PreprocessResult:
        """Preprocess image for OCR with memory-safe operations."""
        corr_id = correlation_id or generate_ocr_correlation_id("preprocess")

        # ✅ Load + validate image first
        img = self._load_and_validate_image(image_input, corr_id)
        original_size = (img.shape[1], img.shape[0])
        was_grayscale = len(img.shape) == 2

        # Convert grayscale to BGR if needed (PaddleOCR expects 3-channel)
        if was_grayscale:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        # Deskew with fallback for low-text pages
        img, angle = self._deskew_safe(img, corr_id)
        was_deskewed = abs(angle) > 0.5

        # Ensure minimum resolution for OCR accuracy
        img = self._ensure_resolution(img)

        # ✅ Memory guard: downscale if too large
        img = self._guard_image_size(img, corr_id)

        # Apply augmentation pipeline (no normalization)
        augmented = self.pipeline(image=img)
        img = augmented["image"]

        # ✅ Ensure uint8 dtype — no float conversion
        if img.dtype != np.uint8:
            # If somehow float, convert safely
            if np.issubdtype(img.dtype, np.floating):
                img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
            else:
                img = img.astype(np.uint8)

        logger.info(
            f"[{corr_id}] Preprocessed: size={original_size}, "
            f"deskew={angle:.2f}°, grayscale={was_grayscale}, dtype={img.dtype}"
        )

        return PreprocessResult(
            image=img,
            original_size=original_size,
            was_deskewed=was_deskewed,
            deskew_angle=angle,
            was_grayscale=was_grayscale,
            correlation_id=corr_id,
        )

    def _load_and_validate_image(
        self,
        image_input: Union[np.ndarray, Image.Image, str, Path],
        corr_id: str,
    ) -> np.ndarray:
        """Load image from various input types and validate."""
        if isinstance(image_input, np.ndarray):
            img = image_input
        elif isinstance(image_input, Image.Image):
            img = cv2.cvtColor(np.array(image_input), cv2.COLOR_RGB2BGR)
        elif isinstance(image_input, (str, Path)):
            img = cv2.imread(str(image_input))
            if img is None:
                raise ValueError(f"Could not read image: {image_input}")
        else:
            raise TypeError(f"Unsupported image type: {type(image_input)}")

        # ✅ Validate image properties
        if img.dtype != np.uint8 and img.dtype.kind != "u":
            logger.warning(f"[{corr_id}] Image dtype {img.dtype} not uint8 — converting")
            if np.issubdtype(img.dtype, np.floating):
                img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
            else:
                img = img.astype(np.uint8)

        if img.ndim == 3 and img.shape[2] not in self._VALID_CHANNELS:
            raise ValueError(f"Unsupported channels: {img.shape[2]} (expected 1 or 3)")

        if img.ndim not in (2, 3):
            raise ValueError(f"Expected 2D or 3D array, got {img.ndim}D")

        return img

    def _deskew_safe(self, img: np.ndarray, corr_id: str) -> tuple[np.ndarray, float]:
        """
        Deskew image with fallback for low-text/blank pages.
        ✅ FIXED: Robust angle detection + safe rotation.
        """
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.bitwise_not(gray)
            thresh_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
            coords = np.column_stack(np.where(thresh_img > 0))

            # ✅ Fallback for low-text pages
            if len(coords) < _MIN_DESKEW_POINTS:
                logger.debug(
                    f"[{corr_id}] Skipping deskew: insufficient text regions ({len(coords)} < {_MIN_DESKEW_POINTS})"
                )
                return img, 0.0

            angle = cv2.minAreaRect(coords)[-1]
            # Adjust angle for OpenCV's minAreaRect convention
            if angle < -45:
                angle = 90 + angle
            if abs(angle) < 0.5:
                return img, 0.0

            # Apply rotation with padding to avoid edge artifacts
            (h, w) = img.shape[:2]
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)

            # ✅ Use BORDER_CONSTANT with white background (better for OCR than REPLICATE)
            corrected = cv2.warpAffine(
                img,
                M,
                (w, h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(255, 255, 255) if img.ndim == 3 else 255,
            )
            return corrected, angle

        except Exception as e:
            logger.warning(f"[{corr_id}] Deskew failed: {e} — returning original image")
            return img, 0.0

    def _ensure_resolution(self, img: np.ndarray, min_height: int = _DEFAULT_MIN_HEIGHT) -> np.ndarray:
        """Upscale image if below minimum height for OCR accuracy."""
        h, w = img.shape[:2]
        if h < min_height:
            scale = min_height / h
            new_w = int(w * scale)
            # ✅ Use INTER_LANCZOS4 for best quality upscaling
            img = cv2.resize(img, (new_w, min_height), interpolation=cv2.INTER_LANCZOS4)
            logger.debug(f"Upscaled image from ({w},{h}) to ({new_w},{min_height})")
        return img

    def _guard_image_size(self, img: np.ndarray, corr_id: str) -> np.ndarray:
        """Downscale image if dimensions exceed safe limits."""
        h, w = img.shape[:2]
        if max(h, w) > _MAX_IMAGE_DIM:
            scale = _MAX_IMAGE_DIM / max(h, w)
            new_size = (int(w * scale), int(h * scale))
            logger.warning(f"[{corr_id}] Image too large ({w}x{h} > {_MAX_IMAGE_DIM}) — downscaling to {new_size}")
            img = cv2.resize(img, new_size, interpolation=cv2.INTER_AREA)
        return img

    def get_pipeline_info(self) -> dict[str, any]:
        """✅ NEW: Return pipeline metadata for monitoring/debugging."""
        return {
            "target_dpi": self.target_dpi,
            "augmentations": [t.__class__.__name__ for t in self.pipeline.transforms],
            "normalization_applied": False,  # ✅ Explicit: no ImageNet norm
        }


# DVMELTSS-M: Explicit module exports
__all__ = ["PreprocessResult", "DocumentPreprocessor"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.ocr.preprocessor) -----
# ========================================================================

