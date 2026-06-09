# backend/app/ingest/xlsx_extractor.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, A - Async
# BATMAN-FIX: A - True async, M - Memory safety, T - Batch processing
# OWASP-FIX: 7 - PII redaction, 9 - File handling, 1 - Formula safety
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Optional
from langchain_core.documents import Document

# DVMELTSS-M: Import centralized utilities
from app.core.ingest_utils import (
    redact_pii,
    neutralize_formula,
    generate_ingest_correlation_id,
)

logger = logging.getLogger(__name__)
_MAX_ROWS_PER_SHEET: Final = 10000
_MAX_COLS_PER_SHEET: Final = 100
_MAX_CELLS_PER_SHEET: Final = _MAX_ROWS_PER_SHEET * _MAX_COLS_PER_SHEET
_MAX_SHEETS_PER_FILE: Final = 20
_DEFAULT_CHUNK_SIZE: Final = 1024


@dataclass(frozen=True)
class XlsxContent:
    """Immutable extracted content from an .xlsx file."""

    source_file: str
    sheets: list[dict]
    error: Optional[str] = None
    correlation_id: Optional[str] = None  # FIXED: Added for tracing

    @property
    def sheet_count(self) -> int:
        return len(self.sheets)

    def to_dict(self) -> dict:
        """Serialize for API responses / logging (with PII redaction)."""
        safe_sheets = []
        for sheet in self.sheets:
            safe_sheet = {
                "name": sheet["name"],
                "row_count": sheet["row_count"],
                "col_count": sheet["col_count"],
                "headers": [redact_pii(h) for h in sheet.get("headers", [])],  # FIXED: Centralized redactor
            }
            safe_sheets.append(safe_sheet)
        return {
            "source_file": self.source_file,
            "sheet_count": self.sheet_count,
            "sheets": safe_sheets,
            "error": self.error,
            "correlation_id": self.correlation_id,  # FIXED: Include
        }


class XlsxExtractor:
    """Extracts and structures Excel spreadsheet content."""

    def __init__(self, chunk_size: int = _DEFAULT_CHUNK_SIZE):
        self.chunk_size = chunk_size
        logger.info(f"XlsxExtractor initialized: chunk_size={chunk_size}")

    async def extract_async(self, file_path: str | Path, correlation_id: Optional[str] = None) -> XlsxContent:
        """Async version: Extract all sheets from an .xlsx file."""
        corr_id = correlation_id or generate_ingest_correlation_id("xlsx")
        file_path = Path(file_path)
        if not file_path.exists():
            return XlsxContent(
                source_file=str(file_path),
                sheets=[],
                error=f"File not found: {file_path}",
                correlation_id=corr_id,
            )
        try:
            import pandas as pd
        except ImportError:
            return XlsxContent(
                source_file=str(file_path),
                sheets=[],
                error="pandas not installed",
                correlation_id=corr_id,
            )
        try:
            loop = asyncio.get_running_loop()
            excel = await loop.run_in_executor(None, lambda: pd.ExcelFile(str(file_path)))
        except Exception as e:
            return XlsxContent(
                source_file=str(file_path),
                sheets=[],
                error=f"Failed to open xlsx: {type(e).__name__}",
                correlation_id=corr_id,
            )
        sheets = []
        sheet_names = excel.sheet_names[:_MAX_SHEETS_PER_FILE]
        for sheet_name in sheet_names:
            try:
                df = await loop.run_in_executor(
                    None,
                    lambda: pd.read_excel(excel, sheet_name=sheet_name, nrows=_MAX_ROWS_PER_SHEET),
                )
                if df.empty:
                    continue
                df = df.dropna(how="all").dropna(axis=1, how="all")
                if len(df.columns) > _MAX_COLS_PER_SHEET:
                    df = df.iloc[:, :_MAX_COLS_PER_SHEET]
                    logger.warning(f"[{corr_id}] Sheet '{sheet_name}' truncated to {_MAX_COLS_PER_SHEET} columns")
                df.columns = [
                    str(c).strip() if not str(c).startswith("Unnamed") else f"Col_{i}" for i, c in enumerate(df.columns)
                ]
                # OWASP-1: Neutralize dangerous formulas
                df = (
                    df.map(neutralize_formula) if hasattr(df, "map") else df.applymap(neutralize_formula)
                )  # FIXED: Centralized
                sheets.append(
                    {
                        "name": sheet_name,
                        "dataframe": df,
                        "row_count": len(df),
                        "col_count": len(df.columns),
                        "headers": list(df.columns),
                    }
                )
                logger.info(f"[{corr_id}] Sheet '{sheet_name}': {len(df)} rows × {len(df.columns)} cols")
            except Exception as e:
                logger.warning(f"[{corr_id}] Sheet '{sheet_name}' failed: {type(e).__name__}")
                continue
        return XlsxContent(source_file=str(file_path), sheets=sheets, correlation_id=corr_id)

    def to_langchain_documents(
        self,
        content: XlsxContent,
        use_gpt4_summary: bool = True,
        correlation_id: Optional[str] = None,
    ) -> list[Document]:
        """Convert each Excel sheet to a LangChain Document via TableExtractor."""
        import uuid
        from datetime import datetime, timezone

        if not content.sheets:
            return []
        docs = []
        now = datetime.now(timezone.utc).isoformat()
        corr_id = correlation_id or content.correlation_id or generate_ingest_correlation_id("xlsx_chunks")  # FIXED
        for sheet in content.sheets:
            df = sheet["dataframe"]
            sheet_name = sheet["name"]
            try:
                from app.extraction.table_extractor import TableExtractor

                extractor = TableExtractor()
                html = df.to_html(index=False, border=0)
                table_result = extractor.extract_from_html(
                    html=html,
                    table_id=f"xlsx_{Path(content.source_file).stem}_{sheet_name}",
                    source_file=content.source_file,
                    page_number=0,
                )
                embed_text = (
                    table_result.to_embed_text()
                    if table_result
                    else (f"Sheet: {sheet_name}\n\n" + df.to_markdown(index=False))
                )
            except Exception:
                embed_text = f"Sheet: {sheet_name}\n" + df.to_string(index=False)
            docs.append(
                Document(
                    page_content=embed_text,
                    metadata={
                        "source_file": content.source_file,
                        "page_number": 0,
                        "chunk_id": str(uuid.uuid4()),
                        "parent_id": "",
                        "block_type": "table",
                        "language": "en",
                        "ocr_confidence": 1.0,
                        "chunk_type": "child",
                        "ingest_timestamp": now,
                        "document_type": "xlsx",
                        "char_count": len(embed_text),
                        "xlsx_sheet_name": sheet_name,
                        "row_count": sheet["row_count"],
                        "col_count": sheet["col_count"],
                        "correlation_id": corr_id,  # FIXED: Propagate
                    },
                )
            )
        return docs

    def extract(self, file_path: str | Path, correlation_id: Optional[str] = None) -> XlsxContent:
        """Sync wrapper — prefers async version in new code."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(self.extract_async(file_path, correlation_id), loop).result()
        except RuntimeError:
            return asyncio.run(self.extract_async(file_path, correlation_id))


# DVMELTSS-M: Explicit module exports
__all__ = ["XlsxExtractor", "XlsxContent"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
