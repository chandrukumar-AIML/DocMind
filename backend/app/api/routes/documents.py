
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Optional, Any, Final

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
    Query,
    Path as FastAPIPath,
    BackgroundTasks,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.config import (
    lazy_settings as settings,
)  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import (
    get_current_user,
    require_editor,
    require_admin,
    AuthenticatedUser,
)
from app.models import (
    DocumentListResponse,
    DocumentMetaResponse,
    ErrorResponse,
    PaginationParams,
)
from app.vectorstore.store_manager import VectorStoreManager
from app.workspace.store_manager import WorkspaceManager
from app.monitoring.metrics_collector import record_document_operation
from app.middleware.rate_limiter import RateLimiter  # FIXED: actual module path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# Rate limiting is handled per-request via check_async in the endpoint

_CACHE_TIMEOUT: Final = 10.0


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class DocumentQueryParams(BaseModel):
    workspace_id: Optional[str] = Field(default=None, max_length=64)
    source_file: Optional[str] = Field(default=None, max_length=255)
    page_min: Optional[int] = Field(default=None, ge=1)
    page_max: Optional[int] = Field(default=None, ge=1)
    language: Optional[str] = Field(default=None, pattern="^[a-z]{2,3}$")
    chunk_type: Optional[str] = Field(default=None)
    search: Optional[str] = Field(default=None, max_length=200)

    @field_validator("page_max")
    @classmethod
    def validate_page_range(cls, v: Optional[int], info) -> Optional[int]:
        page_min = info.data.get("page_min")
        if v is not None and page_min is not None and v < page_min:
            raise ValueError("page_max must be >= page_min")
        return v


def _validate_document_inputs(
    document_id: Optional[str],
    source_file: Optional[str],
    workspace_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate document endpoint inputs before processing."""
    if document_id is not None and not isinstance(document_id, str):
        return False, "document_id must be a string or None"
    if source_file is not None and not isinstance(source_file, str):
        return False, "source_file must be a string or None"
    if workspace_id is not None and not isinstance(workspace_id, str):
        return False, "workspace_id must be a string or None"
    return True, ""


# ========================================================================
# INTERNAL: Document operations (DVMELTSS-B: Business logic separation)
# ========================================================================
async def _list_documents(
    workspace_id: str,
    params: DocumentQueryParams,
    pagination: PaginationParams,
    correlation_id: str,
) -> DocumentListResponse:
    """List documents with filtering and pagination."""
    try:
        vector_store = VectorStoreManager(workspace_id=workspace_id)

        filters = {k: v for k, v in params.model_dump().items() if v is not None and k != "search"}

        docs, total = await asyncio.wait_for(
            vector_store.search_documents_async(
                query=params.search or "",
                filters=filters,
                limit=min(pagination.limit, 100),  # ✅ Cap limit to prevent abuse
                offset=pagination.offset,
                correlation_id=correlation_id,
            ),
            timeout=30.0,  # Add timeout for search operation
        )

        items = []
        for d in docs:
            if d is None:
                continue

            metadata = getattr(d, "metadata", {}) or {}
            items.append(
                DocumentMetaResponse(
                    source_file=metadata.get("source_file", "unknown"),
                    document_type=metadata.get("document_type", metadata.get("file_type", "unknown")),
                    language=metadata.get("language", "en"),
                    page_count=max(
                        int(metadata.get("page_count") or metadata.get("page_number") or 0),
                        0,
                    ),
                    chunk_count=max(int(metadata.get("chunk_count") or 1), 0),
                    mean_ocr_confidence=float(
                        metadata.get("mean_ocr_confidence") or metadata.get("ocr_confidence") or 0.0
                    ),
                    ingest_timestamp=str(metadata.get("ingest_timestamp") or metadata.get("created_at") or ""),
                    tags=metadata.get("tags") or [],
                    correlation_id=correlation_id,
                )
            )

        return DocumentListResponse(
            documents=items,
            total_count=total,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{correlation_id}] Document list timed out")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{correlation_id}] List failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve documents: {type(e).__name__}: {e}")


async def _delete_document(
    document_id: str,
    workspace_id: str,
    correlation_id: str,
) -> bool:
    """Delete document and all associated vectors."""
    try:
        vector_store = VectorStoreManager(workspace_id=workspace_id)
        # VectorStoreManager.delete_by_metadata_async() is the correct method for source_file deletion
        result = await asyncio.wait_for(
            vector_store.delete_by_metadata_async(
                {"source_file": document_id},
                correlation_id=correlation_id,
            ),
            timeout=30.0,
        )
        return result.get("deleted_count", 0) > 0
    except asyncio.TimeoutError:
        logger.error(f"[{correlation_id}] Document delete timed out")
        raise
    except Exception as e:
        logger.error(f"[{correlation_id}] Delete failed: {e}", exc_info=True)
        raise


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.get(
    "/workspaces",
    response_model=list[dict],
    summary="List accessible workspaces",
    description="Return workspaces the user has access to (for multi-tenant UI).",
)
async def list_document_workspaces_static(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    # captured as a document_id by FastAPI route ordering.
    return [
        {
            "workspace_id": user.workspace_id,
            "name": f"{user.workspace_id.replace('_', ' ').title()} Workspace",
            "role": user.role,
            "is_default": True,
        }
    ]


@router.get(
    "/duplicates",
    summary="Find duplicate or near-duplicate documents in the workspace",
)
async def find_duplicates(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    similarity_threshold: float = Query(default=0.95, ge=0.5, le=1.0),
):
    """Detects duplicate documents using content hash comparison."""
    corr_id = generate_correlation_id("find_dupes")
    vector_store = VectorStoreManager(workspace_id=user.workspace_id)
    try:
        docs, _ = await asyncio.wait_for(
            vector_store.search_documents_async(query="", filters={}, limit=500, correlation_id=corr_id),
            timeout=30.0,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {e}")
    import hashlib
    from collections import defaultdict

    file_hashes: dict[str, set] = defaultdict(set)
    for doc in docs:
        if not doc:
            continue
        sf = (doc.metadata.get("source_file") if hasattr(doc, "metadata") else None) or "unknown"
        text = (doc.page_content if hasattr(doc, "page_content") else "") or ""
        h = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()
        file_hashes[sf].add(h)
    file_fingerprints = {sf: hashlib.md5("|".join(sorted(hs)).encode()).hexdigest() for sf, hs in file_hashes.items()}
    fp_groups: dict[str, list] = defaultdict(list)
    for sf, fp in file_fingerprints.items():
        fp_groups[fp].append(sf)
    exact_dupes = [{"files": files, "type": "exact"} for fp, files in fp_groups.items() if len(files) > 1]
    return {
        "workspace_id": user.workspace_id,
        "correlation_id": corr_id,
        "documents_scanned": len(file_hashes),
        "exact_duplicate_groups": len(exact_dupes),
        "duplicates": exact_dupes,
        "similarity_threshold": similarity_threshold,
    }


@router.get(
    "",
    response_model=DocumentListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Workspace access denied"},
        429: {"model": ErrorResponse, "description": "Rate limited"},
    },
    summary="List indexed documents",
    description="Retrieve paginated list of documents with optional filtering.",
)
async def list_documents(
    request: Request,
    params: Annotated[DocumentQueryParams, Depends()],
    pagination: Annotated[PaginationParams, Depends()],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("list_docs")

    # ✅ Validate inputs
    is_valid, error = _validate_document_inputs(None, params.source_file, params.workspace_id, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    rate_limiter = RateLimiter()
    rate_key = f"docs_list:{user.workspace_id}:{user.user_id}"

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
            logger.warning(f"[{corr_id}] Document list rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")

    workspace_id = params.workspace_id or user.workspace_id
    if workspace_id != user.workspace_id and user.role not in [
        "admin",
        "workspace_admin",
    ]:
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    logger.info(f"[{corr_id}] List docs: workspace={workspace_id} filters={params.model_dump()}")

    return await _list_documents(workspace_id, params, pagination, corr_id)


@router.get(
    "/{document_id}",
    response_model=DocumentMetaResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Document not found"},
        429: {"model": ErrorResponse, "description": "Rate limited"},
    },
    summary="Get document metadata",
    description="Retrieve detailed metadata for a specific document.",
)
async def get_document(
    document_id: Annotated[str, FastAPIPath(..., max_length=255)],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("get_doc")

    # ✅ Validate inputs
    is_valid, error = _validate_document_inputs(document_id, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    rate_limiter = RateLimiter()
    rate_key = f"docs_get:{user.workspace_id}:{user.user_id}"

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
            logger.warning(f"[{corr_id}] Document get rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")

    vector_store = VectorStoreManager(workspace_id=user.workspace_id)

    try:
        doc = await asyncio.wait_for(
            vector_store.get_document_by_id_async(document_id, correlation_id=corr_id),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Get document failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve document")

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    metadata = getattr(doc, "metadata", {}) or {}
    return DocumentMetaResponse(
        source_file=metadata.get("source_file", document_id),
        document_type=metadata.get("document_type", metadata.get("file_type", "unknown")),
        language=metadata.get("language", "en"),
        page_count=max(int(metadata.get("page_count") or metadata.get("page_number") or 0), 0),
        chunk_count=max(int(metadata.get("chunk_count") or 1), 0),
        mean_ocr_confidence=float(metadata.get("mean_ocr_confidence") or metadata.get("ocr_confidence") or 0.0),
        ingest_timestamp=str(metadata.get("ingest_timestamp") or metadata.get("created_at") or ""),
        tags=metadata.get("tags") or [],
        correlation_id=corr_id,
    )


@router.delete(
    "/{source_file:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Editor role required"},
        404: {"model": ErrorResponse, "description": "Document not found"},
        429: {"model": ErrorResponse, "description": "Rate limited"},
    },
    summary="Delete document",
    description="Permanently remove document and all associated vectors.",
)
async def delete_document(
    source_file: Annotated[str, FastAPIPath(..., description="Filename to delete")],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_editor)],
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("delete_doc")

    # ✅ Validate inputs
    is_valid, error = _validate_document_inputs(None, source_file, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    rate_limiter = RateLimiter()
    rate_key = f"docs_delete:{user.workspace_id}:{user.user_id}"

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
            logger.warning(f"[{corr_id}] Document delete rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")

    # ✅ Path traversal protection
    sanitized = Path(source_file).name
    if sanitized != source_file:
        logger.warning(f"[{corr_id}] Path traversal attempt blocked: '{source_file}'")
        raise HTTPException(status_code=400, detail="Invalid filename: path traversal not allowed")

    if not sanitized or sanitized.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename: hidden files not allowed")

    logger.info(f"[{corr_id}] Delete request: doc={sanitized} user={user.user_id[:8]}...")

    success = await _delete_document(sanitized, user.workspace_id, corr_id)

    if not success:
        raise HTTPException(status_code=404, detail="Document not found")

    record_document_operation(
        workspace_id=user.workspace_id,
        correlation_id=corr_id,
        operation="delete",
        source_file=sanitized,
        success=True,
        user_id=user.user_id,
    )
    return None


@router.get(
    "/{source_file:path}/file",
    summary="Serve the original document file",
    description="Serves the original file for PDF viewer (react-pdf).",
    responses={
        404: {"model": ErrorResponse, "description": "File not found"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)
async def serve_document_file(
    source_file: Annotated[str, FastAPIPath(...)],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("serve_file")

    # ✅ Path traversal protection
    safe_name = Path(source_file).name
    if safe_name != source_file:
        raise HTTPException(status_code=400, detail="Invalid file path")

    import tempfile as _tf

    upload_dir = getattr(settings, "upload_dir", None) or str(Path(_tf.gettempdir()) / "docmind_uploads")
    file_path = Path(upload_dir) / user.workspace_id / safe_name

    # ✅ Validate file is within upload_dir (prevent directory escape)
    try:
        file_path.resolve().relative_to(Path(upload_dir).resolve())
    except ValueError:
        logger.warning(f"[{corr_id}] Path escape attempt: {file_path}")
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")

    try:
        # Check read permission
        if not file_path.is_file() or not os.access(file_path, os.R_OK):
            raise HTTPException(status_code=403, detail="File not accessible")
    except Exception as e:
        logger.error(f"[{corr_id}] File access check failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to access file")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf" if source_file.endswith(".pdf") else "application/octet-stream",
        filename=safe_name,
        headers={"X-Correlation-ID": corr_id},
    )


@router.get(
    "/{source_file:path}/download",
    summary="Download the original uploaded file",
)
async def download_document(
    source_file: Annotated[str, FastAPIPath(...)],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    """Download the original file that was uploaded."""
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("download")
    safe_name = Path(source_file).name
    if safe_name != source_file:
        raise HTTPException(status_code=400, detail="Invalid file path")

    upload_dir = getattr(settings, "upload_dir", "/tmp/uploads")
    file_path = Path(upload_dir) / user.workspace_id / safe_name

    try:
        file_path.resolve().relative_to(Path(upload_dir).resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")

    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    mime_map = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "txt": "text/plain",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }
    media_type = mime_map.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=safe_name,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Correlation-ID": corr_id,
        },
    )


@router.post(
    "/{document_id}/reindex",
    response_model=dict,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Editor role required"},
        404: {"model": ErrorResponse, "description": "Document not found"},
        429: {"model": ErrorResponse, "description": "Rate limited"},
    },
    summary="Reindex document",
    description="Re-process document with updated chunking/embedding settings.",
)
async def reindex_document(
    document_id: Annotated[str, FastAPIPath(...)],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_editor)],
    background_tasks: BackgroundTasks,
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("reindex")

    # ✅ Validate inputs
    is_valid, error = _validate_document_inputs(document_id, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    rate_limiter = RateLimiter()
    rate_key = f"docs_reindex:{user.workspace_id}:{user.user_id}"

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
            logger.warning(f"[{corr_id}] Reindex rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")

    logger.info(f"[{corr_id}] Reindex queued: doc={document_id}")

    async def _do_reindex():
        try:
            vector_store = VectorStoreManager(workspace_id=user.workspace_id)
            # VectorStoreManager.reindex_by_metadata_async() is the correct method
            await asyncio.wait_for(
                vector_store.reindex_by_metadata_async(
                    {"source_file": document_id},
                    correlation_id=corr_id,
                ),
                timeout=120.0,  # Longer timeout for reindexing
            )
            record_document_operation(
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
                operation="reindex",
                source_file=document_id,
                success=True,
                user_id=user.user_id,
            )
            logger.info(f"[{corr_id}] Reindex completed: doc={document_id}")
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Reindex timed out")
            record_document_operation(
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
                operation="reindex",
                source_file=document_id,
                success=False,
                user_id=user.user_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Reindex failed: {e}", exc_info=True)
            record_document_operation(
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
                operation="reindex",
                source_file=document_id,
                success=False,
                user_id=user.user_id,
            )
            # Don't re-raise — background task failures are logged but don't affect response

    background_tasks.add_task(_do_reindex)

    return {
        "document_id": document_id,
        "status": "queued",
        "correlation_id": corr_id,
        "message": "Reindexing job queued. Check status via /ingest/status/{document_id}",
    }


@router.post(
    "/workspaces",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid workspace name"},
        403: {"model": ErrorResponse, "description": "Admin role required"},
        409: {"model": ErrorResponse, "description": "Workspace already exists"},
        429: {"model": ErrorResponse, "description": "Rate limited"},
    },
    summary="Create new workspace",
    description="Create isolated workspace for document indexing (admin only).",
)
async def create_workspace(
    request: Request,
    body: dict,
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("create_ws")

    # ✅ Validate inputs
    workspace_name = body.get("name", "").strip().lower().replace(" ", "_")
    if not workspace_name or len(workspace_name) > 64:
        raise HTTPException(
            status_code=400,
            detail="Invalid workspace name (1-64 chars, alphanumeric + underscore)",
        )

    # ✅ Validate workspace name format
    if not re.match(r"^[a-z0-9_]+$", workspace_name):
        raise HTTPException(
            status_code=400,
            detail="Workspace name must contain only lowercase letters, numbers, and underscores",
        )

    rate_limiter = RateLimiter()
    rate_key = f"ws_create:{user.workspace_id}:{user.user_id}"

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
            logger.warning(f"[{corr_id}] Workspace create rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")

    manager = WorkspaceManager()

    try:
        exists = await asyncio.wait_for(
            manager.workspace_exists_async(workspace_name),
            timeout=10.0,
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Workspace check failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to check workspace existence")

    if exists:
        raise HTTPException(status_code=409, detail="Workspace already exists")

    try:
        await asyncio.wait_for(
            manager.create_workspace_async(
                workspace_id=workspace_name,
                created_by=user.user_id,
                description=body.get("description", ""),
            ),
            timeout=30.0,
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Workspace creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create workspace")

    logger.info(f"[{corr_id}] Workspace created: {workspace_name} by {user.user_id[:8]}...")

    return {
        "workspace_id": workspace_name,
        "created_by": user.user_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "correlation_id": corr_id,
    }


def get_document_metadata() -> dict[str, Any]:
    """✅ NEW: Return document API metadata for monitoring."""
    return {
        "allowed_extensions": [
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".bmp",
        ],
        "max_page_limit": 100,
        "rate_limits": {
            "read": {"endpoint_group": "query", "default_limit": "100/hour"},
            "write": {"endpoint_group": "query", "default_limit": "10/hour"},
        },
        "cache_timeout_seconds": _CACHE_TIMEOUT,
        "path_traversal_protection": True,
        "workspace_scoped": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["router", "get_document_metadata"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.api.routes.documents) -
# ========================================================================

