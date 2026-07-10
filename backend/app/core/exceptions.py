"""
Base exception classes for DocuMind AI.

Features:
- Machine-readable error_code for API responses
- Optional context dict for additional debugging info
- to_api_response() method for consistent error formatting

Usage:
    from app.core.exceptions import DocuMindError, ValidationError
    raise ValidationError("Invalid input", context={"field": "email"})
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class DocuMindError(Exception):
    """
    Base exception for all DocuMind AI errors.
    """

    error_code: str = "INTERNAL_ERROR"
    default_status_code: int = 500

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        status_code: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.error_code
        self.status_code = status_code or self.__class__.default_status_code
        self.context = context or {}

    def to_api_response(self) -> Dict[str, Any]:
        """Convert exception to API error response dict."""
        return {
            "error": self.error_code,
            "message": self.message,
            "status_code": self.status_code,
            "context": self.context if self.context else None,
        }

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class RAGChainError(DocuMindError):
    """Raised when RAG chain operations fail."""

    error_code = "RAG_CHAIN_ERROR"
    default_status_code = 500


class VisionOCRError(DocuMindError):
    """Raised when GPT-4o Vision OCR fails."""

    error_code = "VISION_OCR_FAILED"
    default_status_code = 503


class VisionAnalyzerError(DocuMindError):
    """Raised when GPT-4o Vision semantic analysis fails."""

    error_code = "VISION_ANALYSIS_FAILED"
    default_status_code = 503


class VectorStoreError(DocuMindError):
    """Raised when vector store operations fail."""

    error_code = "VECTOR_STORE_ERROR"
    default_status_code = 500


class OCRPipelineError(DocuMindError):
    """Raised when the OCR pipeline fails unrecoverably."""

    error_code = "OCR_PIPELINE_FAILED"
    default_status_code = 500


class ValidationError(DocuMindError):
    """Raised when input validation fails."""

    error_code = "VALIDATION_ERROR"
    default_status_code = 422


class AuthenticationError(DocuMindError):
    """Raised when API authentication fails."""

    error_code = "AUTHENTICATION_FAILED"
    default_status_code = 401


class RateLimitError(DocuMindError):
    """Raised when API rate limit is exceeded."""

    error_code = "RATE_LIMIT_EXCEEDED"
    default_status_code = 429


class ServiceUnavailableError(DocuMindError):
    """Raised when a downstream service is temporarily unavailable."""

    error_code = "SERVICE_UNAVAILABLE"
    default_status_code = 503


class NotFoundError(DocuMindError):
    """Raised when requested resource is not found."""

    error_code = "NOT_FOUND"
    default_status_code = 404


# DVMELTSS-M: Explicit module exports
__all__ = [
    "DocuMindError",
    "RAGChainError",
    "VisionOCRError",
    "VisionAnalyzerError",
    "VectorStoreError",
    "OCRPipelineError",
    "ValidationError",
    "AuthenticationError",
    "RateLimitError",
    "ServiceUnavailableError",  # [OK] FIXED: was missing from __all__
    "NotFoundError",
]
# Local smoke test entry point. Run: python -m

