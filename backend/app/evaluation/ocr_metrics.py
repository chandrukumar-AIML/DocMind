# backend/app/evaluation/ocr_metrics.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Scalability
# ✅ FIXED: None handling + safe division + input validation

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final, List, Optional, Any

import numpy as np

# DVMELTSS-M: Import centralized utilities
from app.core.eval_utils import generate_eval_correlation_id
from .text_utils import levenshtein_distance, normalize_text_for_ocr

logger = logging.getLogger(__name__)


@dataclass
class OCRPageMetrics:
    """Metrics for a single OCR-processed page."""
    page_num: int
    cer: float  # Character Error Rate [0.0, 1.0]
    wer: float  # Word Error Rate [0.0, 1.0]
    ocr_confidence: float  # Mean confidence from OCR engine
    char_count_pred: int  # Characters in predicted text
    char_count_gt: int  # Characters in ground truth
    used_vision_fallback: bool  # Whether GPT-4o Vision was used as fallback
    correlation_id: str = ""
    
    @property
    def accuracy_cer(self) -> float:
        """Character-level accuracy (1 - CER)."""
        # ✅ FIXED: Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, 1.0 - self.cer))
    
    @property
    def accuracy_wer(self) -> float:
        """Word-level accuracy (1 - WER)."""
        # ✅ FIXED: Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, 1.0 - self.wer))


@dataclass
class OCRDocumentMetrics:
    """Aggregated metrics for a multi-page document."""
    source_file: str
    pages: List[OCRPageMetrics] = field(default_factory=list)
    correlation_id: str = ""

    @property
    def mean_cer(self) -> float:
        """Mean Character Error Rate across all pages."""
        return float(np.mean([p.cer for p in self.pages])) if self.pages else 1.0

    @property
    def mean_wer(self) -> float:
        """Mean Word Error Rate across all pages."""
        return float(np.mean([p.wer for p in self.pages])) if self.pages else 1.0

    @property
    def mean_confidence(self) -> float:
        """Mean OCR confidence across all pages."""
        return float(np.mean([p.ocr_confidence for p in self.pages])) if self.pages else 0.0

    @property
    def vision_fallback_rate(self) -> float:
        """Proportion of pages that used Vision fallback."""
        if not self.pages:
            return 0.0
        return sum(1 for p in self.pages if p.used_vision_fallback) / len(self.pages)

    def summary(self) -> dict:
        """Return metrics summary for logging/monitoring."""
        return {
            "source_file": self.source_file,
            "page_count": len(self.pages),
            "mean_cer": round(self.mean_cer, 4),
            "mean_wer": round(self.mean_wer, 4),
            "mean_ocr_confidence": round(self.mean_confidence, 4),
            "vision_fallback_rate": round(self.vision_fallback_rate, 4),
            "accuracy_cer": round(1 - self.mean_cer, 4),
            "accuracy_wer": round(1 - self.mean_wer, 4),
            "correlation_id": self.correlation_id,
        }


# ✅ NEW: Input validation helper
def _validate_page_inputs(
    predicted: Optional[str],
    ground_truth: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate page inputs before metric computation."""
    if predicted is None or not isinstance(predicted, str):
        return False, "predicted must be a non-empty string"
    if ground_truth is None or not isinstance(ground_truth, str):
        return False, "ground_truth must be a non-empty string"
    return True, ""


class OCRMetricsCalculator:
    """
    Computes CER and WER between OCR output and ground truth text.
    
    Features:
    - Character-level and word-level error rates
    - Windowed approximation for very long texts
    - Confidence distribution analysis
    - Vision fallback tracking
    - Correlation ID propagation for tracing
    """

    MAX_CHARS_FOR_EXACT: Final = 5000  # Threshold for switching to windowed CER

    def compute_cer(self, predicted: str, ground_truth: str, normalize: bool = True) -> float:
        """Compute Character Error Rate (CER) = edit_distance / len(ground_truth)."""
        # ✅ FIXED: Handle None inputs
        if predicted is None or ground_truth is None:
            return 1.0
        
        if normalize:
            predicted = normalize_text_for_ocr(predicted)
            ground_truth = normalize_text_for_ocr(ground_truth)

        if not ground_truth:
            return 0.0 if not predicted else 1.0

        if len(predicted) > self.MAX_CHARS_FOR_EXACT or len(ground_truth) > self.MAX_CHARS_FOR_EXACT:
            logger.debug("Text too long for exact CER — using windowed approximation.")
            return self._windowed_cer(predicted, ground_truth, window=self.MAX_CHARS_FOR_EXACT)

        distance = levenshtein_distance(predicted, ground_truth)
        cer = distance / len(ground_truth)
        return max(0.0, min(1.0, cer))

    def compute_wer(self, predicted: str, ground_truth: str, normalize: bool = True) -> float:
        """Compute Word Error Rate (WER) = edit_distance_words / len(ground_truth_words)."""
        # ✅ FIXED: Handle None inputs
        if predicted is None or ground_truth is None:
            return 1.0
        
        if normalize:
            predicted = normalize_text_for_ocr(predicted)
            ground_truth = normalize_text_for_ocr(ground_truth)

        pred_words = predicted.split()
        gt_words = ground_truth.split()

        if not gt_words:
            return 0.0 if not pred_words else 1.0

        distance = levenshtein_distance(pred_words, gt_words)
        wer = distance / len(gt_words)
        return max(0.0, min(1.0, wer))

    def evaluate_page(
        self,
        predicted: str,
        ground_truth: str,
        page_num: int = 0,
        confidence: float = 0.0,
        used_fallback: bool = False,
        correlation_id: Optional[str] = None,
    ) -> OCRPageMetrics:
        """Evaluate a single page and return structured metrics."""
        # ✅ Validate inputs
        is_valid, error = _validate_page_inputs(predicted, ground_truth, correlation_id or "ocr_page")
        if not is_valid:
            logger.error(f"Invalid page inputs: {error}")
            return OCRPageMetrics(
                page_num=page_num, cer=1.0, wer=1.0, ocr_confidence=0.0,
                char_count_pred=0, char_count_gt=0, used_vision_fallback=used_fallback,
                correlation_id=correlation_id or generate_eval_correlation_id("ocr_page"),
            )
        
        cer = self.compute_cer(predicted, ground_truth)
        wer = self.compute_wer(predicted, ground_truth)
        
        return OCRPageMetrics(
            page_num=page_num,
            cer=cer,
            wer=wer,
            ocr_confidence=confidence,
            char_count_pred=len(predicted),
            char_count_gt=len(ground_truth),
            used_vision_fallback=used_fallback,
            correlation_id=correlation_id or generate_eval_correlation_id("ocr_page"),
        )

    def evaluate_document(
        self,
        source_file: str,
        predicted_pages: List[str],
        ground_truth_pages: List[str],
        confidences: Optional[List[float]] = None,
        fallback_flags: Optional[List[bool]] = None,
        correlation_id: Optional[str] = None,
    ) -> OCRDocumentMetrics:
        """Evaluate a multi-page document and return aggregated metrics."""
        corr_id = correlation_id or generate_eval_correlation_id("ocr_doc")
        
        # ✅ Validate inputs
        if not isinstance(source_file, str) or not source_file.strip():
            raise ValueError("source_file must be a non-empty string")
        if not isinstance(predicted_pages, list) or not isinstance(ground_truth_pages, list):
            raise ValueError("predicted_pages and ground_truth_pages must be lists")
        
        if len(predicted_pages) != len(ground_truth_pages):
            raise ValueError(
                f"Page count mismatch for '{source_file}': "
                f"predicted={len(predicted_pages)}, "
                f"ground_truth={len(ground_truth_pages)}."
            )
        if not predicted_pages:
            raise ValueError(f"Cannot evaluate empty document: {source_file}")

        confidences = confidences or [0.0] * len(predicted_pages)
        fallback_flags = fallback_flags or [False] * len(predicted_pages)

        doc_metrics = OCRDocumentMetrics(source_file=source_file, correlation_id=corr_id)
        for i, (pred, gt, conf, fallback) in enumerate(
            zip(predicted_pages, ground_truth_pages, confidences, fallback_flags)
        ):
            doc_metrics.pages.append(
                self.evaluate_page(pred, gt, page_num=i, confidence=conf, used_fallback=fallback, correlation_id=corr_id)
            )

        logger.info(
            f"[{corr_id}] OCR eval [{source_file}]: CER={doc_metrics.mean_cer:.4f}, "
            f"WER={doc_metrics.mean_wer:.4f}, fallback_rate={doc_metrics.vision_fallback_rate:.2%}"
        )
        return doc_metrics

    def confidence_distribution(self, confidences: List[float]) -> dict:
        """Compute statistical distribution of OCR confidence scores."""
        # ✅ FIXED: Handle empty list gracefully
        if not confidences:
            logger.warning("confidence_distribution called with empty list")
            return {
                "mean": 0.0, "median": 0.0, "std": 0.0,
                "min": 0.0, "max": 0.0,
                "pct_above_0.9": 0.0, "pct_above_0.85": 0.0, "pct_below_0.7": 0.0,
            }
            
        arr = np.array(confidences)
        return {
            "mean": round(float(arr.mean()), 4),
            "median": round(float(np.median(arr)), 4),
            "std": round(float(arr.std()), 4),
            "min": round(float(arr.min()), 4),
            "max": round(float(arr.max()), 4),
            "pct_above_0.9": round(float((arr >= 0.9).mean()), 4),
            "pct_above_0.85": round(float((arr >= 0.85).mean()), 4),
            "pct_below_0.7": round(float((arr < 0.7).mean()), 4),
        }

    def _windowed_cer(self, predicted: str, ground_truth: str, window: int) -> float:
        """Compute approximate CER using sliding windows for very long texts."""
        step = window // 2  # 50% overlap for smoother approximation
        cers = []
        
        max_len = max(len(predicted), len(ground_truth))
        for i in range(0, max_len, step):
            p_chunk = predicted[i: i + window]
            g_chunk = ground_truth[i: i + window]
            
            # ✅ FIXED: Skip empty ground truth chunks to avoid division by zero
            if not g_chunk:
                continue
                
            distance = levenshtein_distance(p_chunk, g_chunk)
            # ✅ FIXED: Safe division
            cer = distance / len(g_chunk) if len(g_chunk) > 0 else 0.0
            cers.append(cer)
        
        return float(np.mean(cers)) if cers else 0.0


def get_ocr_metrics_metadata() -> dict[str, Any]:
    """✅ NEW: Return OCR metrics metadata for monitoring."""
    return {
        "metrics": ["cer", "wer", "ocr_confidence", "vision_fallback_rate"],
        "max_chars_for_exact_cer": OCRMetricsCalculator.MAX_CHARS_FOR_EXACT,
        "windowed_cer_step_ratio": 0.5,  # 50% overlap
        "accuracy_computation": "1.0 - error_rate (clamped to [0.0, 1.0])",
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "OCRMetricsCalculator",
    "OCRPageMetrics",
    "OCRDocumentMetrics",
    "get_ocr_metrics_metadata",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

