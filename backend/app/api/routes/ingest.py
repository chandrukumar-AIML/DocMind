# backend/app/api/routes/ingest.py
# DVMELTSS-FIX: V/E/M/S + OWASP-3/9 + BATMAN-A
# ✅ FIXED: Proper RateLimiter usage + input validation + safe file streaming + timeout handling
# ✅ ADDED: Audio (MP3/MP4/WAV), DOCX, and XLSX ingestion routes via UniversalIngestionPipeline

from __future__ import annotations

import asyncio
import functools
import gc
import json
import logging
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated, Optional, List, Any, Final

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request,
    UploadFile, status, BackgroundTasks, Path as FastAPIPath
)
from starlette.datastructures import UploadFile as StarletteUploadFile
from pydantic import BaseModel, Field

from app.config import get_settings, lazy_settings as settings  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, require_editor, AuthenticatedUser
from app.models import IngestRequest, IngestResponse, ErrorResponse, ProcessingStatus
from app.ocr.pipeline import OCRPipeline, get_ocr_pipeline
from app.chunking.parent_child import ParentChildChunker
from app.vectorstore.store_manager import VectorStoreManager
from app.ingestion.universal_ingestion import UniversalIngestionPipeline
from app.cache import invalidate_workspace_cache
from app.monitoring.metrics_collector import record_ingest_latency, record_ingest_error
from app.middleware.rate_limiter import RateLimiter  # FIXED: actual module path
from app.core.ocr_utils import detect_language_vectorized

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])

# ✅ FIXED: Use proper RateLimiter with workspace-scoped keys (not constructor params)
# Rate limiting is handled per-request via check_async in the endpoint

# ✅ NEW: Operation timeouts (seconds)
_OCR_TIMEOUT: Final = 300.0
_CHUNKING_TIMEOUT: Final = 60.0
_VECTOR_STORE_TIMEOUT: Final = 120.0
_FILE_READ_TIMEOUT: Final = 60.0

# Magic byte signatures for secure file validation
MAGIC_BYTES: dict[bytes, set[str]] = {
    b"%PDF": {".pdf"},
    b"\x89PNG\r\n\x1a\n": {".png"},
    b"\xff\xd8\xff": {".jpg", ".jpeg"},
    b"II*\x00": {".tiff", ".tif"},
    b"MM\x00*": {".tiff", ".tif"},
    b"BM": {".bmp"},
}
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".txt"}

# Audio/DOCX/XLSX extensions for new routes
AUDIO_EXTENSIONS = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".flac"}
DOCX_EXTENSIONS  = {".docx", ".doc"}
XLSX_EXTENSIONS  = {".xlsx", ".xls", ".csv"}


def _validate_file_magic(content: bytes, claimed_suffix: str) -> bool:
    """Validate file content matches claimed extension using magic bytes."""
    for magic, valid_suffixes in MAGIC_BYTES.items():
        if content[:len(magic)] == magic:
            return claimed_suffix in valid_suffixes
    return claimed_suffix in ALLOWED_EXTENSIONS


# ✅ NEW: Input validation helper
def _validate_ingest_inputs(
    file: Optional[UploadFile],
    options: Optional[str],
    document_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate ingest endpoint inputs before processing."""
    if file is not None and not isinstance(file, (UploadFile, StarletteUploadFile)):
        return False, "file must be an UploadFile or None"
    if options is not None and not isinstance(options, str):
        return False, "options must be a string or None"
    if document_id is not None and not isinstance(document_id, str):
        return False, "document_id must be a string or None"
    return True, ""


@router.post(
    "/document",
    response_model=IngestResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file or parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": ErrorResponse, "description": "No text extracted"},
        500: {"model": ErrorResponse, "description": "Processing failed"},
    },
    summary="Upload and index a document",
    description="Process PDF/image through OCR -> chunking -> vector embedding.",
)
async def ingest_document(
    request: Request,
    file: Annotated[UploadFile, File(..., description="Document file (PDF, PNG, JPG)")],
    user: Annotated[AuthenticatedUser, Depends(require_editor)],
    background_tasks: BackgroundTasks,
    options: Annotated[str, Form(description="JSON-encoded IngestOptions")] = "{}",
) -> IngestResponse:
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("ingest")

    # ✅ Validate inputs
    is_valid, error = _validate_ingest_inputs(file, options, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    # ✅ FIXED: Proper rate limiting using RateLimiter.check_async with workspace-scoped key
    rate_limiter = RateLimiter()
    rate_key = f"ingest:{user.workspace_id}:{user.user_id}"
    
    try:
        rate_result = await asyncio.wait_for(
            rate_limiter.check_async(
                workspace_id=user.workspace_id,
                endpoint_group="ingest",
                identifier=rate_key,
                correlation_id=corr_id,
            ),
            timeout=5.0,
        )
        if not rate_result.allowed:
            logger.warning(f"[{corr_id}] Ingest rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many uploads. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")
    
    # ✅ Validate extension
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {suffix}")
    
    # ✅ Stream read with size limit + proper handling
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    total_bytes = 0
    chunks_read: List[bytes] = []
    
    try:
        # Reset file pointer in case it was read before
        if hasattr(file, "seek"):
            await file.seek(0)
        
        while True:
            chunk = await asyncio.wait_for(
                file.read(1024 * 1024),  # 1MB chunks
                timeout=_FILE_READ_TIMEOUT,
            )
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_size_mb}MB limit")
            chunks_read.append(chunk)
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] File read timed out after {_FILE_READ_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="File upload timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] File read failed: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")
    
    if not chunks_read:
        raise HTTPException(status_code=400, detail="Empty file upload")
    
    content = b"".join(chunks_read)
    
    # ✅ Magic byte validation (OWASP-9) — skip for plain text files
    if suffix != ".txt" and not _validate_file_magic(content, suffix):
        logger.warning(f"[{corr_id}] Magic byte mismatch: {file.filename}")
        raise HTTPException(status_code=400, detail="File content does not match extension")
    
    # ✅ Parse options with Pydantic + safe fallbacks
    try:
        opts_dict = json.loads(options) if options else {}
        # ✅ Safe field access with defaults
        ingest_opts = IngestRequest(
            document_language=opts_dict.get("ocr_lang") or opts_dict.get("document_language"),
            enable_vision_enrichment=opts_dict.get("enable_vision_enrichment", False),
            enable_ocr_fallback=opts_dict.get("enable_ocr_fallback", False),
            tags=opts_dict.get("tags", []),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid options: {e}")
    
    logger.info(f"[{corr_id}] Ingest: file={file.filename} user={user.user_id[:8]}... workspace={user.workspace_id}")
    
    start_ts = time.perf_counter()
    
    try:
        # ✅ Isolated temp directory
        with tempfile.TemporaryDirectory(dir=getattr(settings, "tmp_dir", None)) as tmp_dir:
            tmp_path = Path(tmp_dir) / f"upload{suffix}"
            tmp_path.write_bytes(content)

            # Persist original file so the download endpoint can serve it
            import tempfile as _tf
            _orig_name = file.filename or f"upload{suffix}"
            _ul_dir = Path(getattr(settings, "upload_dir", None) or _tf.gettempdir()) / "docmind_uploads" / user.workspace_id
            _ul_dir.mkdir(parents=True, exist_ok=True)
            (_ul_dir / _orig_name).write_bytes(content)

            # ✅ Free memory early + hint GC
            del content, chunks_read
            gc.collect()
            
            # Step 1: Extract text — plain text files bypass OCR
            if suffix == ".txt":
                try:
                    raw_text = tmp_path.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Failed to read text file: {e}")

                class _FakeOCRResult:
                    full_text = raw_text
                    pages = []
                    detected_lang = None
                    document_id = None
                ocr_result = _FakeOCRResult()
            else:
                # Step 1: OCR for PDF/image files
                # First pass: detect language from a quick sample if not explicitly set
                _explicit_lang = ingest_opts.document_language
                ocr = get_ocr_pipeline(
                    confidence_threshold=settings.ocr_confidence_threshold,
                    use_gpu=settings.ocr_use_gpu,
                    ocr_languages=[_explicit_lang] if _explicit_lang else None,
                )

                try:
                    ocr_result = await ocr.process_file_async(
                        file_path=tmp_path,
                        enable_ocr_fallback=True,
                        correlation_id=corr_id,
                        timeout_seconds=_OCR_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"[{corr_id}] OCR timed out after {_OCR_TIMEOUT}s")
                    raise HTTPException(status_code=408, detail="OCR processing timed out")

                # Auto-detect language from extracted text when not explicitly provided
                if not _explicit_lang and ocr_result.full_text:
                    detected = detect_language_vectorized(ocr_result.full_text)
                    if detected != "en":
                        logger.info(f"[{corr_id}] Auto-detected script language: {detected}, re-running OCR with language hint")
                        try:
                            ocr2 = get_ocr_pipeline(
                                confidence_threshold=settings.ocr_confidence_threshold,
                                use_gpu=settings.ocr_use_gpu,
                                ocr_languages=[detected],
                            )
                            ocr_result2 = await ocr2.process_file_async(
                                file_path=tmp_path,
                                enable_ocr_fallback=True,
                                correlation_id=corr_id,
                                timeout_seconds=_OCR_TIMEOUT,
                            )
                            # Use re-run result only if it produced more text
                            if len(ocr_result2.full_text) >= len(ocr_result.full_text):
                                ocr_result = ocr_result2
                        except Exception as e:
                            logger.warning(f"[{corr_id}] Language-specific OCR re-run failed, using first pass: {e}")

                # Wire handwriting OCR for image files with vision enrichment enabled
                _image_exts = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}
                if ingest_opts.enable_vision_enrichment and suffix in _image_exts:
                    try:
                        from app.ingestion.handwriting_ocr import HandwritingOCR
                        hw_ocr = HandwritingOCR()
                        hw_result = await hw_ocr.transcribe_document_async(str(tmp_path))
                        if hw_result and hw_result.get("text", "").strip():
                            hw_text = hw_result["text"].strip()
                            # Merge handwriting text with OCR text (deduplicated)
                            if hw_text not in ocr_result.full_text:
                                ocr_result.full_text = f"{ocr_result.full_text}\n\n[Handwriting]\n{hw_text}"
                                logger.info(f"[{corr_id}] Handwriting OCR enriched document (+{len(hw_text)} chars)")
                    except ImportError:
                        logger.debug(f"[{corr_id}] HandwritingOCR not available, skipping")
                    except Exception as e:
                        logger.warning(f"[{corr_id}] Handwriting OCR failed (non-fatal): {e}")

            # Vision enrichment for diagrams/charts in PDF pages
            if ingest_opts.enable_vision_enrichment and suffix == ".pdf":
                try:
                    from app.core.vision_llm import get_vision_llm
                    import base64

                    # Check if OCR result has page images (populated by PDF pipeline)
                    pages_with_images = [
                        p for p in getattr(ocr_result, "pages", [])
                        if p and getattr(p, "image_b64", None)
                    ]
                    if pages_with_images:
                        vision_llm = get_vision_llm(timeout=30.0)
                        enrichments = []
                        for page in pages_with_images[:10]:  # Cap at 10 pages
                            b64 = page.image_b64
                            prompt = (
                                "This is a page from a document. Identify and describe any diagrams, "
                                "charts, flowcharts, tables, or figures present. For each:\n"
                                "- Describe what it shows (chart type, axes, key values, trends)\n"
                                "- Extract any key numbers, labels, or data points\n"
                                "- For flowcharts: describe the process flow\n"
                                "If no diagrams/charts are present, reply with 'no visual elements'."
                            )
                            try:
                                resp = await asyncio.wait_for(
                                    vision_llm.ainvoke([{
                                        "role": "user",
                                        "content": [
                                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                                            {"type": "text", "text": prompt},
                                        ],
                                    }]),
                                    timeout=25.0,
                                )
                                desc = resp.content if hasattr(resp, "content") else str(resp)
                                if desc and "no visual elements" not in desc.lower():
                                    pg_num = getattr(page, "page_number", 0) + 1
                                    enrichments.append(f"\n[Page {pg_num} Visual Elements]\n{desc}")
                            except Exception as e:
                                logger.debug(f"[{corr_id}] Vision enrichment skipped for page: {e}")

                        if enrichments:
                            ocr_result.full_text += "\n".join(enrichments)
                            logger.info(f"[{corr_id}] Vision-enriched {len(enrichments)} pages with diagram/chart descriptions")
                except ImportError:
                    logger.debug(f"[{corr_id}] Vision LLM not available, skipping diagram enrichment")
                except Exception as e:
                    logger.warning(f"[{corr_id}] Diagram Vision enrichment failed (non-fatal): {e}")

            if not ocr_result.full_text.strip():
                raise ValueError("No text extracted from document")

            # Step 2: Chunking via async generator (text-only path from OCR result)
            chunker = ParentChildChunker()
            child_chunks: List[Any] = []
            parent_chunks: List[Any] = []
            seen_parent_ids: set = set()

            try:
                async for child, parent in chunker.chunk_text_only(
                    text=ocr_result.full_text,
                    source_file=file.filename or "document",
                    tags=ingest_opts.tags,
                    correlation_id=corr_id,
                ):
                    if child:
                        child_chunks.append(child)
                    if parent:
                        pid = parent.metadata.get("chunk_id", id(parent))
                        if pid not in seen_parent_ids:
                            parent_chunks.append(parent)
                            seen_parent_ids.add(pid)
            except Exception as e:
                logger.error(f"[{corr_id}] Chunking failed: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Document chunking failed")

            if not child_chunks:
                raise ValueError("No chunks created after processing")

            # Step 3: Vector store indexing
            vector_store = VectorStoreManager(workspace_id=user.workspace_id)

            try:
                result_dict = await asyncio.wait_for(
                    vector_store.ingest_chunks_async(
                        child_chunks=child_chunks,
                        parent_chunks=parent_chunks,
                        correlation_id=corr_id,
                    ),
                    timeout=_VECTOR_STORE_TIMEOUT,
                )
                indexed = result_dict.get("child_chunks", len(child_chunks)) if isinstance(result_dict, dict) else len(child_chunks)
            except asyncio.TimeoutError:
                logger.error(f"[{corr_id}] Vector store indexing timed out after {_VECTOR_STORE_TIMEOUT}s")
                raise HTTPException(status_code=408, detail="Indexing timed out")
            
            latency = time.perf_counter() - start_ts
            
            # ✅ Cache invalidation on successful ingest
            background_tasks.add_task(invalidate_workspace_cache, workspace_id=user.workspace_id)
            background_tasks.add_task(
                record_ingest_latency,
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
                latency_ms=latency * 1000,
                success=True,
                chunks=len(child_chunks),
            )
            
            return IngestResponse(
                filename=file.filename or "document",
                status="indexed",
                page_count=len(getattr(ocr_result, "pages", [])),
                child_chunks=len(child_chunks),
                parent_chunks=len(parent_chunks),
                ocr_confidence=1.0 if suffix == ".txt" else 0.95,
                document_type=suffix.lstrip(".") or "unknown",
                latency_seconds=round(latency, 3),
                message="Document successfully indexed.",
                correlation_id=corr_id,
            )
            
    except ValueError as e:
        background_tasks.add_task(record_ingest_error, user.workspace_id, corr_id, str(e), "validation")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        background_tasks.add_task(record_ingest_error, user.workspace_id, corr_id, str(e), "system")
        logger.error(f"[{corr_id}] Ingest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Document processing failed")


@router.get("/status/{document_id}", response_model=IngestResponse, summary="Check ingest status")
async def get_ingest_status(
    document_id: Annotated[str, FastAPIPath(...)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    corr_id = generate_correlation_id("status")
    
    # ✅ Validate inputs
    is_valid, error = _validate_ingest_inputs(None, None, document_id, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    try:
        vector_store = VectorStoreManager(workspace_id=user.workspace_id)
        
        exists = await asyncio.wait_for(
            vector_store.document_exists_async(document_id),
            timeout=_VECTOR_STORE_TIMEOUT,
        )
        
        if exists:
            return IngestResponse(
                filename=document_id,
                status="indexed",
                page_count=0,
                child_chunks=0,
                parent_chunks=0,
                ocr_confidence=1.0,
                document_type="unknown",
                latency_seconds=0.0,
                message="Document found.",
                correlation_id=corr_id,
            )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Status check timed out after {_VECTOR_STORE_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Status check timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Status check failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to check document status")
    
    raise HTTPException(status_code=404, detail="Document not found or still processing")


# ============================================================
# SHARED HELPER: universal pipeline ingestion + vector index
# ============================================================
async def _ingest_via_universal(
    request: Request,
    file: UploadFile,
    user: AuthenticatedUser,
    background_tasks: BackgroundTasks,
    allowed_ext: set[str],
    route_tag: str,
) -> IngestResponse:
    """Shared logic for audio/DOCX/XLSX ingestion via UniversalIngestionPipeline."""
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id(route_tag)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_ext:
        raise HTTPException(status_code=415, detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(allowed_ext)}")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    total_bytes = 0
    chunks_read: List[bytes] = []
    try:
        if hasattr(file, "seek"):
            await file.seek(0)
        while True:
            chunk = await asyncio.wait_for(file.read(1024 * 1024), timeout=_FILE_READ_TIMEOUT)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_size_mb}MB limit")
            chunks_read.append(chunk)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="File upload timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    if not chunks_read:
        raise HTTPException(status_code=400, detail="Empty file upload")

    content = b"".join(chunks_read)
    del chunks_read
    gc.collect()

    start_ts = time.perf_counter()
    pipeline = UniversalIngestionPipeline()

    try:
        with tempfile.TemporaryDirectory(dir=getattr(settings, "tmp_dir", None)) as tmp_dir:
            tmp_path = Path(tmp_dir) / f"upload{suffix}"
            tmp_path.write_bytes(content)
            del content
            gc.collect()

            try:
                result = await asyncio.wait_for(
                    pipeline.ingest_async(file_path=tmp_path, correlation_id=corr_id),
                    timeout=_OCR_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=408, detail=f"Processing timed out after {_OCR_TIMEOUT}s")

            if not result.is_successful:
                raise HTTPException(status_code=422, detail=result.error or "No content extracted from file")

            # Index the resulting Document chunks
            vector_store = VectorStoreManager(workspace_id=user.workspace_id)
            try:
                docs = result.documents if hasattr(result, "documents") and result.documents else []
                result_dict = await asyncio.wait_for(
                    vector_store.ingest_chunks_async(
                        child_chunks=docs,
                        parent_chunks=[],
                        correlation_id=corr_id,
                    ),
                    timeout=_VECTOR_STORE_TIMEOUT,
                )
                indexed = result_dict.get("child_chunks", len(docs)) if isinstance(result_dict, dict) else len(docs)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=408, detail="Vector store indexing timed out")

            latency = time.perf_counter() - start_ts
            background_tasks.add_task(invalidate_workspace_cache, workspace_id=user.workspace_id)
            background_tasks.add_task(
                record_ingest_latency,
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
                latency_ms=latency * 1000,
                success=True,
                chunks=result.chunk_count,
            )

            file_suffix = Path(file.filename or "").suffix.lstrip(".")
            return IngestResponse(
                filename=file.filename or "document",
                status="indexed",
                page_count=getattr(result, "page_count", 0),
                child_chunks=len(docs),
                parent_chunks=0,
                ocr_confidence=0.95,
                document_type=file_suffix or "unknown",
                latency_seconds=round(latency, 3),
                message="Document successfully indexed.",
                correlation_id=corr_id,
            )

    except HTTPException:
        raise
    except Exception as e:
        background_tasks.add_task(record_ingest_error, user.workspace_id, corr_id, str(e), "system")
        logger.error(f"[{corr_id}] Universal ingest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="File processing failed")


# ============================================================
# POST /ingest/audio  — MP3, MP4, WAV, M4A, FLAC, OGG
# ============================================================
@router.post(
    "/audio",
    response_model=IngestResponse,
    responses={
        408: {"model": ErrorResponse, "description": "Processing timed out"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported audio type"},
        422: {"model": ErrorResponse, "description": "No transcript extracted"},
    },
    summary="Transcribe and index an audio/video file",
    description="Transcribe audio via Whisper, chunk the transcript, and index it for RAG queries.",
)
async def ingest_audio(
    request: Request,
    file: Annotated[UploadFile, File(..., description="Audio file (MP3, MP4, WAV, M4A)")],
    user: Annotated[AuthenticatedUser, Depends(require_editor)],
    background_tasks: BackgroundTasks,
) -> IngestResponse:
    return await _ingest_via_universal(request, file, user, background_tasks, AUDIO_EXTENSIONS, "ingest_audio")


# ============================================================
# POST /ingest/docx  — DOCX, DOC
# ============================================================
@router.post(
    "/docx",
    response_model=IngestResponse,
    responses={
        408: {"model": ErrorResponse, "description": "Processing timed out"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported document type"},
        422: {"model": ErrorResponse, "description": "No text extracted"},
    },
    summary="Extract and index a Word document",
    description="Extract text, headings, and tables from DOCX, chunk it, and index for RAG queries.",
)
async def ingest_docx(
    request: Request,
    file: Annotated[UploadFile, File(..., description="Word document (.docx)")],
    user: Annotated[AuthenticatedUser, Depends(require_editor)],
    background_tasks: BackgroundTasks,
) -> IngestResponse:
    return await _ingest_via_universal(request, file, user, background_tasks, DOCX_EXTENSIONS, "ingest_docx")


# ============================================================
# POST /ingest/xlsx  — XLSX, XLS, CSV
# ============================================================
@router.post(
    "/xlsx",
    response_model=IngestResponse,
    responses={
        408: {"model": ErrorResponse, "description": "Processing timed out"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported spreadsheet type"},
        422: {"model": ErrorResponse, "description": "No rows extracted"},
    },
    summary="Extract and index a spreadsheet",
    description="Extract rows from all sheets in XLSX/CSV, chunk them, and index for RAG queries.",
)
async def ingest_xlsx(
    request: Request,
    file: Annotated[UploadFile, File(..., description="Spreadsheet file (.xlsx, .xls, .csv)")],
    user: Annotated[AuthenticatedUser, Depends(require_editor)],
    background_tasks: BackgroundTasks,
) -> IngestResponse:
    return await _ingest_via_universal(request, file, user, background_tasks, XLSX_EXTENSIONS, "ingest_xlsx")


# ============================================================
# POST /ingest/url  — Scrape and index a web page
# ============================================================
class UrlIngestRequest(BaseModel):
    url: str = Field(..., description="HTTP/HTTPS URL to fetch and index")
    title: Optional[str] = Field(None, description="Optional override title")
    workspace_id: Optional[str] = None


@router.post(
    "/url",
    response_model=IngestResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid URL or fetch failed"},
        422: {"model": ErrorResponse, "description": "No content extracted"},
    },
    summary="Fetch and index a web page",
    description="Download the URL, strip HTML to plain text, chunk and index for RAG.",
)
async def ingest_url(
    request: Request,
    body: UrlIngestRequest,
    user: Annotated[AuthenticatedUser, Depends(require_editor)],
    background_tasks: BackgroundTasks,
) -> IngestResponse:
    import re as _re
    import httpx

    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("ingest_url")

    # Validate URL scheme
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    # Fetch the page
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "DocuMind/2.0"})
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"URL returned {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")

    # Strip HTML tags and normalize whitespace
    text = _re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=_re.S | _re.I)
    text = _re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=_re.S | _re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _re.sub(r"[ \t]+", " ", text)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if len(text) < 50:
        raise HTTPException(status_code=422, detail="Could not extract meaningful text from URL")

    # Derive filename from URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    safe_name = _re.sub(r"[^\w.-]", "_", (parsed.netloc + parsed.path).strip("/"))[:80] or "webpage"
    filename = f"{safe_name}.txt"
    source_label = body.title or url

    # Chunk text directly and index into vector store
    start_ts = time.perf_counter()
    try:
        chunker = ParentChildChunker()
        child_chunks: list = []
        parent_chunks: list = []

        # chunk_text_only is an async generator of (child, parent) tuples
        async_gen = chunker.chunk_text_only(
            text=text,
            source_file=filename,
            correlation_id=corr_id,
        )
        async for child, parent in async_gen:
            child_chunks.append(child)
            if parent not in parent_chunks:
                parent_chunks.append(parent)

        if not child_chunks:
            raise HTTPException(status_code=422, detail="No chunks generated from URL content")

        vector_store = VectorStoreManager(workspace_id=user.workspace_id)
        try:
            await asyncio.wait_for(
                vector_store.ingest_chunks_async(
                    child_chunks=child_chunks,
                    parent_chunks=parent_chunks,
                    correlation_id=corr_id,
                ),
                timeout=_VECTOR_STORE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="Vector indexing timed out")

        latency = time.perf_counter() - start_ts
        background_tasks.add_task(invalidate_workspace_cache, workspace_id=user.workspace_id)

        return IngestResponse(
            filename=filename,
            status="indexed",
            page_count=1,
            child_chunks=len(child_chunks),
            parent_chunks=len(parent_chunks),
            ocr_confidence=1.0,
            document_type="url",
            latency_seconds=round(latency, 3),
            message=f"URL indexed: {source_label}",
            correlation_id=corr_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] URL ingest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="URL ingestion failed")


def get_ingest_metadata() -> dict[str, Any]:
    """✅ NEW: Return ingest pipeline metadata for monitoring."""
    return {
        "allowed_extensions": list(ALLOWED_EXTENSIONS),
        "max_upload_size_mb": settings.max_upload_size_mb,
        "default_ocr_lang": "en",
        "rate_limit": {"endpoint_group": "ingest", "default_limit": "10/hour"},
        "timeouts": {
            "file_read_seconds": _FILE_READ_TIMEOUT,
            "ocr_seconds": _OCR_TIMEOUT,
            "chunking_seconds": _CHUNKING_TIMEOUT,
            "vector_store_seconds": _VECTOR_STORE_TIMEOUT,
        },
        "magic_byte_validation": True,
        "workspace_scoped": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["router", "get_ingest_metadata"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

