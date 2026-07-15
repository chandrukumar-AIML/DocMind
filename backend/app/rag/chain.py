
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from typing import AsyncIterator, Optional, Any, List, Dict, Tuple, Union

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from app.vectorstore.store_manager import VectorStoreManager

from app.config import get_settings
from app.core.llm_pool import get_llm
from app.core.retry import RetryConfig
from app.core.rag_utils import (
    escape_prompt_content,
    generate_rag_correlation_id,
    build_safe_context,
)
from app.core.openai_errors import is_insufficient_quota_error
from app.core.exceptions import RAGChainError
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

from .hyde import HyDEExpander
from .hybrid_search import HybridSearcher
from .reranker import get_reranker
from .prompts import ANSWER_PROMPT, CONDENSE_QUESTION_PROMPT

logger = logging.getLogger(__name__)

_MAX_ANSWER_LENGTH: int = 8000  # ~2000 tokens max

# Module-level circuit breakers — shared across all AdvancedRAGChain instances.
# Opens after 5 consecutive LLM/embedding failures; probes again after 60 s.
_llm_breaker = CircuitBreaker(name="rag-llm", failure_threshold=5, reset_timeout_s=60.0)
_embedding_breaker = CircuitBreaker(name="rag-embedding", failure_threshold=5, reset_timeout_s=60.0)


@dataclass
class Citation:
    """Structured citation for RAG responses from local document chunks."""

    source_file: str
    page_number: int
    block_type: str
    chunk_text: str
    rerank_score: float
    chunk_id: Optional[str] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to API-friendly dict with truncated text."""
        return {
            "type": "document",
            "source_file": self.source_file,
            "page_number": self.page_number + 1,  # 1-indexed for UI
            "block_type": self.block_type,
            "chunk_text": self.chunk_text[:200] + ("..." if len(self.chunk_text) > 200 else ""),
            "rerank_score": round(self.rerank_score, 4),
            "chunk_id": self.chunk_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict, correlation_id: Optional[str] = None) -> "Citation":
        """✅ NEW: Safe factory method with defaults for missing keys."""
        return cls(
            source_file=data.get("source_file", "unknown"),
            page_number=int(data.get("page_number", 0)),
            block_type=data.get("block_type", "text"),
            chunk_text=data.get("chunk_text", ""),
            rerank_score=float(data.get("rerank_score", 0.0)),
            chunk_id=data.get("chunk_id"),
            correlation_id=correlation_id or data.get("correlation_id"),
        )

    @classmethod
    def from_metadata(cls, metadata: dict, chunk_text: str = "", rerank_score: float = 0.0,
                      correlation_id: Optional[str] = None) -> "Citation":
        """Build a Citation from document chunk metadata. Returns WebCitation for web: sources."""
        source = metadata.get("source_file", "unknown")
        if source.startswith("web:"):
            url = source[4:]  # strip "web:" prefix
            return WebCitation(  # type: ignore[return-value]
                url=url,
                title=metadata.get("title", url),
                chunk_text=chunk_text[:200],
                rerank_score=rerank_score,
                chunk_id=metadata.get("chunk_id"),
                correlation_id=correlation_id,
            )
        return cls(
            source_file=source,
            page_number=int(metadata.get("page_number", 0)),
            block_type=metadata.get("block_type", "text"),
            chunk_text=chunk_text,
            rerank_score=rerank_score,
            chunk_id=metadata.get("chunk_id"),
            correlation_id=correlation_id,
        )


@dataclass
class WebCitation:
    """
    Citation for web search results — distinct from document citations.

    Web results do not have page numbers or PDF highlights; they carry a URL
    and an optional page title. Keeping them separate prevents the UI from
    trying to render non-existent PDF anchors for web-sourced answers.
    """

    url: str
    title: str
    chunk_text: str
    rerank_score: float
    chunk_id: Optional[str] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": "web",
            "url": self.url,
            "title": self.title,
            "chunk_text": self.chunk_text[:200] + ("..." if len(self.chunk_text) > 200 else ""),
            "rerank_score": round(self.rerank_score, 4),
            "chunk_id": self.chunk_id,
            "correlation_id": self.correlation_id,
        }


@dataclass
class RAGResponse:
    """Complete RAG response with metadata for monitoring."""

    answer: str
    citations: List[Union[Citation, "WebCitation"]]
    hyde_hypothesis: str
    retrieved_count: int
    reranked_count: int
    query: str
    faithfulness_score: Optional[float] = None
    answer_relevance_score: Optional[float] = None
    context_precision_score: Optional[float] = None
    latency_ms: Optional[float] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to API response format."""
        return {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "hyde_hypothesis": self.hyde_hypothesis,
            "retrieved_count": self.retrieved_count,
            "reranked_count": self.reranked_count,
            "query": self.query,
            "scores": {
                "faithfulness": self.faithfulness_score,
                "answer_relevance": self.answer_relevance_score,
                "context_precision": self.context_precision_score,
            },
            "latency_ms": self.latency_ms,
            "correlation_id": self.correlation_id,
        }


@dataclass
class RetrievalResult:
    """Intermediate retrieval state for debugging/telemetry."""

    standalone_question: str
    hypothesis: str
    candidate_docs: List[Any]
    expanded_docs: List[Any]
    reranked: List[Tuple[Any, float]]
    context: str
    citations: List[Citation]
    timings: Dict[str, float] = field(default_factory=dict)
    correlation_id: Optional[str] = None


class AdvancedRAGChain:
    """
    Full advanced RAG pipeline:
    HyDE -> hybrid search -> parent expansion -> cross-encoder rerank
    -> GPT-4o streaming answer with citations.

    Features (DVMELTSS-V, BATMAN-A, OWASP-1):
    - Centralized LLM pool via app.core.llm_pool
    - Async streaming with token-by-token yield + timeout guards
    - Prompt injection protection via centralized escaping
    - Comprehensive timing metrics + correlation ID tracing
    - Fallback to extractive answers when LLM unavailable
    """

    def __init__(
        self,
        store_manager: Optional[VectorStoreManager] = None,
        use_gpu: bool = False,
        correlation_id: Optional[str] = None,
    ):
        settings = get_settings()
        self.store = store_manager or VectorStoreManager()
        self.hyde = HyDEExpander(model=settings.openai_chat_model)
        self.searcher = HybridSearcher(store_manager=self.store)
        # Reranking toggle — when disabled (low-RAM hosts), skip the cross-encoder
        # entirely so PyTorch model weights never load. Retrieval RRF scores are used.
        self.rerank_enabled = settings.rerank_enabled
        self._reranker = None  # Lazy — only instantiated when first needed
        self._bm25_ready = False
        try:
            self.correlation_id = correlation_id or generate_rag_correlation_id()
        except Exception:
            self.correlation_id = f"rag_{int(time.time())}"

        self.llm = get_llm(streaming=True, temperature_override=0.1)
        self.condenser_llm = get_llm(streaming=False, temperature_override=0.0)

        self._llm_retry_config = RetryConfig(
            max_attempts=3,
            backoff_base=1.0,
            exceptions=(Exception,),
        )

        logger.info(f"AdvancedRAGChain initialized | corr_id={self.correlation_id}")

    @property
    def reranker(self):
        """Lazy reranker — only instantiated on first access (when rerank is enabled)."""
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker

    async def initialize(self) -> None:
        """
        Async startup — build BM25 index without blocking constructor.
        ✅ FIXED: Idempotent — skips if already initialized.
        """
        if self._bm25_ready:
            logger.debug(f"[{self.correlation_id}] BM25 already initialized — skipping")
            return

        try:
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
            await loop.run_in_executor(None, self.searcher.build_bm25_from_store)
            self._bm25_ready = True
            logger.info(f"[{self.correlation_id}] BM25 index ready.")
        except Exception as e:
            logger.warning(f"[{self.correlation_id}] BM25 init failed: {e}. Hybrid search uses FAISS only.")

    def rebuild_bm25(self, source_file: Optional[str] = None) -> None:
        """Manually trigger BM25 rebuild for a specific document or all."""
        self.searcher.build_bm25_from_store(source_file=source_file)
        self._bm25_ready = True
        logger.info(f"[{self.correlation_id}] BM25 rebuilt for {source_file or 'all documents'}")

    async def _stream_llm(self, messages: List[BaseMessage]) -> AsyncIterator[Any]:
        """Stream LLM response through circuit breaker."""
        async with _llm_breaker:
            async for chunk in self.llm.astream(messages):
                yield chunk

    async def _invoke_llm(self, messages: List[BaseMessage]) -> Any:
        """Invoke LLM through circuit breaker. Skips immediately if quota exceeded or circuit open."""
        from app.core.openai_errors import (
            is_openai_available,
            is_authentication_error,
            mark_openai_quota_exceeded,
            mark_openai_auth_failed,
        )

        if not is_openai_available():
            raise RuntimeError("LLM skipped — OpenAI quota/auth previously exceeded")
        async with _llm_breaker:
            try:
                return await asyncio.wait_for(self.llm.ainvoke(messages), timeout=15.0)
            except Exception as e:
                if is_insufficient_quota_error(e):
                    mark_openai_quota_exceeded()
                    raise RuntimeError(f"LLM quota exceeded: {e}") from e
                if is_authentication_error(e):
                    mark_openai_auth_failed()
                    raise RuntimeError(f"LLM auth failed: {e}") from e
                raise

    async def stream(
        self,
        question: str,
        chat_history: Optional[List[Union[dict, BaseMessage]]] = None,
        filter_dict: Optional[Dict[str, Any]] = None,
        top_k_retrieve: int = 20,
        top_k_rerank: int = 3,
        timeout_seconds: int = 120,
        correlation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Stream RAG response token-by-token with metadata chunks.
        ✅ FIXED: Proper async iterator handling + memory guard.

        workspace_id: see query() — required for multi-tenant isolation since this
        chain instance is shared across all workspaces.
        """
        corr_id = correlation_id or thread_id or self.correlation_id
        start_time = time.perf_counter()
        store, searcher = self._resolve_workspace_store(workspace_id)

        try:
            yield {"type": "status", "content": "searching"}

            result = await asyncio.wait_for(
                self._run_retrieval_pipeline(
                    question,
                    chat_history or [],
                    filter_dict,
                    top_k_retrieve,
                    top_k_rerank,
                    corr_id,
                    store=store,
                    searcher=searcher,
                ),
                timeout=timeout_seconds * 0.7,
            )

            # Low-confidence detection: no docs found or all rerank scores weak
            max_score = max((s for _, s in result.reranked), default=0.0) if result.reranked else 0.0
            low_confidence = len(result.reranked) == 0 or max_score < 0.1

            yield {"type": "status", "content": "generating"}

            messages = ANSWER_PROMPT.format_messages(
                context=escape_prompt_content(result.context),
                question=escape_prompt_content(result.standalone_question),
                chat_history=chat_history or [],
            )

            full_answer = ""
            try:
                async for chunk in self._stream_llm(messages):
                    if chunk.content:
                        content_str = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                        if len(full_answer) + len(content_str) > _MAX_ANSWER_LENGTH:
                            logger.warning(f"[{corr_id}] Answer truncated at {_MAX_ANSWER_LENGTH} chars")
                            yield {
                                "type": "token",
                                "content": content_str[: _MAX_ANSWER_LENGTH - len(full_answer)]
                                + "\n\n[Response truncated]",
                            }
                            break
                        full_answer += content_str
                        yield {"type": "token", "content": content_str}

            except asyncio.TimeoutError:
                logger.warning(f"[{corr_id}] LLM streaming timed out")
                yield {
                    "type": "token",
                    "content": "[Response truncated due to timeout]",
                }
            except Exception as e:
                logger.warning(f"[{corr_id}] LLM streaming failed: {e}")
                fallback = self._generate_fallback_answer(result.reranked, result.standalone_question)
                yield {"type": "token", "content": fallback}
                full_answer = fallback

            yield {
                "type": "citations",
                "content": [c.to_dict() for c in result.citations],
            }

            latency = round((time.perf_counter() - start_time) * 1000)
            yield {
                "type": "done",
                "latency_ms": latency,
                "correlation_id": corr_id,
                "low_confidence": low_confidence,
                "retrieved_count": len(result.candidate_docs),
                "reranked_count": len(result.reranked),
            }

        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] RAG stream timed out after {timeout_seconds}s")
            yield {
                "type": "error",
                "message": f"Request timed out after {timeout_seconds}s",
                "correlation_id": corr_id,
            }
        except Exception as e:
            logger.error(f"[{corr_id}] RAG stream failed: {e}", exc_info=True)
            yield {"type": "error", "message": str(e), "correlation_id": corr_id}

    async def query(
        self,
        question: str,
        chat_history: Optional[List[Union[dict, BaseMessage]]] = None,
        filter_dict: Optional[Dict[str, Any]] = None,
        top_k_retrieve: int = 20,
        top_k_rerank: int = 3,
        timeout_seconds: int = 120,
        correlation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> RAGResponse:
        """Non-streaming RAG query returning complete response object.

        workspace_id: when given, retrieval is scoped to that workspace's own
        Chroma collection + FAISS index + BM25 cache instead of this chain's
        construction-time default store — required for multi-tenant isolation
        since this chain instance is shared across all workspaces.
        """
        corr_id = correlation_id or thread_id or self.correlation_id
        start_time = time.perf_counter()
        store, searcher = self._resolve_workspace_store(workspace_id)

        try:
            result = await asyncio.wait_for(
                self._run_retrieval_pipeline(
                    question,
                    chat_history or [],
                    filter_dict,
                    top_k_retrieve,
                    top_k_rerank,
                    corr_id,
                    store=store,
                    searcher=searcher,
                ),
                timeout=timeout_seconds * 0.7,
            )

            messages = ANSWER_PROMPT.format_messages(
                context=escape_prompt_content(result.context),
                question=escape_prompt_content(result.standalone_question),
                chat_history=chat_history or [],
            )

            try:
                response = await asyncio.wait_for(
                    self._invoke_llm(messages),  # ✅ FIXED: Use dedicated retry method
                    timeout=timeout_seconds * 0.3,
                )
                answer_str = response.content if isinstance(response.content, str) else str(response.content)
            except Exception as e:
                logger.warning(f"[{corr_id}] LLM batch failed: {e}")
                answer_str = self._generate_fallback_answer(result.reranked, result.standalone_question)

            latency = round((time.perf_counter() - start_time) * 1000)

            return RAGResponse(
                answer=answer_str,
                citations=result.citations,
                hyde_hypothesis=result.hypothesis,
                retrieved_count=len(result.candidate_docs),
                reranked_count=len(result.reranked),
                query=result.standalone_question,
                latency_ms=latency,
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] RAG query failed: {e}")
            raise

    def _resolve_workspace_store(self, workspace_id: Optional[str]):
        """Resolve the (store, searcher) pair to use for a query.

        Without workspace_id, returns this chain's construction-time default
        (self.store, self.searcher) — unchanged behavior. With workspace_id, resolves
        a cached workspace-scoped VectorStoreManager (app.dependencies) and builds a
        lightweight HybridSearcher over it, including a workspace-scoped BM25 cache
        path, so keyword-search hits can't return another workspace's documents.
        """
        if not workspace_id:
            return self.store, self.searcher

        from app.dependencies import get_store_manager_for_workspace
        from app.core.workspace_utils import get_bm25_index_path
        from app.rag.hybrid_search import HybridSearcher

        store = get_store_manager_for_workspace(workspace_id)
        searcher = HybridSearcher(
            store_manager=store,
            semantic_weight=self.searcher.semantic_weight,
            keyword_weight=self.searcher.keyword_weight,
            bm25_cache_path=get_bm25_index_path(workspace_id),
        )
        return store, searcher

    async def _run_retrieval_pipeline(
        self,
        question: str,
        chat_history: List[Union[dict, BaseMessage]],
        filter_dict: Optional[Dict[str, Any]],
        top_k_retrieve: int,
        top_k_rerank: int,
        correlation_id: str,
        store: Optional[VectorStoreManager] = None,
        searcher: Optional[Any] = None,
    ) -> RetrievalResult:
        """Core retrieval pipeline with correlation ID propagation."""
        store = store or self.store
        searcher = searcher or self.searcher
        if not question or not question.strip():
            raise RAGChainError("Question cannot be empty")
        if top_k_rerank > top_k_retrieve:
            raise RAGChainError(f"top_k_rerank ({top_k_rerank}) cannot exceed top_k_retrieve ({top_k_retrieve})")

        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
        timings: Dict[str, float] = {}

        # Stage 1: Condense question
        t0 = time.perf_counter()
        standalone_q = await self._condense_question(question, chat_history, correlation_id)
        timings["condense_ms"] = round((time.perf_counter() - t0) * 1000)

        # Stage 2: HyDE hypothesis
        t1 = time.perf_counter()
        hypothesis = await loop.run_in_executor(None, self.hyde.expand, standalone_q)
        timings["hyde_ms"] = round((time.perf_counter() - t1) * 1000)

        # Stage 3: Hybrid search (vector embedding via circuit-breaker-guarded path)
        t2 = time.perf_counter()
        async with _embedding_breaker:
            candidates = await loop.run_in_executor(
                None,
                partial(
                    searcher.search,
                    query=standalone_q,
                    k=top_k_retrieve,
                    filter_dict=filter_dict,
                    hyde_query=hypothesis,
                    correlation_id=correlation_id,
                ),
            )
        timings["search_ms"] = round((time.perf_counter() - t2) * 1000)

        candidate_docs = [doc for doc, _ in candidates]
        expanded_docs = await loop.run_in_executor(
            None, partial(self._expand_to_parents, candidate_docs, store=store)
        )

        # Stage 4: Reranking (toggle-gated — skipped on low-RAM hosts)
        t3 = time.perf_counter()
        if self.rerank_enabled:
            reranked = await loop.run_in_executor(
                None,
                partial(
                    self.reranker.rerank,
                    query=standalone_q,
                    documents=expanded_docs,
                    top_k=top_k_rerank,
                    correlation_id=correlation_id,
                ),
            )
        else:
            # Reranker disabled — keep retrieval order, pair with descending
            # placeholder scores so downstream code that expects (doc, score) works.
            n = len(expanded_docs[:top_k_rerank])
            reranked = [(doc, 1.0 - (i / max(n, 1))) for i, doc in enumerate(expanded_docs[:top_k_rerank])]
        timings["rerank_ms"] = round((time.perf_counter() - t3) * 1000)

        logger.info(
            f"[{correlation_id}] Pipeline: condense={timings['condense_ms']}ms | "
            f"hyde={timings['hyde_ms']}ms | search={timings['search_ms']}ms | "
            f"rerank={timings['rerank_ms']}ms"
        )

        if not reranked:
            logger.warning(f"[{correlation_id}] Reranker returned 0 results")
            fallback = expanded_docs[:top_k_rerank]
            reranked = [(doc, 0.0) for doc in fallback]

        try:
            context, citation_dicts = build_safe_context(reranked)
            # ✅ Convert dicts to Citation objects safely
            citations = [
                Citation.from_metadata(
                    metadata=c,
                    chunk_text=c.get("chunk_text", ""),
                    rerank_score=float(c.get("rerank_score", 0.0)),
                    correlation_id=correlation_id,
                )
                for c in citation_dicts
                if isinstance(c, dict)
            ]
        except Exception as e:
            logger.error(f"[{correlation_id}] Context building failed: {e}")
            context = "<document_context>\nContext unavailable.\n</document_context>"
            citations = []

        return RetrievalResult(
            standalone_question=standalone_q,
            hypothesis=hypothesis,
            candidate_docs=candidate_docs,
            expanded_docs=expanded_docs,
            reranked=reranked,
            context=context,
            citations=citations,
            timings=timings,
            correlation_id=correlation_id,
        )

    async def _condense_question(self, question: str, chat_history: List[Any], corr_id: str) -> str:
        """Rephrase follow-up question using chat history context."""
        if not chat_history:
            return question
        try:
            # ✅ Convert dict messages to LangChain objects if needed
            lc_history = []
            for msg in chat_history:
                if isinstance(msg, dict):
                    role = msg.get("role", "human")
                    content = msg.get("content", "")
                    if role == "human":
                        lc_history.append(HumanMessage(content=content))
                    elif role == "ai":
                        lc_history.append(AIMessage(content=content))
                    elif role == "system":
                        lc_history.append(SystemMessage(content=content))
                elif isinstance(msg, BaseMessage):
                    lc_history.append(msg)

            messages = CONDENSE_QUESTION_PROMPT.format_messages(
                chat_history=lc_history, question=escape_prompt_content(question)
            )
            response = await self.condenser_llm.ainvoke(messages)
            content_str = response.content if isinstance(response.content, str) else str(response.content)
            return content_str.strip()
        except Exception as e:
            logger.warning(f"[{corr_id}] Question condensing failed: {e}")
            return question

    def _generate_fallback_answer(self, reranked: List[Tuple[Any, float]], question: str) -> str:
        """Generate extractive answer when LLM is unavailable."""
        if not reranked:
            return "I couldn't find relevant indexed text for that question."

        snippets = []
        query_terms = set(t.lower() for t in question.split() if len(t) > 2)

        for doc, score in reranked[:3]:
            text = " ".join(doc.page_content.split())
            if not text:
                continue
            first_sentence = text.split(". ")[0][:260].strip()
            overlap = sum(1 for term in query_terms if term in text.lower())
            snippets.append((overlap + max(score, 0), first_sentence, doc.metadata))

        snippets.sort(key=lambda item: item[0], reverse=True)

        lines = ["Extractive answer from indexed text:"]
        for _, snippet, meta in snippets[:2]:
            page = int(meta.get("page_number", 0)) + 1
            source = meta.get("source_file", "document")
            lines.append(f"- {snippet} [SOURCE: {source}, page {page}]")

        return "\n".join(lines)

    def _expand_to_parents(self, docs: List[Any], store: Optional[VectorStoreManager] = None) -> List[Any]:
        """Expand child chunks to parent documents with deduplication."""
        store = store or self.store
        expanded = []
        seen_ids: set[str] = set()

        for doc in docs:
            meta = getattr(doc, "metadata", {})
            parent_id = meta.get("parent_id", "")
            chunk_id = meta.get("chunk_id", "")

            if parent_id and parent_id not in seen_ids:
                parent_text = store.get_parent(parent_id)
                if parent_text:
                    parent_doc = Document(
                        page_content=parent_text,
                        metadata={**meta, "chunk_type": "parent_expanded"},
                    )
                    expanded.append(parent_doc)
                    seen_ids.add(parent_id)
                    seen_ids.add(chunk_id)
                    continue

            if chunk_id and chunk_id not in seen_ids:
                expanded.append(doc)
                seen_ids.add(chunk_id)
            elif not chunk_id:
                expanded.append(doc)

        logger.debug(f"Parent expansion: {len(docs)} -> {len(expanded)} contexts")
        return expanded

    def get_pipeline_stats(self) -> dict:
        """Return pipeline configuration for monitoring."""
        return {
            "bm25_ready": self._bm25_ready,
            # Avoid touching self.reranker when disabled — that would force a lazy load
            "reranker_info": (
                self.reranker.get_model_info()
                if self.rerank_enabled and hasattr(self.reranker, "get_model_info")
                else "disabled"
            ),
            "llm_model": getattr(self.llm, "model_name", "unknown"),
            "correlation_id": self.correlation_id,
        }


# DVMELTSS-M: Explicit module exports
__all__ = ["AdvancedRAGChain", "Citation", "RAGResponse", "RetrievalResult"]


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.rag.chain) ------------
# ========================================================================

