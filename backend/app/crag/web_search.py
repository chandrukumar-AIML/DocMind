# backend/app/crag/web_search.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, M - Modular
# BATMAN-FIX: A - Async (to_thread), T - Time complexity (retry backoff)
# OWASP-FIX: 1 - Search injection prevention, 9 - Safe ID generation

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Final, Optional

from langchain_core.documents import Document

# DVMELTSS-M: Import centralized utilities
from app.core.ids import generate_web_result_id
from app.core.prompts import escape_prompt_content

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & SECURITY (DVMELTSS-S, OWASP-1) ------------------------
# ========================================================================

DEFAULT_MAX_RESULTS: Final = 5
MAX_QUERY_LENGTH: Final = 200
_MAX_RESULTS_CAP: Final = 10  # Prevent abuse via oversized requests

# OWASP-1: Strip control characters & potentially malicious symbols from search queries
_QUERY_SANITIZE_PATTERN: Final = re.compile(r'[^\w\s.,!?\'"-]')

# Retry configuration for transient DuckDuckGo API/network issues
_MAX_SEARCH_RETRIES: Final = 2
_RETRY_DELAY_BASE: Final = 1.0  # seconds


@dataclass(frozen=True)
class WebSearchResult:
    """Immutable result from web search formatted for RAG pipeline."""

    query: str
    documents: list[Document]
    source: str = "web_search"
    result_count: int = 0


def _sanitize_query(query: str) -> str:
    """
    OWASP-1: Sanitize search query to prevent injection & limit length.
    - Removes non-standard symbols
    - Truncates to safe length
    - Strips whitespace
    """
    clean = _QUERY_SANITIZE_PATTERN.sub("", query)
    return clean.strip()[:MAX_QUERY_LENGTH]


# ========================================================================
# -- WEB SEARCHER CLASS (BATMAN-A, DVMELTSS-E) -------------------------
# ========================================================================


class WebSearcher:
    """
    DuckDuckGo web search fallback for CRAG pipeline.

    Used when:
    1. All retrieved documents are graded IRRELEVANT
    2. Query rewriting has been exhausted (max retries)
    3. Query requires real-time or external information

    Features (DVMELTSS-E, BATMAN-A):
    - Query sanitization prevents search injection
    - Retry logic with exponential backoff for network blips
    - Modern `asyncio.to_thread` instead of deprecated `get_event_loop()`
    - Deterministic chunk IDs via centralized utils
    - Correlation ID tracing for distributed debugging
    - Lazy import for optional duckduckgo-search dependency
    """

    def __init__(
        self,
        max_results: int = DEFAULT_MAX_RESULTS,
        retry_attempts: int = _MAX_SEARCH_RETRIES,
    ):
        self.max_results = min(max_results, _MAX_RESULTS_CAP)
        self.retry_attempts = retry_attempts

    def search(
        self,
        query: str,
        max_results: int | None = None,
        correlation_id: Optional[str] = None,
    ) -> WebSearchResult:
        """
        Search the web and return results as LangChain Documents.
        DVMELTSS-E: Graceful fallbacks, retry logic, safe query handling.
        """
        corr_id = correlation_id or "unknown"
        safe_query = _sanitize_query(query)
        n = min(max_results or self.max_results, _MAX_RESULTS_CAP)

        if not safe_query:
            logger.warning(f"[{corr_id}] Web search skipped: empty query after sanitization")
            return WebSearchResult(query=query, documents=[], result_count=0)

        # BATMAN-T: Retry with exponential backoff
        last_error = None
        for attempt in range(self.retry_attempts + 1):
            try:
                # FIXED: Lazy import with clear error message
                from duckduckgo_search import DDGS

                # Context manager ensures proper connection cleanup
                with DDGS() as ddgs:
                    raw_results = list(ddgs.text(safe_query, max_results=n))
                break  # Success
            except ImportError:
                logger.error("duckduckgo-search package not installed. Install with: pip install duckduckgo-search")
                return WebSearchResult(query=query, documents=[], result_count=0)
            except Exception as e:
                last_error = e
                if attempt < self.retry_attempts:
                    wait = _RETRY_DELAY_BASE * (2**attempt)
                    logger.warning(f"[{corr_id}] Web search attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                    # FIXED: Use time.sleep only in sync method (acceptable here)
                    import time

                    time.sleep(wait)
                else:
                    logger.error(f"[{corr_id}] Web search failed after {self.retry_attempts+1} attempts: {e}")
                    return WebSearchResult(query=query, documents=[], result_count=0)

        # Convert raw results to LangChain Documents
        documents = []
        for result in raw_results:
            title = result.get("title", "").strip()
            body = result.get("body", "").strip()
            href = result.get("href", "").strip()

            if not body:
                continue

            # Format content for LLM context
            content = f"[WEB SOURCE: {escape_prompt_content(title)}]\n{escape_prompt_content(body)}"

            doc = Document(
                page_content=content,
                metadata={
                    "source_file": f"web:{href}",
                    "page_number": 0,
                    # FIXED: Use centralized ID generator
                    "chunk_id": generate_web_result_id(href, safe_query),
                    "parent_id": "",
                    "block_type": "web_result",
                    "language": "en",
                    "ocr_confidence": 1.0,
                    "chunk_type": "child",
                    "ingest_timestamp": "",
                    "document_type": "web",
                    "char_count": len(content),
                    "web_title": title,
                    "web_url": href,
                    "web_query": safe_query,
                },
            )
            documents.append(doc)

        logger.info(f"[{corr_id}] WebSearch: '{safe_query[:50]}' -> {len(documents)} results")
        return WebSearchResult(
            query=query,
            documents=documents,
            result_count=len(documents),
        )

    async def search_async(
        self,
        query: str,
        max_results: int | None = None,
        correlation_id: Optional[str] = None,
    ) -> WebSearchResult:
        """
        Async wrapper — runs blocking search in thread executor.
        BATMAN-A: Uses modern `asyncio.to_thread` + non-blocking retry.
        """
        corr_id = correlation_id or "unknown"
        safe_query = _sanitize_query(query)
        n = min(max_results or self.max_results, _MAX_RESULTS_CAP)

        if not safe_query:
            logger.warning(f"[{corr_id}] Web search skipped: empty query after sanitization")
            return WebSearchResult(query=query, documents=[], result_count=0)

        # FIXED: Retry logic with asyncio.sleep (non-blocking)
        last_error = None
        for attempt in range(self.retry_attempts + 1):
            try:
                # FIXED: Lazy import with clear error message
                from duckduckgo_search import DDGS

                # Run blocking DDGS call in thread pool
                raw_results = await asyncio.to_thread(lambda: list(DDGS().text(safe_query, max_results=n)))
                break  # Success
            except ImportError:
                logger.error("duckduckgo-search package not installed. Install with: pip install duckduckgo-search")
                return WebSearchResult(query=query, documents=[], result_count=0)
            except Exception as e:
                last_error = e
                if attempt < self.retry_attempts:
                    wait = _RETRY_DELAY_BASE * (2**attempt)
                    logger.warning(f"[{corr_id}] Web search attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                    # FIXED: Non-blocking sleep for async context
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[{corr_id}] Web search failed after {self.retry_attempts+1} attempts: {e}")
                    return WebSearchResult(query=query, documents=[], result_count=0)

        # Convert raw results to LangChain Documents (same as sync method)
        documents = []
        for result in raw_results:
            title = result.get("title", "").strip()
            body = result.get("body", "").strip()
            href = result.get("href", "").strip()

            if not body:
                continue

            content = f"[WEB SOURCE: {escape_prompt_content(title)}]\n{escape_prompt_content(body)}"

            doc = Document(
                page_content=content,
                metadata={
                    "source_file": f"web:{href}",
                    "page_number": 0,
                    "chunk_id": generate_web_result_id(href, safe_query),
                    "parent_id": "",
                    "block_type": "web_result",
                    "language": "en",
                    "ocr_confidence": 1.0,
                    "chunk_type": "child",
                    "ingest_timestamp": "",
                    "document_type": "web",
                    "char_count": len(content),
                    "web_title": title,
                    "web_url": href,
                    "web_query": safe_query,
                },
            )
            documents.append(doc)

        logger.info(f"[{corr_id}] WebSearch: '{safe_query[:50]}' -> {len(documents)} results")
        return WebSearchResult(
            query=query,
            documents=documents,
            result_count=len(documents),
        )


# DVMELTSS-M: Explicit module exports
__all__ = ["WebSearcher", "WebSearchResult"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
