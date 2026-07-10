"""
OCR with automatic fallback chain:
  1. PaddleOCR (local, free, primary)
  2. Mistral OCR API (cloud, free tier — kicks in when PaddleOCR fails or is unavailable)
  3. pypdfium2 plain-text extraction (last resort for text-layer PDFs)

Usage:
    from app.ocr.ocr_with_fallback import extract_text_with_fallback
    text = await extract_text_with_fallback(image_bytes_or_pdf_path, settings)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


async def extract_text_with_fallback(
    source: Union[bytes, str, Path],
    *,
    mistral_api_key: Optional[str] = None,
    ocr_languages: list[str] = None,
    correlation_id: str = "ocr",
) -> tuple[str, str]:
    """
    Extract text from an image or PDF with automatic fallback.

    Returns:
        (text, provider_used) — provider_used is one of:
        "paddle", "mistral", "pypdfium2", "empty"
    """
    if ocr_languages is None:
        ocr_languages = ["en"]

    # ── 1. Try PaddleOCR (local) ─────────────────────────────────────
    try:
        text = await _paddle_extract(source, ocr_languages, correlation_id)
        if text and text.strip():
            logger.info(f"[{correlation_id}] OCR: PaddleOCR succeeded ({len(text)} chars)")
            return text, "paddle"
        logger.debug(f"[{correlation_id}] PaddleOCR returned empty — trying next provider")
    except Exception as e:
        logger.warning(f"[{correlation_id}] PaddleOCR failed: {e} — trying Mistral OCR")

    # ── 2. Try Mistral OCR API (cloud fallback) ───────────────────────
    if mistral_api_key:
        try:
            text = await _mistral_extract(source, mistral_api_key, correlation_id)
            if text and text.strip():
                logger.info(f"[{correlation_id}] OCR: Mistral OCR succeeded ({len(text)} chars)")
                return text, "mistral"
            logger.debug(f"[{correlation_id}] Mistral OCR returned empty — trying pypdfium2")
        except Exception as e:
            logger.warning(f"[{correlation_id}] Mistral OCR failed: {e} — trying pypdfium2")
    else:
        logger.debug(f"[{correlation_id}] No MISTRAL_API_KEY — skipping Mistral OCR fallback")

    # ── 3. Try pypdfium2 plain-text extraction (last resort for PDFs) ─
    try:
        text = await _pypdfium2_extract(source, correlation_id)
        if text and text.strip():
            logger.info(f"[{correlation_id}] OCR: pypdfium2 extraction succeeded ({len(text)} chars)")
            return text, "pypdfium2"
    except Exception as e:
        logger.warning(f"[{correlation_id}] pypdfium2 extraction failed: {e}")

    logger.error(f"[{correlation_id}] All OCR providers failed — returning empty string")
    return "", "empty"


async def _paddle_extract(
    source: Union[bytes, str, Path],
    languages: list[str],
    correlation_id: str,
) -> str:
    """Run PaddleOCR in a thread (it's sync) and return extracted text."""
    def _run() -> str:
        from app.ocr.paddle_ocr import PaddleOCREngine
        engine = PaddleOCREngine(language=languages[0] if languages else "en")
        if isinstance(source, (str, Path)):
            path = Path(source)
            if path.suffix.lower() == ".pdf":
                return engine.extract_text_from_pdf(str(path))
            else:
                return engine.extract_text_from_image(str(path))
        else:
            return engine.extract_text_from_bytes(source)

    return await asyncio.to_thread(_run)


async def _mistral_extract(
    source: Union[bytes, str, Path],
    api_key: str,
    correlation_id: str,
) -> str:
    """Call Mistral OCR API and return markdown text."""
    import base64
    import httpx

    # Convert source to base64 bytes
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = source

    b64 = base64.b64encode(raw).decode("utf-8")
    suffix = ".pdf" if raw[:4] == b"%PDF" else ".png"
    media_type = "application/pdf" if suffix == ".pdf" else "image/png"

    payload = {
        "model": "mistral-ocr-latest",
        "document": {
            "type": "base64",
            "data": b64,
            "media_type": media_type,
        },
        "include_image_base64": False,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.mistral.ai/v1/ocr",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    # Mistral OCR returns pages → each has markdown text
    pages = data.get("pages", [])
    return "\n\n".join(p.get("markdown", "") for p in pages if p.get("markdown"))


async def _pypdfium2_extract(
    source: Union[bytes, str, Path],
    correlation_id: str,
) -> str:
    """Extract text layer from PDF using pypdfium2 (no OCR, fast for text PDFs)."""
    def _run() -> str:
        import pypdfium2 as pdfium

        if isinstance(source, (str, Path)):
            pdf = pdfium.PdfDocument(str(source))
        else:
            pdf = pdfium.PdfDocument(source)

        parts = []
        for i, page in enumerate(pdf):
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    return await asyncio.to_thread(_run)


__all__ = ["extract_text_with_fallback"]
