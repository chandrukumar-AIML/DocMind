from __future__ import annotations
import asyncio
import logging
from typing import Optional, Callable, List

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.llm_pool import get_llm
from app.core.retry import retry_async, RetryConfig
from app.core.rag_utils import escape_prompt_content, generate_rag_correlation_id
from app.core.openai_errors import is_insufficient_quota_error

from .prompts import HYDE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class HyDEExpander:
    """
    Hypothetical Document Embedding (HyDE) query expander.

    Features (DVMELTSS-V, BATMAN-A, OWASP-1):
    - Centralized LLM client via app.core.llm_pool
    - Async support with retry logic
    - Prompt injection protection via escaping
    - Correlation ID tracing for audit trails
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        temperature: float = 0.1,
        max_tokens: int = 200,
        max_retries: int = 3,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ):
        settings = get_settings()
        self.correlation_id = correlation_id or generate_rag_correlation_id("hyde")

        self.llm = get_llm(
            streaming=False,
            model_override=model,
            temperature_override=temperature,
        )
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._local_fallback = not bool(settings.openai_api_key)

        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=max_retries,
                backoff_base=1.0,
                exceptions=(Exception,),
            )
        )

        logger.info(f"HyDEExpander initialized | model={model} | corr_id={self.correlation_id}")

    async def expand_async(
        self,
        query: str,
        document_context: Optional[str] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> str:
        """Async version: Generate hypothetical passage for the query."""
        corr_id = correlation_id or self.correlation_id

        from app.core.openai_errors import is_openai_available

        if self._local_fallback or not is_openai_available():
            logger.debug(f"[{corr_id}] Using local fallback for HyDE (quota/flag)")
            return self._local_expand(query)

        safe_query = escape_prompt_content(query)
        safe_context = escape_prompt_content(document_context[:500]) if document_context else None

        user_prompt = f"Question: {safe_query}"
        if safe_context:
            user_prompt = f"Document context: {safe_context}...\n\n{user_prompt}"

        try:
            response = await asyncio.wait_for(
                self.llm.ainvoke(
                    [
                        {"role": "system", "content": HYDE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ]
                ),
                timeout=8.0,
            )
            content = response.content if hasattr(response, "content") else str(response)
            hypothesis = content.strip() if content else ""
            if hypothesis:
                logger.info(f"[{corr_id}] HyDE: '{query[:60]}' -> '{hypothesis[:80]}'")
                return hypothesis
            logger.warning(f"[{corr_id}] HyDE returned empty content")
            return query
        except Exception as e:
            if is_insufficient_quota_error(e):
                self._local_fallback = True
                logger.warning(f"[{corr_id}] HyDE: quota exceeded, using fallback")
            else:
                logger.warning(f"[{corr_id}] HyDE failed: {type(e).__name__}: {e}")
            return self._local_expand(query)

    def expand(self, query: str, document_context: Optional[str] = None) -> str:
        """Sync wrapper — prefers async version in new code."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(self.expand_async(query, document_context), loop).result()
        except RuntimeError:
            return asyncio.run(self.expand_async(query, document_context))

    async def expand_multi_embedding_async(
        self,
        query: str,
        embed_fn: Callable[[str], List[float]],
        n: int = 3,
        correlation_id: Optional[str] = None,
    ) -> List[float]:
        """Async: Generate N hypotheses and return averaged embedding."""
        import numpy as np

        corr_id = correlation_id or self.correlation_id

        if self._local_fallback:
            return embed_fn(query)

        # Generate hypotheses concurrently
        hypotheses = await asyncio.gather(*[self.expand_async(query, correlation_id=corr_id) for _ in range(n)])
        embeddings = [embed_fn(h) for h in hypotheses]

        avg_vector = np.mean(embeddings, axis=0).tolist()
        logger.debug(f"[{corr_id}] Multi-HyDE: {n} hypotheses averaged")
        return avg_vector

    def _local_expand(self, query: str) -> str:
        """Local fallback: simple query reformulation."""
        expansions = {
            "what": "definition of",
            "how": "method for",
            "why": "reasons for",
            "when": "timeline of",
            "where": "location of",
        }
        words = query.lower().split()
        if words and words[0] in expansions:
            return f"{expansions[words[0]]} {query}"
        return f"information about {query}"


# DVMELTSS-M: Explicit module exports
__all__ = ["HyDEExpander"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.rag.hyde) ------------
# ========================================================================

