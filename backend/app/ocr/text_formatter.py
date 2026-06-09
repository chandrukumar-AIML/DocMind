# backend/app/ocr/text_formatter.py
# DVMELTSS-FIX: M - Modular, V - Validate, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Input validation + safe attribute access
# ✅ FIXED: Length guard for PII scrubbing + async wrapper
# ✅ FIXED: Correlation_id propagation + bullet-safe list formatting

from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

from app.core.ocr_utils import scrub_pii_for_ocr

if TYPE_CHECKING:
    from .paddle_ocr import TextBlock
    from .vision_analyzer import EnrichedDocument, TableAnalysis, DiagramAnalysis

logger = logging.getLogger(__name__)

# ✅ NEW: Formatting constraints
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

        # ✅ FIXED: Use bullet format (\n• ) instead of semicolons to avoid delimiter collision
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

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]

    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    def run_tests():
        print("🔍 Testing Text Formatter module (app/ocr/text_formatter.py)")
        print("=" * 70)

        try:
            from app.ocr.text_formatter import (
                EnrichedTextFormatter,
                _MAX_FORMAT_LENGTH,
                _MAX_BULLET_POINTS,
            )

            # -- Mock Classes for Testing ------------------------------
            # We define these locally to avoid circular imports with paddle_ocr/vision_analyzer

            class MockTextBlock:
                def __init__(self, text, block_type, page_num, line_num):
                    self.text = text
                    self.block_type = block_type
                    self.page_num = page_num
                    self.line_num = line_num

            class MockTableAnalysis:
                def __init__(self, summary="", markdown=""):
                    self.summary = summary
                    self.markdown_table = markdown

            class MockDiagramAnalysis:
                def __init__(self, d_type="figure", desc="", points=[]):
                    self.diagram_type = d_type
                    self.description = desc
                    self.key_data_points = points

            class MockMetadata:
                def __init__(self):
                    self.title = "Test Doc"
                    self.document_type = "PDF"
                    self.language = "en"
                    self.page_count = 1
                    self.summary = "Test summary"
                    self.key_entities = ["A", "B"]

            class MockPage:
                def __init__(self, blocks):
                    self.blocks = blocks

            class MockOCRResult:
                def __init__(self, pages):
                    self.pages = pages

            class MockEnrichedDocument:
                def __init__(self, ocr_result=None, metadata=None):
                    self.correlation_id = "test-corr"
                    self.metadata = metadata
                    self.ocr_result = ocr_result
                    self.table_analyses = {}
                    self.diagram_analyses = {}

            # -- Test 1: Structure & Constants -----------------------
            print("\n📌 Test 1: Structure & Constants")
            assert _MAX_FORMAT_LENGTH == 8000
            assert _MAX_BULLET_POINTS == 20
            print(f"   ✅ Constants: length={_MAX_FORMAT_LENGTH}, bullets={_MAX_BULLET_POINTS}")

            assert hasattr(EnrichedTextFormatter, "format_block")
            assert hasattr(EnrichedTextFormatter, "format_document")
            print("   ✅ Methods: format_block, format_document present")

            # -- Test 2: Input Validation ---------------------------
            print("\n📌 Test 2: Input Validation")

            valid_block = MockTextBlock("text", "text", 1, 1)
            valid_enriched = MockEnrichedDocument()

            is_valid, error = EnrichedTextFormatter._validate_inputs(None, valid_enriched, "t1")
            assert not is_valid
            print("   ✅ Validation: None block rejected")

            is_valid, error = EnrichedTextFormatter._validate_inputs(valid_block, None, "t1")
            assert not is_valid
            print("   ✅ Validation: None enriched rejected")

            # Block with missing attributes
            bad_block = object()
            is_valid, error = EnrichedTextFormatter._validate_inputs(bad_block, valid_enriched, "t1")
            assert not is_valid
            print("   ✅ Validation: Block missing attributes rejected")

            # -- Test 3: Safe Scrubbing -----------------------------
            print("\n📌 Test 3: Safe Scrubbing & Truncation")

            # Truncation test (without scrubbing)
            long_text = "A" * 10000
            result = EnrichedTextFormatter._safe_scrub(long_text, scrub_pii=False, max_length=200, corr_id="t3")
            assert len(result) == 200
            print("   ✅ Truncation: Long text truncated when scrub=False")

            # Scrubbing test (PII) - using real scrub_pii_for_ocr logic
            text_with_pii = "Contact john@example.com at 123-456-7890 for info."
            result = EnrichedTextFormatter._safe_scrub(text_with_pii, scrub_pii=True, max_length=200, corr_id="t3")
            # scrub_pii_for_ocr replaces emails/phones with [EMAIL], [PHONE]
            assert "john@example.com" not in result or "[EMAIL]" in result
            print("   ✅ Scrubbing: PII removed/replaced")

            # -- Test 4: Format Block (Text) ------------------------
            print("\n📌 Test 4: Format Block (Text)")

            block = MockTextBlock("Hello World", "text", 1, 1)
            enriched = MockEnrichedDocument()

            result = EnrichedTextFormatter.format_block(block, enriched, scrub_pii=False, correlation_id="t4")
            assert result == "Hello World"
            print("   ✅ Text Block: Returns raw text")

            # -- Test 5: Format Block (Table) -----------------------
            print("\n📌 Test 5: Format Block (Table)")

            table_block = MockTextBlock("Raw Table Text", "table", 1, 1)
            enriched = MockEnrichedDocument()

            # No analysis -> fallback
            result = EnrichedTextFormatter.format_block(table_block, enriched, scrub_pii=False, correlation_id="t5")
            assert result == "Raw Table Text"
            print("   ✅ Table Block: Fallback to raw text when no analysis")

            # With analysis -> key is constructed as f"p{page_num}_l{line_num}" -> "p1_l1"
            enriched.table_analyses["p1_l1"] = MockTableAnalysis(
                summary="Sales Summary", markdown="| Q | V |\n|---|---|\n| 1 | 100 |"
            )
            result = EnrichedTextFormatter.format_block(table_block, enriched, scrub_pii=False, correlation_id="t5")
            assert "Sales Summary" in result
            assert "| Q | V |" in result
            print("   ✅ Table Block: Returns summary + markdown")

            # -- Test 6: Format Block (Diagram) ---------------------
            print("\n📌 Test 6: Format Block (Diagram)")

            fig_block = MockTextBlock("Figure 1 caption", "figure", 1, 1)
            enriched = MockEnrichedDocument()

            # With analysis -> key "p1_l1"
            enriched.diagram_analyses["p1_l1"] = MockDiagramAnalysis(
                d_type="bar_chart",
                desc="Sales over time",
                points=["Jan: 100", "Feb: 120", "Mar: 150"],
            )
            result = EnrichedTextFormatter.format_block(fig_block, enriched, scrub_pii=False, correlation_id="t6")
            assert "[Bar Chart] Sales over time" in result
            assert "• Jan: 100" in result
            print("   ✅ Diagram Block: Returns formatted description + bullets")

            # -- Test 7: Format Document ----------------------------
            print("\n📌 Test 7: Format Document")

            b1 = MockTextBlock("Block 1", "text", 1, 1)
            b2 = MockTextBlock("Block 2", "text", 1, 2)

            p1 = MockPage([b1, b2])
            ocr_res = MockOCRResult([p1])
            meta = MockMetadata()
            enriched = MockEnrichedDocument(ocr_result=ocr_res, metadata=meta)

            result = EnrichedTextFormatter.format_document(
                enriched, scrub_pii=False, include_metadata=True, correlation_id="t7"
            )
            assert "Document: Test Doc" in result
            assert "Block 1" in result
            assert "Block 2" in result
            print("   ✅ Document: Metadata + Blocks formatted together")

            # -- Test 8: Async Wrapper ------------------------------
            print("\n📌 Test 8: Async Wrapper")

            async def test_async():
                block = MockTextBlock("Async Text", "text", 1, 1)
                enriched = MockEnrichedDocument()
                result = await EnrichedTextFormatter.format_block_async(
                    block,
                    enriched,
                    scrub_pii=False,
                    correlation_id="t8",
                    timeout_seconds=2.0,
                )
                assert result == "Async Text"
                return True

            success = asyncio.run(test_async())
            assert success
            print("   ✅ Async: Wrapper works correctly")

            # -- Test 9: Stats --------------------------------------
            print("\n📌 Test 9: Get Format Stats")

            b1 = MockTextBlock("12345", "text", 1, 1)
            p1 = MockPage([b1])
            enriched = MockEnrichedDocument(ocr_result=MockOCRResult([p1]))

            stats = EnrichedTextFormatter.get_format_stats(enriched, scrub_pii=False)
            assert stats["total_blocks"] == 1
            assert stats["total_chars_original"] == 5
            print("   ✅ Stats: Correct block and char counts")

            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! Text Formatter module verified.")
            print("\n💡 What we verified:")
            print("   • Constants: limits and formatting rules ✅")
            print("   • Validation: checks for nulls and attributes ✅")
            print("   • Scrubbing: length guards and PII removal ✅")
            print("   • Formatting: Text, Table (w/ markdown), Diagram (w/ bullets) ✅")
            print("   • Document: Full structure assembly ✅")
            print("   • Async: Non-blocking execution ✅")
            print("   • Stats: Metrics calculation ✅")
            return True

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    # Run tests
    success = run_tests()
    sys.exit(0 if success else 1)
