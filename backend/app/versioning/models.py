# backend/app/versioning/models.py
# DVMELTSS-FIX: V - Validate, M - Modular, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Final, Optional
from pydantic import BaseModel, Field, ConfigDict
# DVMELTSS-M: Import centralized utilities
from app.core.schema_utils import CorrelationIdField
# DVMELTSS-S: Valid version statuses
_VALID_STATUSES: Final = frozenset({"draft", "published", "archived", "deleted"})
@dataclass
class DiffResult:
    """
    Structured result of document diff computation.
    """
    document_id: str
    has_changes: bool
    similarity_ratio: float
    added_lines: list[str]
    removed_lines: list[str]
    modified_sections: list[dict]
    change_summary: str
    correlation_id: Optional[str] = None  # FIXED: Added for tracing
    error: Optional[str] = None
    def __post_init__(self):
        # DVMELTSS-V: Clamp similarity ratio
        if not (0.0 <= self.similarity_ratio <= 1.0):
            self.similarity_ratio = max(0.0, min(1.0, self.similarity_ratio))
        # Clamp summary length
        if len(self.change_summary) > 500:
            self.change_summary = self.change_summary[:497] + "..."
@dataclass
class VersionMetadata:
    """
    Metadata for a single document version.
    """
    version_id: str
    document_id: str
    created_at: str  # ISO 8601
    author_id: str
    change_summary: str
    status: str = "draft"
    parent_version_id: Optional[str] = None
    correlation_id: Optional[str] = None  # FIXED: Added for tracing
    def __post_init__(self):
        # DVMELTSS-V: Validate status
        if self.status not in _VALID_STATUSES:
            self.status = "draft"
    def to_dict(self) -> dict:
        """Serialize for storage/API."""
        return {
            "version_id": self.version_id,
            "document_id": self.document_id,
            "created_at": self.created_at,
            "author_id": self.author_id,
            "change_summary": self.change_summary,
            "status": self.status,
            "parent_version_id": self.parent_version_id,
            "correlation_id": self.correlation_id,
        }
class DiffResultModel(BaseModel):
    """Pydantic model for API responses."""
    document_id: str
    has_changes: bool
    similarity_ratio: float = Field(ge=0.0, le=1.0)
    added_lines: list[str]
    removed_lines: list[str]
    modified_sections: list[dict]
    change_summary: str
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing
    error: Optional[str] = None
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "doc-123",
                "has_changes": True,
                "similarity_ratio": 0.85,
                "added_lines": ["New clause added"],
                "removed_lines": [],
                "modified_sections": [],
                "change_summary": "Added new payment terms clause.",
                "correlation_id": "req-abc123",
            }
        }
    )
class VersionComparison(BaseModel):
    """Side-by-side version comparison for API."""
    version_a_id: str
    version_b_id: str
    created_at_a: str
    created_at_b: str
    author_a: str
    author_b: str
    change_summary: str
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "version_a_id": "v1-abc",
                "version_b_id": "v2-def",
                "created_at_a": "2024-01-01T10:00:00Z",
                "created_at_b": "2024-01-02T14:30:00Z",
                "author_a": "user-1",
                "author_b": "user-1",
                "change_summary": "Updated liability clause per legal review.",
                "correlation_id": "req-abc123",
            }
        }
    )
# DVMELTSS-M: Explicit module exports
__all__ = ["DiffResult", "VersionMetadata", "DiffResultModel", "VersionComparison"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

