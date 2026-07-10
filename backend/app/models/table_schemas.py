# backend/app/models/table_schemas.py

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator

# DVMELTSS-M: Import centralized utilities
from app.core.schema_utils import CorrelationIdField, sanitize_text


class TableQueryRequest(BaseModel):
    """Request to query a specific table by ID."""
    table_id: str
    question: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Natural language question about the table",
    )
    operation: str = Field(
        default="view",
        description="view | sum | max | min | filter | describe",
    )
    filter_col: Optional[str] = Field(default=None, max_length=100)
    filter_value: Optional[str] = Field(default=None, max_length=200)
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing

    @field_validator("question")
    @classmethod
    def sanitize_question(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return sanitize_text(v, max_length=1000, min_length=1)


class TableQueryResponse(BaseModel):
    table_id: str
    markdown: str
    json_data: dict[str, Any]
    summary: str
    headers: list[str]
    row_count: int
    col_count: int
    table_type: str
    answer: Optional[str] = Field(default=None, description="Result of operation query")
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class ExtractionStatsResponse(BaseModel):
    source_file: str
    table_count: int
    chart_count: int
    form_count: int
    tables: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    forms: list[dict[str, Any]] = []
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


# DVMELTSS-M: Explicit module exports
__all__ = ["TableQueryRequest", "TableQueryResponse", "ExtractionStatsResponse"]
# Local smoke test entry point. Run: python -m 

