
from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

from app.core.ocr_utils import scrub_pii_for_ocr

if TYPE_CHECKING:
    from .paddle_ocr import TextBlock
    from .vision_analyzer import EnrichedDocument, TableAnalysis, DiagramAnalysis

logger = logging.getLogger(__name__)

_MAX_FORMAT_LENGTH: int = 8000  # Prevent huge strings from blocking scrubbing
_MAX_BULLET_POINTS: int = 20  # Limit diagram key points for embedding efficiency


class EnrichedTextFormatter:
    """
    Formats enriched blocks into optimal text for vector embedding.

    Strategy:
    - Tables: Summary + markdown for semantic search
    - Diagrams: Type + description + key data points (bullet-safe)
    - Text: Raw text with optional PII scrubbing + length guard

    ✅ FIXED: Input validation, safe attribute access, async support.
    """

    @staticmethod
    def _validate_inputs(
        block: "TextBlock | None", enriched: "EnrichedDocument | None", corr_id: str
    ) -> tuple[bool, str]:
        """Validate inputs before formatting."""
        if block is None:
            return False, "block is None"
        if enriched is None:
            return False, "enriched document is None"
        if not hasattr(block, "text") or not hasattr(block, "block_type"):
            return False, "block missing required attributes"
        return True, ""

    @staticmethod
    def _safe_scrub(text: str, scrub_pii: bool, max_length: int, corr_id: str) -> str:
        """
        Safely scrub PII with length guard.
        ✅ Prevents blocking on huge strings.
        """
        if not scrub_pii:
            return text[:max_length]

        if len(text) > max_length:
            logger.debug(f"[{corr_id}] Truncating text from {len(text)} to {max_length} chars before PII scrub")
            text = text[:max_length]

        try:
            return scrub_pii_for_ocr(text)
        except Exception as e:
            logger.warning(f"[{corr_id}] PII scrub failed: {e} — returning unscrubbed text")
            return text

    @staticmethod
    def format_block(
        block: "TextBlock",
        enriched: "EnrichedDocument",
        scrub_pii: bool = True,
        correlation_id: str | None = None,
    ) -> str:
        """
        Format a block for embedding with optional PII scrubbing.

        ✅ FIXED: Input validation + safe attribute access + correlation_id tracing.
        """
        corr_id = correlation_id or f"format_p{block.page_num}_l{block.line_num}"

        # ✅ Validate inputs first
        is_valid, error = EnrichedTextFormatter._validate_inputs(block, enriched, corr_id)
        if not is_valid:
            logger.warning(f"[{corr_id}] Invalid inputs: {error} — returning empty")
            return ""

        block_id = f"p{block.page_num}_l{block.line_num}"

        if block.block_type == "table":
            return EnrichedTextFormatter._format_table(block_id, block, enriched, scrub_pii, corr_id)
        if block.block_type in {"figure", "figure_caption"}:
            return EnrichedTextFormatter._format_diagram(block_id, block, enriched, scrub_pii, corr_id)

        # Default: return text with optional PII scrubbing + length guard
        return EnrichedTextFormatter._safe_scrub(block.text, scrub_pii, _MAX_FORMAT_LENGTH, corr_id)

    @staticmethod
    async def format_block_async(
        block: "TextBlock",
        enriched: "EnrichedDocument",
        scrub_pii: bool = True,
        correlation_id: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> str:
        """
        Async wrapper for format_block — runs blocking scrub in thread pool.

        ✅ Use this in FastAPI routes to avoid event loop freeze.
        """
        corr_id = correlation_id or f"format_p{block.page_num}_l{block.line_num}"

        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: EnrichedTextFormatter.format_block(block, enriched, scrub_pii, corr_id),
                ),
                timeout=timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Formatting timed out after {timeout_seconds}s")
            # Return minimal fallback
            return block.text[:200] if hasattr(block, "text") else ""

    @staticmethod
    def _format_table(
        block_id: str,
        block: "TextBlock",
        enriched: "EnrichedDocument",
        scrub_pii: bool,
        corr_id: str,
    ) -> str:
        """Format table block with enriched analysis."""
        ta: "TableAnalysis | None" = enriched.table_analyses.get(block_id)

        if not ta:
            # Fallback to raw block text
            return EnrichedTextFormatter._safe_scrub(block.text, scrub_pii, _MAX_FORMAT_LENGTH, corr_id)

        # ✅ Safe attribute access with defaults
        summary = getattr(ta, "summary", "") or ""
        markdown_table = getattr(ta, "markdown_table", "") or ""

        if not summary and not markdown_table:
            return EnrichedTextFormatter._safe_scrub(block.text, scrub_pii, _MAX_FORMAT_LENGTH, corr_id)

        # Combine summary + markdown for rich embedding
        formatted = f"{summary}\n\n{markdown_table}" if summary and markdown_table else (summary or markdown_table)

        return EnrichedTextFormatter._safe_scrub(formatted, scrub_pii, _MAX_FORMAT_LENGTH, corr_id)

    @staticmethod
    def _format_diagram(
        block_id: str,
        block: "TextBlock",
        enriched: "EnrichedDocument",
        scrub_pii: bool,
        corr_id: str,
    ) -> str:
        """Format diagram block with enriched analysis."""
        da: "DiagramAnalysis | None" = enriched.diagram_analyses.get(block_id)

        if not da:
            return EnrichedTextFormatter._safe_scrub(block.text, scrub_pii, _MAX_FORMAT_LENGTH, corr_id)

        # ✅ Safe attribute access with defaults
        diagram_type = getattr(da, "diagram_type", "figure") or "figure"
        description = getattr(da, "description", "") or ""
        key_data_points = getattr(da, "key_data_points", []) or []

        if not description and not key_data_points:
            return EnrichedTextFormatter._safe_scrub(block.text, scrub_pii, _MAX_FORMAT_LENGTH, corr_id)

        bullets = "\n• ".join(key_data_points[:_MAX_BULLET_POINTS])  # Limit points for efficiency
        formatted = f"[{diagram_type.replace('_', ' ').title()}] {description}"
        if bullets:
            formatted += f"\n\nKey data:\n• {bullets}"

        return EnrichedTextFormatter._safe_scrub(formatted, scrub_pii, _MAX_FORMAT_LENGTH, corr_id)

    @staticmethod
    def format_document(
        enriched: "EnrichedDocument",
        scrub_pii: bool = True,
        correlation_id: str | None = None,
        include_metadata: bool = True,
    ) -> str:
        """
        ✅ NEW: Format entire enriched document for embedding.

        Args:
            enriched: EnrichedDocument with OCR + Vision analysis
            scrub_pii: Whether to scrub PII from output
            correlation_id: Request ID for tracing
            include_metadata: Whether to prepend document metadata

        Returns:
            Formatted text ready for vector embedding
        """
        corr_id = correlation_id or enriched.correlation_id or "format_doc"
        parts = []

        # Optional: prepend document metadata
        if include_metadata and enriched.metadata:
            meta = enriched.metadata
            meta_text = (
                f"Document: {meta.title}\n"
                f"Type: {meta.document_type}\n"
                f"Language: {meta.language}\n"
                f"Pages: {meta.page_count}\n"
                f"Summary: {meta.summary}\n"
                f"Entities: {', '.join(meta.key_entities[:10])}\n\n"
            )
            parts.append(EnrichedTextFormatter._safe_scrub(meta_text, scrub_pii, _MAX_FORMAT_LENGTH, corr_id))

        # Format each block in reading order
        for page in enriched.ocr_result.pages:
            for block in sorted(page.blocks, key=lambda b: b.line_num):
                formatted = EnrichedTextFormatter.format_block(block, enriched, scrub_pii, corr_id)
                if formatted.strip():
                    parts.append(formatted)

        # Join with double newline for clear separation
        full_text = "\n\n".join(parts)

        # Final length guard for embedding safety
        if len(full_text) > _MAX_FORMAT_LENGTH * 2:
            logger.warning(f"[{corr_id}] Formatted doc too large ({len(full_text)} chars) — truncating")
            full_text = full_text[: _MAX_FORMAT_LENGTH * 2]

        return full_text

    @staticmethod
    def get_format_stats(
        enriched: "EnrichedDocument",
        scrub_pii: bool = True,
    ) -> dict[str, int | float]:
        """
        ✅ NEW: Return formatting metrics for monitoring.

        Useful for tracking embedding token usage and PII scrub overhead.
        """
        total_chars = 0
        scrubbed_chars = 0

        for page in enriched.ocr_result.pages:
            for block in page.blocks:
                original = len(block.text)
                total_chars += original
                if scrub_pii:
                    scrubbed = len(scrub_pii_for_ocr(block.text))
                    scrubbed_chars += scrubbed

        return {
            "total_blocks": sum(len(p.blocks) for p in enriched.ocr_result.pages),
            "total_chars_original": total_chars,
            "total_chars_after_scrub": scrubbed_chars if scrub_pii else total_chars,
            "scrub_reduction_pct": round((1 - scrubbed_chars / total_chars) * 100, 2)
            if total_chars > 0 and scrub_pii
            else 0.0,
            "table_analyses": len(enriched.table_analyses),
            "diagram_analyses": len(enriched.diagram_analyses),
        }


# DVMELTSS-M: Explicit module exports
__all__ = ["EnrichedTextFormatter"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.ocr.text_formatter) --
# ========================================================================

