
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Optional, Any

from langchain_core.documents import Document

from .format_detector import FormatDetector, FileFormat

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.ingest_utils import (
    validate_upload_path,
    generate_ingest_correlation_id,
    read_file_bytes_async,
    _MAX_FILE_SIZE_MB,
)
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution

logger = logging.getLogger(__name__)

_EXTENSION_FALLBACK: Final = {
    ".pdf": FileFormat.PDF,
    ".docx": FileFormat.DOCX,
    ".xlsx": FileFormat.XLSX,
    ".mp3": FileFormat.MP3,
    ".mp4": FileFormat.MP4,
    ".wav": FileFormat.WAV,
    ".png": FileFormat.PNG,
    ".jpg": FileFormat.JPEG,
    ".jpeg": FileFormat.JPEG,
}

_HANDLER_TIMEOUT: Final = 300  # 5 minutes


@dataclass(frozen=True)
class IngestionResult:
    """Immutable unified result from any ingestion path."""

    source_file: str
    format: str
    documents: list[Document]
    page_count: int = 0
    word_count: int = 0
    duration_sec: float = 0.0
    has_speakers: bool = False
    language: str = "en"
    error: Optional[str] = None
    correlation_id: Optional[str] = None

    @property
    def is_successful(self) -> bool:
        return self.error is None and len(self.documents) > 0

    @property
    def chunk_count(self) -> int:
        return len(self.documents)

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "source_file": self.source_file,
            "format": self.format,
            "chunk_count": self.chunk_count,
            "page_count": self.page_count,
            "word_count": self.word_count,
            "duration_sec": round(self.duration_sec, 2),
            "has_speakers": self.has_speakers,
            "language": self.language,
            "error": self.error,
            "is_successful": self.is_successful,
            "correlation_id": self.correlation_id,
        }


def _validate_ingest_inputs(
    file_path: Optional[str | Path],
    file_bytes: Optional[bytes],
    correlation_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate ingestion inputs before processing."""
    if file_path is None and file_bytes is None:
        return False, "Either file_path or file_bytes must be provided"
    if file_path is not None and not isinstance(file_path, (str, Path)):
        return False, "file_path must be a string, Path, or None"
    if file_bytes is not None and not isinstance(file_bytes, bytes):
        return False, "file_bytes must be bytes or None"
    if correlation_id is not None and not isinstance(correlation_id, str):
        return False, "correlation_id must be a string or None"
    return True, ""


class UniversalIngestionPipeline:
    """Routes any supported file format to the correct async ingestion handler."""

    def __init__(self):
        settings = get_settings()
        self.detector = FormatDetector()
        self.handwriting_threshold = float(getattr(settings, "handwriting_confidence_threshold", 0.70))

    async def ingest_async(
        self,
        file_path: str | Path,
        file_bytes: Optional[bytes] = None,
        correlation_id: Optional[str] = None,
    ) -> IngestionResult:
        """Async entry point: Detect format and route to correct handler."""
        corr_id = correlation_id or generate_ingest_correlation_id("universal")

        # ✅ Validate inputs
        is_valid, error = _validate_ingest_inputs(file_path, file_bytes, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid ingest inputs: {error}")
            return IngestionResult(
                source_file=str(file_path) if file_path else "unknown",
                format="unknown",
                documents=[],
                error=error,
                correlation_id=corr_id,
            )

        file_path_obj = Path(file_path) if isinstance(file_path, str) else file_path
        safe_path, path_error = validate_upload_path(file_path_obj)
        if path_error:
            return IngestionResult(
                source_file=str(file_path_obj),
                format="unknown",
                documents=[],
                error=path_error,
                correlation_id=corr_id,
            )

        if file_bytes is None:
            try:
                file_bytes = await read_file_bytes_async(safe_path, max_bytes=16)
            except Exception as e:
                return IngestionResult(
                    source_file=str(file_path_obj),
                    format="unknown",
                    documents=[],
                    error=f"Cannot read file: {e}",
                    correlation_id=corr_id,
                )

        detected = self.detector.detect(file_bytes, safe_path.name)
        fmt = detected.format
        logger.info(
            f"[{corr_id}] Universal ingest: {safe_path.name} | format={fmt.value} | confidence={detected.confidence:.2f}"
        )

        file_size_mb = safe_path.stat().st_size / 1024 / 1024
        if file_size_mb > _MAX_FILE_SIZE_MB:
            return IngestionResult(
                source_file=safe_path.name,
                format="unknown",
                documents=[],
                error=f"File too large: {file_size_mb:.1f}MB > {_MAX_FILE_SIZE_MB}MB",
                correlation_id=corr_id,
            )

        try:
            if fmt == FileFormat.DOCX:
                return await asyncio.wait_for(
                    self._ingest_docx_async(safe_path, corr_id),
                    timeout=_HANDLER_TIMEOUT,
                )
            elif fmt == FileFormat.XLSX:
                return await asyncio.wait_for(
                    self._ingest_xlsx_async(safe_path, corr_id),
                    timeout=_HANDLER_TIMEOUT,
                )
            elif detected.is_audio_video:
                return await asyncio.wait_for(
                    self._ingest_audio_async(safe_path, file_bytes, corr_id),
                    timeout=_HANDLER_TIMEOUT,
                )
            elif fmt in (FileFormat.PDF,) or detected.is_image:
                return await asyncio.wait_for(
                    self._ingest_document_async(safe_path, corr_id),
                    timeout=_HANDLER_TIMEOUT,
                )
            else:
                return IngestionResult(
                    source_file=safe_path.name,
                    format="unknown",
                    documents=[],
                    error=f"Unsupported format: {fmt.value} ({safe_path.suffix})",
                    correlation_id=corr_id,
                )
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Ingestion handler timed out after {_HANDLER_TIMEOUT}s")
            return IngestionResult(
                source_file=safe_path.name,
                format="unknown",
                documents=[],
                error=f"Handler timed out after {_HANDLER_TIMEOUT}s",
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Ingestion routing failed: {e}", exc_info=True)
            return IngestionResult(
                source_file=safe_path.name,
                format="unknown",
                documents=[],
                error=f"Ingestion pipeline error: {type(e).__name__}",
                correlation_id=corr_id,
            )

    async def _ingest_document_async(self, file_path: Path, corr_id: str) -> IngestionResult:
        """Async: Route to existing OCR pipeline for PDF/image files."""
        try:
            from app.ocr.pipeline import get_ocr_pipeline
            from app.chunking.parent_child import ParentChildChunker

            ocr_pipeline = get_ocr_pipeline()
            enriched = await ocr_pipeline.process_file_enriched_async(str(file_path), correlation_id=corr_id)

            chunker = ParentChildChunker()
            child_chunks, _ = await chunker.chunk_enriched_document_async(
                enriched=enriched, source_file=file_path.name, correlation_id=corr_id
            )

            word_count = sum(len(doc.page_content.split()) for doc in child_chunks)
            lang = getattr(enriched.metadata, "language", "en") if getattr(enriched, "metadata", None) else "en"

            return IngestionResult(
                source_file=file_path.name,
                format="pdf_image",
                documents=child_chunks,
                page_count=len(enriched.ocr_result.pages) if hasattr(enriched, "ocr_result") else 0,
                word_count=word_count,
                language=lang,
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Document ingest failed: {e}")
            return IngestionResult(
                source_file=file_path.name,
                format="pdf_image",
                documents=[],
                error=str(e),
                correlation_id=corr_id,
            )

    async def _ingest_audio_async(self, file_path: Path, file_bytes: bytes, corr_id: str) -> IngestionResult:
        """Async: Transcribe audio/video and create text Documents."""
        try:
            from .audio_transcriber import AudioTranscriber

            transcriber = AudioTranscriber()
            result = await transcriber.transcribe_async(file_path, file_bytes=file_bytes, correlation_id=corr_id)

            if result.error:
                return IngestionResult(
                    source_file=file_path.name,
                    format="audio",
                    documents=[],
                    error=result.error,
                    correlation_id=corr_id,
                )

            chunks = result.to_chunks(max_words=200)
            now = datetime.now(timezone.utc).isoformat()

            docs = [
                Document(
                    page_content=chunk,
                    metadata={
                        "source_file": file_path.name,
                        "page_number": 0,
                        "chunk_id": str(uuid.uuid4()),
                        "parent_id": "",
                        "block_type": "transcript",
                        "language": result.language,
                        "ocr_confidence": 1.0,
                        "chunk_type": "child",
                        "ingest_timestamp": now,
                        "document_type": "audio",
                        "char_count": len(chunk),
                        "audio_duration": result.duration_sec,
                        "has_speakers": result.has_speakers,
                        "chunk_index": i,
                        "correlation_id": corr_id,
                    },
                )
                for i, chunk in enumerate(chunks)
                if chunk.strip()
            ]

            return IngestionResult(
                source_file=file_path.name,
                format="audio",
                documents=docs,
                page_count=1,
                word_count=len(result.full_text.split()) if result.full_text else 0,
                duration_sec=result.duration_sec,
                has_speakers=result.has_speakers,
                language=result.language,
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Audio ingest failed: {e}")
            return IngestionResult(
                source_file=file_path.name,
                format="audio",
                documents=[],
                error=str(e),
                correlation_id=corr_id,
            )

    async def _ingest_docx_async(self, file_path: Path, corr_id: str) -> IngestionResult:
        """Async: Extract and chunk a Word document."""
        try:
            from .docx_extractor import DocxExtractor

            extractor = DocxExtractor()
            content = await extractor.extract_async(file_path, correlation_id=corr_id)

            if content.error:
                return IngestionResult(
                    source_file=file_path.name,
                    format="docx",
                    documents=[],
                    error=content.error,
                    correlation_id=corr_id,
                )

            docs = await extractor.to_langchain_documents_async(content, correlation_id=corr_id)

            return IngestionResult(
                source_file=file_path.name,
                format="docx",
                documents=docs,
                page_count=1,
                word_count=len(content.full_text.split()) if content.full_text else 0,
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] DOCX ingest failed: {e}")
            return IngestionResult(
                source_file=file_path.name,
                format="docx",
                documents=[],
                error=str(e),
                correlation_id=corr_id,
            )

    async def _ingest_xlsx_async(self, file_path: Path, corr_id: str) -> IngestionResult:
        """Async: Extract and chunk an Excel spreadsheet."""
        try:
            from .xlsx_extractor import XlsxExtractor

            extractor = XlsxExtractor()
            content = await extractor.extract_async(file_path, correlation_id=corr_id)

            if content.error:
                return IngestionResult(
                    source_file=file_path.name,
                    format="xlsx",
                    documents=[],
                    error=content.error,
                    correlation_id=corr_id,
                )

            docs = extractor.to_langchain_documents(content, correlation_id=corr_id)

            return IngestionResult(
                source_file=file_path.name,
                format="xlsx",
                documents=docs,
                page_count=content.sheet_count if hasattr(content, "sheet_count") else 0,
                word_count=sum(len(d.page_content.split()) for d in docs),
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] XLSX ingest failed: {e}")
            return IngestionResult(
                source_file=file_path.name,
                format="xlsx",
                documents=[],
                error=str(e),
                correlation_id=corr_id,
            )

    def ingest(
        self,
        file_path: str | Path,
        file_bytes: Optional[bytes] = None,
        correlation_id: Optional[str] = None,
    ) -> IngestionResult:
        """
        Sync wrapper — prefers async version in new code.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_ingest():
            return await self.ingest_async(file_path, file_bytes, correlation_id)

        return run_async_in_task(_do_ingest)


def get_ingestion_metadata() -> dict[str, Any]:
    """✅ NEW: Return ingestion metadata for debugging."""
    return {
        "supported_formats": list(_EXTENSION_FALLBACK.keys()),
        "max_file_size_mb": _MAX_FILE_SIZE_MB,
        "handler_timeout_seconds": _HANDLER_TIMEOUT,
        "default_language": "en",
        "async_safe": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "UniversalIngestionPipeline",
    "IngestionResult",
    "get_ingestion_metadata",
]
# Local smoke test entry point. Run: python -m

