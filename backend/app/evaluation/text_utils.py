# backend/app/evaluation/text_utils.py
# DVMELTSS-FIX: V - Validate, M - Modular, T - Time complexity
# ASCALE-FIX: S - Separation
# ✅ FIXED: None handling + regex safety + input validation

from __future__ import annotations

import re
from typing import Union, Sequence, Any

SequenceType = Union[str, Sequence[str]]  # ✅ More flexible than List[str]


def levenshtein_distance(seq1: SequenceType, seq2: SequenceType) -> int:
    """
    Levenshtein edit distance between two sequences.

    Optimizations:
    - Early exit for exact match
    - Space-optimized DP (O(min(m,n)) space)
    - Works on both str (char-level) and list[str] (word-level)

    Time: O(m*n), Space: O(min(m,n))

    Args:
        seq1: First sequence (string or list of tokens)
        seq2: Second sequence (string or list of tokens)

    Returns:
        Minimum number of single-character edits to transform seq1 into seq2

    Examples:
        >>> levenshtein_distance("kitten", "sitting")
        3
        >>> levenshtein_distance(["hello", "world"], ["hello", "there"])
        1
    """
    # ✅ FIXED: Handle None inputs
    if seq1 is None:
        return len(seq2) if seq2 is not None else 0
    if seq2 is None:
        return len(seq1)

    if not seq1:
        return len(seq2)
    if not seq2:
        return len(seq1)

    # FIXED: Early exit for exact match
    if seq1 == seq2:
        return 0

    # Ensure seq1 is the shorter sequence for space optimization
    if len(seq1) > len(seq2):
        seq1, seq2 = seq2, seq1

    m, n = len(seq1), len(seq2)

    # Initialize previous row
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if seq1[i - 1] == seq2[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,  # Insertion
                prev[j] + 1,  # Deletion
                prev[j - 1] + cost,  # Substitution
            )
        prev, curr = curr, [0] * (n + 1)

    return prev[n]


def normalize_text_for_ocr(text: str) -> str:
    """
    Normalize text before OCR metric computation (CER/WER).

    Normalization steps:
    1. Lowercase all characters
    2. Collapse multiple whitespace to single space
    3. Strip leading/trailing whitespace
    4. Remove zero-width characters that may appear in OCR output

    Args:
        text: Raw OCR output text

    Returns:
        Normalized text for comparison

    Examples:
        >>> normalize_text_for_ocr("  Hello\\u200b  World  ")
        'hello world'
    """
    # ✅ FIXED: Handle None/empty early
    if not text:
        return ""

    # ✅ FIXED: Early return for very long text to avoid regex overhead
    if len(text) > 1_000_000:  # 1MB limit
        # Just lowercase and strip for huge texts
        return text.lower().strip()

    text = text.lower()
    # Remove zero-width characters
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize_for_wer(text: str) -> list[str]:
    """
    Tokenize text for Word Error Rate computation.

    Handles:
    - Punctuation separation (e.g., "hello," -> ["hello", ","])
    - Contraction preservation (e.g., "don't" stays as one token)
    - Unicode-aware splitting

    Args:
        text: Input text to tokenize

    Returns:
        List of tokens for WER computation

    Examples:
        >>> tokenize_for_wer("Hello, world! Don't stop.")
        ['Hello', ',', 'world', '!', "Don't", 'stop', '.']
    """
    if not text:
        return []

    tokens = text.split()
    result = []

    for token in tokens:
        # Preserve contractions
        if "'" in token and len(token) > 2:
            result.append(token)
            continue
        # Pure punctuation tokens
        if re.fullmatch(r"[^\w\s]+", token, re.UNICODE):
            result.append(token)
            continue
        # ✅ FIXED: Use non-backtracking regex pattern
        # Original pattern could cause catastrophic backtracking on malformed input
        cleaned = re.findall(r"[\w']+(?:[-'][\w']+)*|[^\w\s]", token, re.UNICODE)
        result.extend([t for t in cleaned if t.strip()])

    # Fallback: if tokenization failed, return the whole text as single token
    if not result and text.strip():
        return [text.strip()]
    return result


def compute_exact_match(pred: str, gt: str, normalize: bool = True) -> bool:
    """
    Compute exact match between prediction and ground truth.

    Args:
        pred: Predicted text
        gt: Ground truth text
        normalize: Whether to normalize texts before comparison

    Returns:
        True if texts match (after optional normalization), False otherwise

    Examples:
        >>> compute_exact_match("Hello", "hello", normalize=True)
        True
        >>> compute_exact_match("Hello", "World", normalize=False)
        False
    """
    # ✅ FIXED: Handle None inputs safely
    if pred is None and gt is None:
        return True
    if pred is None or gt is None:
        return False

    if normalize:
        pred = normalize_text_for_ocr(pred)
        gt = normalize_text_for_ocr(gt)
    return pred == gt


def compute_f1_from_counts(tp: int, fp: int, fn: int) -> float:
    """
    Compute F1 score from true positive, false positive, false negative counts.

    Args:
        tp: True positives (must be non-negative)
        fp: False positives (must be non-negative)
        fn: False negatives (must be non-negative)

    Returns:
        F1 score (harmonic mean of precision and recall), 0.0 if undefined

    Examples:
        >>> compute_f1_from_counts(10, 2, 3)
        0.8333...
        >>> compute_f1_from_counts(0, 0, 0)
        0.0
    """
    # ✅ FIXED: Validate inputs are non-negative
    if tp < 0 or fp < 0 or fn < 0:
        raise ValueError("tp, fp, and fn must be non-negative integers")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    if precision + recall == 0:
        return 0.0
    return 2 * (precision * recall) / (precision + recall)


def get_text_utils_metadata() -> dict[str, Any]:
    """✅ NEW: Return text utils metadata for monitoring."""
    return {
        "functions": [
            "levenshtein_distance",
            "normalize_text_for_ocr",
            "tokenize_for_wer",
            "compute_exact_match",
            "compute_f1_from_counts",
        ],
        "max_text_length_for_normalization": 1_000_000,
        "supported_sequence_types": ["str", "list[str]", "tuple[str, ...]"],
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "levenshtein_distance",
    "normalize_text_for_ocr",
    "tokenize_for_wer",
    "compute_exact_match",
    "compute_f1_from_counts",
    "get_text_utils_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
