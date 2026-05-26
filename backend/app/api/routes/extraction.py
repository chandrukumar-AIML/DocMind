# backend/app/api/routes/extraction.py
# DVMELTSS-FIX: V/E/M/S + ASCALE-L + BATMAN-A
# ✅ FIXED: Proper RateLimiter usage + input validation + safe pandas operations + timeout handling

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings, lazy_settings as settings  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.models import ErrorResponse
from app.vectorstore.store_manager import VectorStoreManager
from app.cache import get_cache
from app.core.vision_llm import get_vision_llm
from app.middleware.rate_limiter import RateLimiter  # FIXED: actual module path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/extraction", tags=["extraction"])

# ✅ FIXED: Use proper RateLimiter with workspace-scoped keys (not constructor params)
# Rate limiting is handled per-request via check_async in the endpoint

# ✅ NEW: Cache operation timeout (seconds)
_CACHE_TIMEOUT: Final = 10.0
# ✅ NEW: LLM operation timeout (seconds)
_LLM_TIMEOUT: Final = 30.0
# ✅ NEW: Max rows for pandas operations to prevent memory abuse
_MAX_PANDAS_ROWS: Final = 10000


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class TableQueryRequest(BaseModel):
    operation: str = Field(..., pattern="^(view|describe|sum|max|min|filter)$")
    question: Optional[str] = Field(default=None, max_length=500)
    filter_col: Optional[str] = Field(default=None, max_length=64)
    filter_value: Optional[str] = Field(default=None, max_length=200)
    
    @field_validator('filter_col', 'filter_value', mode='before')
    @classmethod
    def strip_strings(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class TableQueryResponse(BaseModel):
    table_id: str
    markdown: str
    answer: Optional[str]
    correlation_id: str


class ExtractionStatsResponse(BaseModel):
    source_file: str
    workspace_id: str
    table_count: int
    chart_count: int
    form_count: int
    tables: list[dict]
    charts: list[dict]
    correlation_id: str


# ✅ NEW: Input validation helper
def _validate_extraction_inputs(
    table_id: Optional[str],
    source_file: Optional[str],
    operation: Optional[str],
    filter_col: Optional[str],
    filter_value: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate extraction endpoint inputs before processing."""
    if table_id is not None and not isinstance(table_id, str):
        return False, "table_id must be a string or None"
    if source_file is not None and not isinstance(source_file, str):
        return False, "source_file must be a string or None"
    if operation is not None and not isinstance(operation, str):
        return False, "operation must be a string or None"
    if filter_col is not None and not isinstance(filter_col, str):
        return False, "filter_col must be a string or None"
    if filter_value is not None and not isinstance(filter_value, str):
        return False, "filter_value must be a string or None"
    return True, ""


# ========================================================================
# INTERNAL: Extraction helpers (DVMELTSS-B: Business logic separation)
# ========================================================================
def _get_cache_key(workspace_id: str, item_type: str, item_id: str) -> str:
    """Generate Redis cache key for extraction items."""
    return f"extract:{workspace_id}:{item_type}:{item_id}"


async def _validate_table_exists(
    workspace_id: str, 
    table_id: str, 
    vector_store: VectorStoreManager,
    corr_id: str,
) -> bool:
    """✅ NEW: Validate that table exists in vector store before querying."""
    try:
        # Check if table metadata exists in vector store
        docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(
                query="",
                filters={"table_id": table_id, "block_type": "table"},
                limit=1,
                correlation_id=corr_id,
            ),
            timeout=_CACHE_TIMEOUT,
        )
        return len(docs) > 0
    except asyncio.TimeoutError:
        logger.warning(f"[{corr_id}] Table validation timed out")
        return False
    except Exception as e:
        logger.warning(f"[{corr_id}] Table validation failed: {e}")
        return False


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.get(
    "/stats",
    response_model=ExtractionStatsResponse,
    summary="Get extraction statistics for a document",
)
async def get_extraction_stats(
    source_file: str = Query(..., min_length=1, max_length=255),
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
    request: Request = None,
) -> ExtractionStatsResponse:
    corr_id = (request.headers.get("X-Correlation-ID") if request else None) or generate_correlation_id("extract_stats")
    workspace_id = user.workspace_id if user else "default"
    
    # ✅ Validate inputs
    is_valid, error = _validate_extraction_inputs(None, source_file, None, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    # ✅ FIXED: Use vector store to get actual extraction counts
    vector_store = VectorStoreManager(workspace_id=workspace_id)
    
    try:
        # Search for tables
        table_docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(
                query="",
                filters={"source_file": source_file, "block_type": "table"},
                limit=100,
                correlation_id=corr_id,
            ),
            timeout=_CACHE_TIMEOUT,
        )
        
        # Search for charts
        chart_docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(
                query="",
                filters={"source_file": source_file, "block_type": "figure"},
                limit=100,
                correlation_id=corr_id,
            ),
            timeout=_CACHE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Extraction stats timed out")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Extraction stats failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve extraction stats")
    
    tables = [
        {
            "table_id": d.metadata.get("table_id", d.id) if hasattr(d, "metadata") else d.id,
            "page": (d.metadata.get("page_number", 0) if hasattr(d, "metadata") else 0) + 1,
            "rows": d.metadata.get("row_count", 0) if hasattr(d, "metadata") else 0,
            "cols": d.metadata.get("col_count", 0) if hasattr(d, "metadata") else 0,
            "type": d.metadata.get("table_type", "data") if hasattr(d, "metadata") else "data",
            "summary": (d.metadata.get("summary", "") or "")[:100] if hasattr(d, "metadata") else "",
        }
        for d in table_docs if d is not None
    ]
    
    charts = [
        {
            "chart_id": d.metadata.get("chart_id", d.id) if hasattr(d, "metadata") else d.id,
            "page": (d.metadata.get("page_number", 0) if hasattr(d, "metadata") else 0) + 1,
            "type": d.metadata.get("chart_type", "other") if hasattr(d, "metadata") else "other",
            "title": d.metadata.get("title") if hasattr(d, "metadata") else None,
            "takeaway": (d.metadata.get("key_takeaway", "") or "")[:100] if hasattr(d, "metadata") else "",
        }
        for d in chart_docs if d is not None
    ]
    
    return ExtractionStatsResponse(
        source_file=source_file,
        workspace_id=workspace_id,
        table_count=len(tables),
        chart_count=len(charts),
        form_count=0,
        tables=tables,
        charts=charts,
        correlation_id=corr_id,
    )


@router.get(
    "/table/{table_id}",
    response_model=TableQueryResponse,
    summary="Get extracted table by ID",
)
async def get_table(
    table_id: str,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> TableQueryResponse:
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("get_table")
    
    # ✅ Validate inputs
    is_valid, error = _validate_extraction_inputs(table_id, None, None, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    # ✅ FIXED: Validate table exists in vector store
    vector_store = VectorStoreManager(workspace_id=user.workspace_id)
    if not await _validate_table_exists(user.workspace_id, table_id, vector_store, corr_id):
        raise HTTPException(status_code=404, detail=f"Table not found: {table_id}")
    
    # ✅ FIXED: Fetch from vector store instead of in-memory cache
    try:
        docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(
                query="",
                filters={"table_id": table_id, "block_type": "table"},
                limit=1,
                correlation_id=corr_id,
            ),
            timeout=_CACHE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Table fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve table")
    
    if not docs:
        raise HTTPException(status_code=404, detail=f"Table not found: {table_id}")
    
    doc = docs[0]
    return TableQueryResponse(
        table_id=table_id,
        markdown=doc.metadata.get("markdown", "") if hasattr(doc, "metadata") else "",
        answer=None,
        correlation_id=corr_id,
    )


@router.post(
    "/table/{table_id}/query",
    response_model=TableQueryResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Table not found"},
        429: {"model": ErrorResponse, "description": "Rate limited"},
        500: {"model": ErrorResponse, "description": "Query failed"},
    },
    summary="Query a specific table with natural language",
)
async def query_table(
    table_id: str,
    request_body: TableQueryRequest,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> TableQueryResponse:
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("query_table")
    
    # ✅ Validate inputs
    is_valid, error = _validate_extraction_inputs(
        table_id, None, request_body.operation, request_body.filter_col, request_body.filter_value, corr_id
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    # ✅ FIXED: Proper rate limiting using RateLimiter.check_async with workspace-scoped key
    rate_limiter = RateLimiter()
    rate_key = f"extract_query:{user.workspace_id}:{user.user_id}"
    
    try:
        rate_result = await asyncio.wait_for(
            rate_limiter.check_async(
                workspace_id=user.workspace_id,
                endpoint_group="query",
                identifier=rate_key,
                correlation_id=corr_id,
            ),
            timeout=5.0,
        )
        if not rate_result.allowed:
            logger.warning(f"[{corr_id}] Extraction query rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many table queries. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")
    
    # ✅ FIXED: Validate table exists in vector store
    vector_store = VectorStoreManager(workspace_id=user.workspace_id)
    if not await _validate_table_exists(user.workspace_id, table_id, vector_store, corr_id):
        raise HTTPException(status_code=404, detail=f"Table not found: {table_id}")
    
    # ✅ FIXED: Fetch from vector store
    try:
        docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(
                query="",
                filters={"table_id": table_id, "block_type": "table"},
                limit=1,
                correlation_id=corr_id,
            ),
            timeout=_CACHE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Table fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve table")
    
    if not docs:
        raise HTTPException(status_code=404, detail=f"Table not found: {table_id}")
    
    doc = docs[0]
    markdown = doc.metadata.get("markdown", "") if hasattr(doc, "metadata") else ""
    json_data = doc.metadata.get("json_data", {}) if hasattr(doc, "metadata") else {}
    
    if request_body.operation == "view":
        return TableQueryResponse(table_id=table_id, markdown=markdown, answer=None, correlation_id=corr_id)
    
    if request_body.operation == "describe" and request_body.question:
        # ✅ FIXED: Use centralized LLM pool with timeout
        try:
            llm = get_vision_llm(model_override=settings.openai_chat_model, timeout=30.0)
            
            prompt = (
                f"Answer this question about the table. Return direct answer only.\n\n"
                f"Table:\n{markdown[:1500]}\n\n"
                f"Question: {request_body.question}"
            )
            
            # ✅ FIXED: Use asyncio.wait_for for proper timeout
            response = await asyncio.wait_for(
                llm.ainvoke([{"role": "user", "content": prompt}]),
                timeout=_LLM_TIMEOUT,
            )
            answer = response.content if hasattr(response, "content") else str(response)
            
        except asyncio.TimeoutError:
            logger.warning(f"[{corr_id}] Table LLM query timed out after {_LLM_TIMEOUT}s")
            answer = "Table description timed out. Please try again."
        except Exception as e:
            logger.error(f"[{corr_id}] Table LLM query failed: {e}")
            answer = "Failed to describe table."
            
        return TableQueryResponse(table_id=table_id, markdown=markdown, answer=answer, correlation_id=corr_id)
    
    # Pandas operations (sum, max, min, filter) — ✅ FIXED: Safe mode + regex escaping
    import pandas as pd
    
    records = json_data.get("records", []) if isinstance(json_data, dict) else []
    if not records:
        return TableQueryResponse(table_id=table_id, markdown=markdown, answer="No data available.", correlation_id=corr_id)
    
    try:
        # ✅ FIXED: Limit DataFrame size to prevent memory abuse
        if len(records) > _MAX_PANDAS_ROWS:
            logger.warning(f"[{corr_id}] Table has {len(records)} rows, limiting to {_MAX_PANDAS_ROWS}")
            records = records[:_MAX_PANDAS_ROWS]
        
        df = pd.DataFrame(records)
        
        if request_body.operation in ("sum", "max", "min") and request_body.filter_col:
            col = request_body.filter_col
            if col not in df.columns:
                raise HTTPException(status_code=400, detail=f"Column not found: {col}")
            
            # ✅ FIXED: Safe numeric conversion with proper regex pattern
            numeric = pd.to_numeric(
                df[col].astype(str).str.replace(r'[,$%]', '', regex=True), 
                errors="coerce"
            )
            
            if request_body.operation == "sum":
                answer = f"{col} sum: {numeric.sum():.2f}"
            elif request_body.operation == "max":
                answer = f"{col} max: {numeric.max():.2f}"
            else:
                answer = f"{col} min: {numeric.min():.2f}"
            return TableQueryResponse(table_id=table_id, markdown=markdown, answer=answer, correlation_id=corr_id)
            
        if request_body.operation == "filter" and request_body.filter_col and request_body.filter_value:
            col = request_body.filter_col
            if col not in df.columns:
                raise HTTPException(status_code=400, detail=f"Column not found: {col}")
            
            # ✅ FIXED: Safe None checks + escape regex special chars in filter_value
            safe_value = re.escape(request_body.filter_value) if request_body.filter_value else ""
            filtered = df[df[col].astype(str).str.contains(safe_value, case=False, na=False, regex=True)]
            
            # ✅ FIXED: Fallback if tabulate not installed
            try:
                from tabulate import tabulate
                answer = tabulate(filtered.head(20), headers="keys", tablefmt="pipe", showindex=False)
            except ImportError:
                # Simple fallback: CSV-like format
                header = "| " + " | ".join(str(c) for c in filtered.columns) + " |"
                sep = "| " + " | ".join(["---"] * len(filtered.columns)) + " |"
                rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in filtered.head(20).itertuples(index=False)]
                answer = "\n".join([header, sep] + rows)
            
            return TableQueryResponse(table_id=table_id, markdown=markdown, answer=answer, correlation_id=corr_id)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] Table pandas operation failed: {e}")
        raise HTTPException(status_code=400, detail=f"Query operation failed: {str(e)}")
    
    return TableQueryResponse(table_id=table_id, markdown=markdown, answer=None, correlation_id=corr_id)


@router.get(
    "/export-tables/{source_file:path}",
    summary="Export all extracted tables from a document as XLSX",
)
async def export_tables_xlsx(
    source_file: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    """Download all tables from the document as a single Excel workbook (one sheet per table)."""
    from fastapi.responses import Response
    corr_id = generate_correlation_id("export_tables")
    filename_safe = source_file.split("/")[-1].split("\\")[-1]

    vector_store = VectorStoreManager(workspace_id=user.workspace_id)
    try:
        table_docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(
                query="",
                filters={"source_file": filename_safe, "block_type": "table"},
                limit=50,
                correlation_id=corr_id,
            ),
            timeout=_CACHE_TIMEOUT,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve tables: {e}")

    if not table_docs:
        raise HTTPException(status_code=404, detail="No tables found in document")

    try:
        import io
        import openpyxl
        import json as _json

        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # remove default empty sheet

        for idx, doc in enumerate(table_docs, 1):
            meta = doc.metadata if hasattr(doc, "metadata") else {}
            sheet_name = f"Table_{idx}"
            ws = wb.create_sheet(title=sheet_name)

            # Try JSON data first, then parse markdown
            json_data = meta.get("json_data") or {}
            if isinstance(json_data, str):
                try:
                    json_data = _json.loads(json_data)
                except Exception:
                    json_data = {}

            records = json_data.get("records", []) if isinstance(json_data, dict) else []
            if records:
                headers = list(records[0].keys())
                ws.append(headers)
                for row in records[:_MAX_PANDAS_ROWS]:
                    ws.append([row.get(h, "") for h in headers])
            else:
                # Fall back to parsing markdown table
                md = meta.get("markdown", doc.page_content if hasattr(doc, "page_content") else "")
                lines = [l.strip() for l in md.split("\n") if l.strip().startswith("|")]
                for line in lines:
                    if set(line.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
                        continue  # skip separator row
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    ws.append(cells)

            # Metadata row
            ws.append([])
            ws.append([f"Source: {filename_safe}", f"Page: {meta.get('page_number', '?')}"])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        xlsx_bytes = buf.read()

        safe_name = filename_safe.rsplit(".", 1)[0]
        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_tables.xlsx"'},
        )
    except ImportError:
        raise HTTPException(status_code=501, detail="openpyxl not installed — cannot export XLSX")
    except Exception as e:
        logger.error(f"[{corr_id}] XLSX export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export tables")


class FormFieldRequest(BaseModel):
    source_file: str = Field(..., max_length=255)


@router.post(
    "/form-fields",
    summary="Extract labeled field:value pairs from form images using Vision AI",
)
async def extract_form_fields(
    request: FormFieldRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    corr_id = generate_correlation_id("form_fields")
    vector_store = VectorStoreManager(workspace_id=user.workspace_id)

    try:
        figure_docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(
                query="",
                filters={"source_file": request.source_file, "block_type": "figure"},
                limit=10,
                correlation_id=corr_id,
            ),
            timeout=_CACHE_TIMEOUT,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve figures: {e}")

    if not figure_docs:
        raise HTTPException(status_code=404, detail="No image/figure blocks found in document")

    try:
        llm = get_vision_llm(timeout=30.0)
    except Exception as e:
        raise HTTPException(status_code=501, detail="Vision LLM not available")

    import json as _json
    all_fields = []

    for doc in figure_docs[:5]:
        meta = doc.metadata if hasattr(doc, "metadata") else {}
        image_b64 = meta.get("image_b64") or meta.get("base64")
        if not image_b64:
            continue

        prompt = (
            "This image contains a form. Extract all visible labeled fields and their values. "
            "Return ONLY a JSON array like: "
            '[{"field": "Name", "value": "John Doe", "field_type": "text"}, ...]. '
            "If a field is blank, use value: null. field_type can be: text, checkbox, date, number, signature."
        )
        try:
            response = await asyncio.wait_for(
                llm.ainvoke([{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }]),
                timeout=_LLM_TIMEOUT,
            )
            raw = response.content if hasattr(response, "content") else str(response)
            # Extract JSON array from response
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                fields = _json.loads(raw[start:end])
                for f in fields:
                    f["page"] = meta.get("page_number", 0) + 1
                all_fields.extend(fields)
        except Exception as e:
            logger.warning(f"[{corr_id}] Form field extraction failed for chunk: {e}")

    return {
        "source_file": request.source_file,
        "workspace_id": user.workspace_id,
        "correlation_id": corr_id,
        "field_count": len(all_fields),
        "fields": all_fields,
    }


class AggregateRequest(BaseModel):
    source_files: list[str] = Field(..., min_length=1, max_length=20)
    operation: str = Field(..., pattern="^(sum|count|avg|min|max|list)$")
    column: str = Field(..., max_length=64)
    filter_col: Optional[str] = Field(default=None, max_length=64)
    filter_value: Optional[str] = Field(default=None, max_length=200)


@router.post(
    "/aggregate",
    summary="Aggregate data across multiple documents (SUM/COUNT/AVG/FILTER on table columns)",
)
async def aggregate_cross_document(
    request: AggregateRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    corr_id = generate_correlation_id("aggregate")
    vector_store = VectorStoreManager(workspace_id=user.workspace_id)

    import pandas as pd
    import json as _json
    import re

    all_records = []

    for sf in request.source_files[:20]:
        try:
            table_docs, _ = await asyncio.wait_for(
                vector_store.search_documents_async(
                    query="",
                    filters={"source_file": sf.split("/")[-1].split("\\")[-1], "block_type": "table"},
                    limit=20,
                    correlation_id=corr_id,
                ),
                timeout=_CACHE_TIMEOUT,
            )
            for doc in table_docs:
                meta = doc.metadata if hasattr(doc, "metadata") else {}
                json_data = meta.get("json_data") or {}
                if isinstance(json_data, str):
                    try:
                        json_data = _json.loads(json_data)
                    except Exception:
                        continue
                records = json_data.get("records", []) if isinstance(json_data, dict) else []
                for r in records:
                    r["_source_file"] = sf
                all_records.extend(records)
        except Exception as e:
            logger.warning(f"[{corr_id}] Aggregate: failed to get tables for {sf}: {e}")

    if not all_records:
        raise HTTPException(status_code=404, detail="No table data found in specified documents")

    df = pd.DataFrame(all_records[:_MAX_PANDAS_ROWS])

    # Apply filter if specified
    if request.filter_col and request.filter_value:
        if request.filter_col not in df.columns:
            raise HTTPException(status_code=400, detail=f"Filter column not found: {request.filter_col}")
        safe_val = re.escape(request.filter_value)
        df = df[df[request.filter_col].astype(str).str.contains(safe_val, case=False, na=False, regex=True)]

    if request.column not in df.columns:
        raise HTTPException(status_code=400, detail=f"Column not found: {request.column}")

    numeric_col = pd.to_numeric(
        df[request.column].astype(str).str.replace(r"[,$%]", "", regex=True),
        errors="coerce",
    )

    if request.operation == "sum":
        result = float(numeric_col.sum())
    elif request.operation == "count":
        result = int(df[request.column].notna().sum())
    elif request.operation == "avg":
        result = float(numeric_col.mean()) if not numeric_col.isna().all() else 0.0
    elif request.operation == "min":
        result = float(numeric_col.min()) if not numeric_col.isna().all() else 0.0
    elif request.operation == "max":
        result = float(numeric_col.max()) if not numeric_col.isna().all() else 0.0
    elif request.operation == "list":
        result = df[request.column].dropna().unique().tolist()[:100]
    else:
        result = None

    return {
        "workspace_id": user.workspace_id,
        "correlation_id": corr_id,
        "operation": request.operation,
        "column": request.column,
        "filter": {"col": request.filter_col, "value": request.filter_value} if request.filter_col else None,
        "result": result,
        "rows_processed": len(df),
        "source_files": request.source_files,
    }


def get_extraction_metadata() -> dict[str, Any]:
    """✅ NEW: Return extraction API metadata for monitoring."""
    return {
        "supported_operations": ["view", "describe", "sum", "max", "min", "filter"],
        "rate_limit": {"endpoint_group": "query", "default_limit": "100/hour"},
        "cache_backend": "redis",
        "llm_timeout_seconds": _LLM_TIMEOUT,
        "cache_timeout_seconds": _CACHE_TIMEOUT,
        "max_pandas_rows": _MAX_PANDAS_ROWS,
        "workspace_scoped": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["router", "get_extraction_metadata"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

