# backend/app/models/document_schemas.py
"""Document-related Pydantic models for FastAPI validation."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


class DocumentMetadata(BaseModel):
    """Document metadata returned in API responses."""
    id: str = Field(..., description="Document unique ID")
    filename: str = Field(..., description="Original filename")
    source_file: str = Field(..., description="Storage path/key")
    file_size: int = Field(..., description="File size in bytes")
    mime_type: str = Field(..., description="MIME type")
    
    # Processing status
    status: str = Field(default="processing", description="processing|completed|failed")
    page_count: Optional[int] = Field(None, description="Number of pages (PDF/images)")
    
    # OCR/Extraction
    detected_language: Optional[str] = Field(None, description="Detected language code")
    ocr_confidence: Optional[float] = Field(None, description="OCR confidence score 0-1")
    
    # Indexing
    chunk_count: Optional[int] = Field(None, description="Number of vector chunks")
    vector_count: Optional[int] = Field(None, description="Number of embeddings")
    
    # Context
    workspace_id: str = Field(..., description="Workspace ID")
    uploaded_by: Optional[str] = Field(None, description="User ID who uploaded")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(None)
    processed_at: Optional[datetime] = Field(None)
    
    # Tags/metadata
    tags: Optional[List[str]] = Field(default_factory=list)
    custom_metadata: Optional[dict] = Field(default_factory=dict)
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "doc_abc123",
                "filename": "report.pdf",
                "source_file": "s3://bucket/docs/report.pdf",
                "file_size": 2048576,
                "mime_type": "application/pdf",
                "status": "completed",
                "page_count": 10,
                "detected_language": "en",
                "ocr_confidence": 0.95,
                "chunk_count": 45,
                "vector_count": 45,
                "workspace_id": "ws_default",
                "uploaded_by": "usr_xyz789",
                "created_at": "2026-04-28T12:00:00Z",
                "tags": ["report", "q1"],
            }
        }


class PaginationParams(BaseModel):
    """Standard pagination parameters for list endpoints."""
    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page")
    sort_by: Optional[str] = Field(default="created_at", description="Field to sort by")
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$", description="Sort direction")
    
    @property
    def offset(self) -> int:
        """Calculate SQL OFFSET from page params."""
        return (self.page - 1) * self.page_size
    
    @property
    def limit(self) -> int:
        """Return page size as SQL LIMIT."""
        return self.page_size
    
    class Config:
        json_schema_extra = {
            "example": {
                "page": 1,
                "page_size": 20,
                "sort_by": "created_at",
                "sort_order": "desc"
            }
        }


class DocumentUpdateRequest(BaseModel):
    """Request schema for updating document metadata."""
    tags: Optional[List[str]] = Field(None, description="Replace existing tags")
    custom_metadata: Optional[dict] = Field(None, description="Merge with existing metadata")
    title: Optional[str] = Field(None, max_length=500, description="Optional document title")
    
    class Config:
        json_schema_extra = {
            "example": {
                "tags": ["updated", "reviewed"],
                "custom_metadata": {"department": "engineering"},
                "title": "Q1 Financial Report"
            }
        }

# -- Module Exports ---------------------------------------------------------
__all__ = [
    "DocumentMetadata",
    "PaginationParams", 
    "DocumentUpdateRequest",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

