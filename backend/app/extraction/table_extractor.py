
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from io import StringIO
from typing import Final, Optional, Any

import pandas as pd
import numpy as np
from pydantic import BaseModel, ValidationError, Field

# DVMELTSS-M: Import centralized utilities
from app.core.vision_llm import get_vision_llm
from app.core.retry import retry_async, RetryConfig
from app.core.prompts import escape_prompt_content
from app.core.openai_errors import classify_openai_error

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-M) -------------------------
# ========================================================================

_VALID_TABLE_TYPES: Final = frozenset({"financial", "schedule", "comparison", "data", "form", "log", "other"})

_MAX_RETRIES: Final = 3
_RETRY_BASE_DELAY: Final = 1.0
_RETRY_MAX_DELAY: Final = 30.0

_CURRENCY_RE: Final = re.compile(r"[$€£¥₹,\s]")
_PERCENT_RE: Final = re.compile(r"%\s*$")

_MAX_COLUMNS: Final = 200  # Truncate wide tables to prevent OOM
_MAX_ROWS_FOR_SUMMARY: Final = 50  # Limit rows sent to LLM for summary
_MAX_HTML_PREVIEW: Final = 2000  # Keep small HTML preview


# DVMELTSS-V: Pydantic schemas for LLM summary generation
class TableSummarySchema(BaseModel):
    summary: str = Field(..., max_length=300)
    table_type: str = Field(..., pattern=f"^({'|'.join(_VALID_TABLE_TYPES)})$")
    key_insights: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}  # ✅ FIXED: Pydantic v2 config


# ========================================================================
# -- IMMUTABLE DATA MODEL (DVMELTSS-M, V) -------------------------------
# ========================================================================


@dataclass
class ExtractedTable:
    """
    Structured representation of a table.
    ✅ FIXED: Proper field defaults + validation in __post_init__.
    """

    table_id: str
    source_file: str
    page_number: int
    chunk_id: str

    # Representations
    html: str = ""
    markdown: str = ""
    json_data: dict = field(default_factory=dict)
    summary: str = ""

    # Schema info
    headers: list[str] = field(default_factory=list)
    row_count: int = 0
    col_count: int = 0
    table_type: str = "data"
    correlation_id: Optional[str] = None

    def __post_init__(self):
        # ✅ Validate table_type against allowed values
        if self.table_type not in _VALID_TABLE_TYPES:
            object.__setattr__(self, "table_type", "data")
        # ✅ Clamp counts to non-negative
        if self.row_count < 0:
            object.__setattr__(self, "row_count", 0)
        if self.col_count < 0:
            object.__setattr__(self, "col_count", 0)

    def to_embed_text(self) -> str:
        """Text to embed in vector store — combines summary + markdown."""
        return f"{self.summary}\n\n{self.markdown}"

    def to_metadata(self) -> dict[str, Any]:
        """Metadata dict for ChromaDB storage."""
        return {
            "table_id": self.table_id,
            "block_type": "table",
            "row_count": self.row_count,
            "col_count": self.col_count,
            "table_type": self.table_type,
            "has_json": True,
            "headers": ",".join(self.headers[:10]),
            "correlation_id": self.correlation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """✅ NEW: Convert to dict for API serialization."""
        return {
            "table_id": self.table_id,
            "source_file": self.source_file,
            "page_number": self.page_number,
            "chunk_id": self.chunk_id,
            "html_preview": self.html[:_MAX_HTML_PREVIEW],
            "markdown": self.markdown,
            "json_data": self.json_data,
            "summary": self.summary,
            "headers": self.headers,
            "row_count": self.row_count,
            "col_count": self.col_count,
            "table_type": self.table_type,
            "correlation_id": self.correlation_id,
        }


# ========================================================================
# -- EXTRACTOR CLASS (DVMELTSS-V, BATMAN-A, OWASP-1) -------------------
# ========================================================================


class TableExtractor:
    """
    Extracts and structures tables from PP-StructureV3 HTML output or raw text.

    Features:
    - Centralized vision LLM client via app.core.vision_llm
    - Pandas-based normalization with dtype control
    - Centralized retry decorator for semantic summaries
    - Safe handling of multi-level headers and merged cells
    - Async-safe interface for FastAPI integration
    """

    def __init__(self, model: str = "gpt-4o", max_retries: int = _MAX_RETRIES):
        self.client = get_vision_llm(model_override=model, timeout=30.0)
        self.model = model
        self.max_retries = max_retries

        logger.info(f"TableExtractor initialized: model={model}, async=True")

    def _validate_table_input(self, html: str, text: str, corr_id: str) -> tuple[bool, str]:
        """Validate table input before processing."""
        if not html and not text:
            return False, "Both html and text are empty"
        if html and len(html) > 10_000_000:  # 10MB limit
            logger.warning(f"[{corr_id}] HTML too large ({len(html)} chars) — truncating")
        if text and len(text) > 1_000_000:  # 1MB limit
            logger.warning(f"[{corr_id}] Text too large ({len(text)} chars) — truncating")
        return True, ""

    @retry_async(
        config=RetryConfig(
            max_attempts=_MAX_RETRIES,
            backoff_base=_RETRY_BASE_DELAY,
            backoff_max=_RETRY_MAX_DELAY,
            exceptions=(Exception,),
        )
    )
    async def _call_vision_api(self, prompt: str, corr_id: str):
        """Call vision LLM with retry logic."""
        if sys.version_info >= (3, 9):
            return await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=400,
                response_format={"type": "json_object"},
                extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
            )
        else:
            # Python 3.8 fallback
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
            return await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=400,
                    response_format={"type": "json_object"},
                    extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
                ),
            )

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    async def _call_llm_with_retry(self, prompt: str, correlation_id: str) -> Optional[dict]:
        """DVMELTSS-E: Async LLM call for table summary with centralized retry."""
        corr_id = correlation_id

        safe_prompt = escape_prompt_content(prompt)

        if self._estimate_tokens(safe_prompt) > 4000:
            safe_prompt = safe_prompt[:16000]

        try:
            response = await self._call_vision_api(safe_prompt, corr_id)
            content = response.choices[0].message.content
            if not content:
                return None

            data = json.loads(content)
            TableSummarySchema.model_validate(data)
            return data

        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning(f"[{corr_id}] Table summary JSON error: {e}")
            return None
        except Exception as e:
            err = classify_openai_error(e)
            if err and err.error_type == "quota":
                return None
            logger.warning(f"[{corr_id}] Table summary unexpected error: {type(e).__name__}: {e}")
            return None

    def _html_to_dataframe(self, html: str) -> Optional[pd.DataFrame]:
        """Parse HTML table string to pandas DataFrame with dtype control."""
        try:
            tables = pd.read_html(StringIO(html))
            if not tables:
                return None
            df = tables[0]
            # Flatten multi-level columns
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [" ".join(str(c) for c in col if str(c) != "Unnamed").strip() for col in df.columns]
            return df
        except Exception as e:
            logger.debug(f"HTML parse failed: {e}")
            return None

    def _text_to_dataframe(self, text: str) -> Optional[pd.DataFrame]:
        """Parse pipe-separated text to DataFrame."""
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        if len(lines) < 2:
            return None

        rows = []
        for line in lines:
            if "|" in line:
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if cells:
                    rows.append(cells)

        if len(rows) < 2:
            return None

        try:
            headers = rows[0]
            data = [r + [""] * (len(headers) - len(r)) for r in rows[1:]]
            return pd.DataFrame(data, columns=headers)
        except Exception as e:
            logger.debug(f"Text table parse failed: {e}")
            return None

    @staticmethod
    def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean DataFrame: strip strings, rename unnamed columns, drop empty rows.
        ✅ FIXED: Add dtype control + memory guard for wide tables.
        """
        # ✅ Memory guard: truncate wide tables
        if len(df.columns) > _MAX_COLUMNS:
            logger.debug(f"Truncating wide table from {len(df.columns)} to {_MAX_COLUMNS} columns")
            df = df.iloc[:, :_MAX_COLUMNS]

        # Rename unnamed columns
        df.columns = [f"Col_{i}" if "Unnamed" in str(c) else str(c).strip() for i, c in enumerate(df.columns)]
        # Drop empty
        df = df.dropna(how="all").dropna(axis=1, how="all")
        # Strip strings and control dtypes
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.strip().replace("nan", "")
            # Convert numeric columns to appropriate dtype to save memory
            elif df[col].dtype in [np.float64, np.int64]:
                try:
                    df[col] = pd.to_numeric(df[col], downcast="integer")
                except (ValueError, TypeError):
                    try:
                        df[col] = pd.to_numeric(df[col], downcast="float")
                    except (ValueError, TypeError):
                        pass  # Keep as is
        return df.reset_index(drop=True)

    @staticmethod
    def _to_markdown(df: pd.DataFrame) -> str:
        """Convert DataFrame to Markdown with fallback if tabulate not installed."""
        try:
            from tabulate import tabulate

            return tabulate(df, headers="keys", tablefmt="pipe", showindex=False)
        except ImportError:
            # ✅ Fallback: simple pipe-separated markdown
            if df.empty:
                return ""
            header = "| " + " | ".join(str(c) for c in df.columns) + " |"
            sep = "| " + " | ".join("---" for _ in df.columns) + " |"
            rows = []
            for row in df.itertuples(index=False):
                cells = [str(v).replace("|", "\\|") for v in row]  # Escape pipes
                rows.append("| " + " | ".join(cells) + " |")
            return "\n".join([header, sep] + rows)

    @staticmethod
    def _to_json(df: pd.DataFrame) -> dict[str, Any]:
        """Convert DataFrame to nested JSON."""
        return {
            "columns": df.to_dict(orient="list"),
            "records": df.to_dict(orient="records")[:100],
            "row_count": len(df),
            "col_count": len(df.columns),
        }

    async def _generate_summary_async(self, markdown: str, source_file: str, corr_id: str) -> tuple[str, str]:
        """Generate semantic summary via LLM with centralized retry."""
        safe_markdown = escape_prompt_content(markdown[:1500])
        safe_source = escape_prompt_content(source_file)

        # Limit markdown to first _MAX_ROWS_FOR_SUMMARY rows for LLM
        lines = markdown.split("\n")
        if len(lines) > _MAX_ROWS_FOR_SUMMARY + 2:  # +2 for header + separator
            truncated = "\n".join(lines[: _MAX_ROWS_FOR_SUMMARY + 2]) + "\n... (truncated)"
            safe_markdown = escape_prompt_content(truncated)

        prompt = f"""Analyze this table and return JSON:
{{
  "summary": "2-3 sentence description of what this table shows",
  "table_type": "financial|schedule|comparison|data|form|other",
  "key_insights": ["insight 1", "insight 2"]
}}

Table:
{safe_markdown}

Document: {safe_source}
"""
        try:
            data = await self._call_llm_with_retry(prompt, corr_id)
            if data:
                summary = data.get("summary", "Table from document.")
                insights = data.get("key_insights", [])
                if insights:
                    summary += " Key insights: " + "; ".join(insights[:3])
                return summary, data.get("table_type", "data")
        except Exception:
            pass
        return f"Table extracted from {source_file}.", "data"

    async def extract_async(
        self,
        html: str,
        text: str,
        table_id: str,
        source_file: str,
        page_number: int,
        chunk_id: str = "",
        correlation_id: Optional[str] = None,
    ) -> Optional[ExtractedTable]:
        """
        Async entry point: Extract table from HTML or Text.
        ✅ FIXED: Input validation + safe error handling.
        """
        corr_id = correlation_id or "table_unknown"

        # ✅ Validate inputs first
        is_valid, error = self._validate_table_input(html, text, corr_id)
        if not is_valid:
            logger.warning(f"[{corr_id}] Invalid table input: {error}")
            return None

        try:
            df = self._html_to_dataframe(html) if html else self._text_to_dataframe(text)
            if df is None or df.empty:
                return None

            df = self._normalize_dataframe(df)
            markdown = self._to_markdown(df)
            json_data = self._to_json(df)
            headers = list(df.columns)

            # LLM Summary with correlation_id propagation
            summary, table_type = await self._generate_summary_async(markdown, source_file, corr_id)

            return ExtractedTable(
                table_id=table_id,
                source_file=source_file,
                page_number=page_number,
                chunk_id=chunk_id,
                html=html[:_MAX_HTML_PREVIEW],  # Keep small preview
                markdown=markdown,
                json_data=json_data,
                summary=summary,
                headers=headers,
                row_count=len(df),
                col_count=len(df.columns),
                table_type=table_type,
                correlation_id=corr_id,
            )

        except Exception as e:
            logger.warning(f"[{corr_id}] Table extraction failed: {type(e).__name__}: {e}")
            return None

    def extract(
        self,
        html: str,
        text: str,
        table_id: str,
        source_file: str,
        page_number: int,
        chunk_id: str = "",
        correlation_id: Optional[str] = None,
    ) -> Optional[ExtractedTable]:
        """
        Sync wrapper — use extract_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return None
            logger.warning(
                "⚠️ TableExtractor.extract() called from async context — " "use extract_async() instead. Returning None."
            )
            return None
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(
                self.extract_async(
                    html,
                    text,
                    table_id,
                    source_file,
                    page_number,
                    chunk_id,
                    correlation_id,
                )
            )


# DVMELTSS-M: Explicit module exports
__all__ = ["TableExtractor", "ExtractedTable"]
# Local smoke test entry point. Run: python -m

