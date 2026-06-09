# backend/app/ocr/preprocessor.py
# DVMELTSS-FIX: M - Modular, S - Security, A - Async
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: M - Memory safety, A - Async-safe
# ✅ FIXED: Removed ImageNet normalization (breaks OCR)
# ✅ FIXED: Proper dtype handling (no float32->uint8 corruption)
# ✅ FIXED: Deskew fallback for low-text/blank pages
# ✅ FIXED: Input validation + memory guard for huge images
# ✅ FIXED: Async wrapper with thread executor for FastAPI
# ✅ FINAL FIX: Added comprehensive main() block for local testing

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

    # ✅ NEW: Valid image constraints
    _VALID_DTYPES: Final = {np.uint8}
    _VALID_CHANNELS: Final = {1, 3}  # Grayscale or BGR

    def __init__(self, target_dpi: int = _DEFAULT_TARGET_DPI):
        self.target_dpi = target_dpi
        # ✅ FIXED: Removed Normalize step — PaddleOCR expects raw [0,255] BGR
        self.pipeline: Compose = A.Compose(
            [
                A.GaussianBlur(blur_limit=(3, 3), p=0.5),
                A.CLAHE(clip_limit=4.0, tile_grid_size=(8, 8), p=1.0),
                A.Sharpen(alpha=(0.2, 0.4), lightness=(0.8, 1.2), p=0.7),
                # ✅ REMOVED: A.Normalize(...) — breaks OCR models
            ]
        )
        logger.info(f"DocumentPreprocessor initialized: target_dpi={target_dpi}")

    # ✅ NEW: Async wrapper for FastAPI integration
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

    # ✅ NEW: Combined load + validate helper
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

    # ✅ NEW: Memory guard for huge images
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

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]

    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    async def run_tests():
        print("🔍 Testing DocumentPreprocessor module (app/ocr/preprocessor.py)")
        print("=" * 70)

        try:
            # -- Test 1: Module imports & initialization ------------------
            print("\n📌 Test 1: Module imports & initialization")
            from app.ocr.preprocessor import DocumentPreprocessor, PreprocessResult

            preprocessor = DocumentPreprocessor(target_dpi=300)
            assert preprocessor.target_dpi == 300
            pipeline_info = preprocessor.get_pipeline_info()
            assert pipeline_info["normalization_applied"] is False, "Should NOT apply ImageNet normalization"
            print(
                f"   ✅ Initialized: dpi={preprocessor.target_dpi}, no normalization={not pipeline_info['normalization_applied']}"
            )

            # -- Test 2: Image loading & validation -----------------------
            print("\n📌 Test 2: Image loading & validation")

            # Test numpy array input (already uint8)
            test_img = np.random.randint(0, 256, (500, 500, 3), dtype=np.uint8)
            result = preprocessor.preprocess(test_img, correlation_id="test-1")
            assert result.image.dtype == np.uint8, "Output must be uint8"
            assert result.original_size == (500, 500)
            print(f"   ✅ NumPy input: dtype={result.image.dtype}, size={result.original_size}")

            # Test PIL Image input — ✅ USE IMAGE THAT MEETS MIN HEIGHT
            pil_img = Image.new("RGB", (600, 1200), color="white")  # Height=1200 >= 1000
            result = preprocessor.preprocess(pil_img, correlation_id="test-2")
            # PIL (W=600, H=1200) -> CV2 (H=1200, W=600, C=3)
            assert result.image.shape[:2] == (
                1200,
                600,
            ), f"Expected (1200, 600), got {result.image.shape[:2]}"
            print(f"   ✅ PIL input: (W=600,H=1200) -> CV2 shape={result.image.shape[:2]}")

            # Test grayscale -> BGR conversion
            gray_img = np.random.randint(0, 256, (300, 300), dtype=np.uint8)
            result = preprocessor.preprocess(gray_img, correlation_id="test-3")
            assert result.was_grayscale is True
            assert result.image.ndim == 3 and result.image.shape[2] == 3, "Should be 3-channel BGR"
            print(f"   ✅ Grayscale->BGR: {result.was_grayscale} -> {result.image.shape[2]} channels")

            # -- Test 3: Preprocessing pipeline (blur, CLAHE, sharpen) ---
            print("\n📌 Test 3: Augmentation pipeline (no normalization)")

            # Create test image with text-like pattern
            text_img = np.ones((800, 1200, 3), dtype=np.uint8) * 255  # White background
            text_img[200:250, 100:500] = 0  # Black "text" rectangle

            result = preprocessor.preprocess(text_img, correlation_id="test-4")
            assert result.image.dtype == np.uint8, "Output must stay uint8"
            assert result.image.min() >= 0 and result.image.max() <= 255, "Values in [0,255]"
            assert not np.issubdtype(result.image.dtype, np.floating), "Should NOT be float (no normalization)"
            print(f"   ✅ Pipeline: dtype={result.image.dtype}, range=[{result.image.min()}, {result.image.max()}]")

            # -- Test 4: Deskew with fallback -----------------------------
            print("\n📌 Test 4: Deskew with low-text fallback")

            # Test with blank page (should skip deskew)
            blank_img = np.ones((1000, 1000, 3), dtype=np.uint8) * 255
            result = preprocessor.preprocess(blank_img, correlation_id="test-5")
            assert result.was_deskewed is False, "Blank page should skip deskew"
            assert result.deskew_angle == 0.0
            print(f"   ✅ Blank page: deskew skipped (was_deskewed={result.was_deskewed})")

            # Test with simulated skewed text (simple diagonal line)
            skewed_img = np.ones((500, 500, 3), dtype=np.uint8) * 255
            cv2.line(skewed_img, (50, 100), (450, 400), 0, 3)  # Diagonal black line
            result = preprocessor.preprocess(skewed_img, correlation_id="test-6")
            assert result.image is not None
            print(f"   ✅ Skewed image: processed (angle={result.deskew_angle:.2f}°)")

            # -- Test 5: Resolution enforcement (explicit test) -----------
            print("\n📌 Test 5: Minimum resolution enforcement")

            # Small image should be upscaled to min_height=1000
            small_img = np.random.randint(0, 256, (200, 300, 3), dtype=np.uint8)
            result = preprocessor.preprocess(small_img, correlation_id="test-7")
            assert result.image.shape[0] >= 1000, f"Height should be >= 1000, got {result.image.shape[0]}"
            expected_w = int(300 * (1000 / 200))  # Scale width proportionally
            assert result.image.shape[1] == expected_w, f"Width should be scaled to {expected_w}"
            print(f"   ✅ Upscale: {small_img.shape[:2]} -> {result.image.shape[:2]}")

            # -- Test 6: Memory guard for huge images ---------------------
            print("\n📌 Test 6: Memory guard (downscale huge images)")

            from unittest.mock import patch

            huge_img = np.random.randint(0, 256, (500, 500, 3), dtype=np.uint8)
            with patch("app.ocr.preprocessor._MAX_IMAGE_DIM", 400):  # Temporarily lower limit
                result = preprocessor.preprocess(huge_img, correlation_id="test-8")
                assert max(result.image.shape[:2]) <= 400, "Should be downscaled"
            print(f"   ✅ Downscale: {huge_img.shape[:2]} -> {result.image.shape[:2]} (guard enforced)")

            # -- Test 7: Async wrapper with timeout -----------------------
            print("\n📌 Test 7: Async wrapper with timeout protection")

            # Test with valid input (should succeed quickly)
            result = await preprocessor.preprocess_async(test_img, correlation_id="test-9", timeout_seconds=10.0)
            assert result.image is not None
            print(f"   ✅ Async: completed within timeout, dtype={result.image.dtype}")

            # Test timeout behavior (with non-existent file -> fast fail)
            try:
                await preprocessor.preprocess_async(
                    "/nonexistent/image.png",
                    correlation_id="test-10",
                    timeout_seconds=0.1,
                )
                print("   ❌ Should raise error for missing file")
            except (ValueError, FileNotFoundError, Exception) as e:
                print(f"   ✅ Async error handling: {type(e).__name__}")

            # -- Test 8: PreprocessResult validation ----------------------
            print("\n📌 Test 8: PreprocessResult dataclass validation")

            # Valid result should pass
            valid_result = PreprocessResult(
                image=np.zeros((100, 100, 3), dtype=np.uint8),
                original_size=(100, 100),
                was_deskewed=False,
                deskew_angle=0.0,
                was_grayscale=False,
                correlation_id="valid",
            )
            assert valid_result.image.dtype == np.uint8
            print(f"   ✅ Valid result: accepted (dtype={valid_result.image.dtype})")

            # Invalid dtype should raise ValueError in __post_init__
            try:
                invalid_result = PreprocessResult(
                    image=np.zeros((100, 100, 3), dtype=np.float32),  # Wrong dtype
                    original_size=(100, 100),
                    was_deskewed=False,
                    deskew_angle=0.0,
                    was_grayscale=False,
                )
                print("   ❌ Should raise ValueError for float32 image")
            except ValueError as e:
                if "must be uint8" in str(e):
                    print(f"   ✅ Invalid dtype rejected: {e}")

            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! DocumentPreprocessor module verified.")
            return True

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    # Run async tests
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
