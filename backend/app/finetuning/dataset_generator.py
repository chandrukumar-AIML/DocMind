# backend/app/finetuning/dataset_generator.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, A - Async
# BATMAN-FIX: A - True async, T - Batch processing, M - Memory safety
# OWASP-FIX: 1 - Prompt escaping, 7 - Safe data handling

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional

from langchain_core.documents import Document
from pydantic import BaseModel, ValidationError, Field, ConfigDict

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.finetune_utils import (
    generate_finetune_correlation_id,
    validate_domain,
)
from app.core.retry import retry_async, RetryConfig
from app.core.prompts import escape_prompt_content
from app.core.openai_errors import classify_openai_error

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-M) -------------------------
# ========================================================================

# Valid domains for query generation
_VALID_DOMAINS: Final = frozenset({"legal", "medical", "invoice", "general"})

# DVMELTSS-V: Query length constraints
_MIN_QUERY_LENGTH: Final = 5
_MAX_QUERY_LENGTH: Final = 100
_QUERIES_PER_CHUNK: Final = 2

# BATMAN-A: Token safety limits
_MAX_PROMPT_TOKENS: Final = 6000
_MAX_CHUNK_TEXT: Final = 600

# DVMELTSS-E: Retry configuration
_MAX_RETRIES: Final = 3
_RETRY_BASE_DELAY: Final = 1.0
_RETRY_MAX_DELAY: Final = 30.0


# DVMELTSS-V: Pydantic schemas for structured output
# FIXED: Pydantic v2 — use model_config = ConfigDict() instead of class Config
class QueryGenerationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(..., min_length=1, max_length=10)


class HardNegativeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hardest_negative_index: int = Field(..., ge=0)
    reason: str = Field(..., max_length=200)


# ========================================================================
# -- IMMUTABLE DATA MODELS (DVMELTSS-M, V) ------------------------------
# ========================================================================


@dataclass  # FIXED: Removed frozen=True for Pydantic v2 compatibility
class TrainingTriplet:
    """
    Training example for embedding fine-tuning.
    FIXED: Not frozen to allow safe mutation in __post_init__.
    """

    id: str
    anchor: str  # query/question
    positive: str  # relevant document chunk
    negative: str  # hard negative chunk
    domain: str
    source_file: str
    correlation_id: Optional[str] = None  # FIXED: Added for tracing

    def __post_init__(self):
        # DVMELTSS-V: Validate domain
        if self.domain not in _VALID_DOMAINS:
            self.domain = "general"  # Direct assignment (not frozen)
        # Clamp text lengths
        if len(self.anchor) > _MAX_QUERY_LENGTH:
            self.anchor = self.anchor[:_MAX_QUERY_LENGTH]
        if len(self.positive) > 512:
            self.positive = self.positive[:512]
        if len(self.negative) > 512:
            self.negative = self.negative[:512]

    def to_dict(self) -> dict:
        """Serialize for JSONL storage."""
        return {
            "id": self.id,
            "anchor": self.anchor,
            "positive": self.positive,
            "negative": self.negative,
            "domain": self.domain,
            "source_file": self.source_file,
            "correlation_id": self.correlation_id,  # FIXED: Include in output
        }


@dataclass
class TripletDataset:
    """Collection of training triplets with metadata."""

    domain: str
    triplets: list[TrainingTriplet] = field(default_factory=list)
    version: str = ""
    created_at: str = ""
    correlation_id: Optional[str] = None  # FIXED: Added for tracing

    @property
    def size(self) -> int:
        return len(self.triplets)

    def to_sentence_transformers_format(self) -> list[dict]:
        """Convert to sentence-transformers InputExample format."""
        return [{"texts": [t.anchor, t.positive, t.negative], "label": 1.0} for t in self.triplets]

    def train_val_split(self, val_ratio: float = 0.1) -> tuple["TripletDataset", "TripletDataset"]:
        """Split into train and validation sets."""
        shuffled = self.triplets.copy()
        random.shuffle(shuffled)
        n_val = max(1, int(len(shuffled) * val_ratio))
        train = TripletDataset(
            domain=self.domain,
            triplets=shuffled[n_val:],
            version=self.version,
            created_at=self.created_at,
            correlation_id=self.correlation_id,
        )
        val = TripletDataset(
            domain=self.domain,
            triplets=shuffled[:n_val],
            version=self.version,
            created_at=self.created_at,
            correlation_id=self.correlation_id,
        )
        return train, val

    def save(self, path: str | Path) -> Path:
        """Save dataset to JSONL file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for t in self.triplets:
                f.write(json.dumps(t.to_dict(), ensure_ascii=False) + "\n")
        logger.info(f"Dataset saved: {path} ({self.size} triplets)")
        return path

    @classmethod
    def load(cls, path: str | Path, correlation_id: Optional[str] = None) -> "TripletDataset":
        """Load dataset from JSONL file."""
        path = Path(path)
        triplets = []
        domain = "unknown"
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    domain = d.get("domain", domain)
                    triplets.append(TrainingTriplet(**d, correlation_id=correlation_id))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Skipping malformed line: {e}")
        return cls(domain=domain, triplets=triplets, correlation_id=correlation_id)


# ========================================================================
# -- PROMPT TEMPLATES (OWASP-1: Structured, safe) -----------------------
# ========================================================================

QUERY_GENERATION_PROMPT = """You are building a training dataset for a domain-specific retrieval model.

Given this document chunk from a {domain} document, generate {n_queries} realistic 
search queries that a user would type to find this specific information.

Document chunk:
{chunk_text}

Rules:
- Queries must be naturally phrased (as a real user would type them)
- Queries must be answerable ONLY using this specific chunk
- Vary query types: some keyword-based, some question-based, some conceptual
- Domain-appropriate terminology ({domain})
- Each query: {min_len}-{max_len} words

Return ONLY valid JSON matching this schema:
{{
  "queries": ["query text here", "another query here"]
}}
"""

HARD_NEGATIVE_PROMPT = """Given this query and a correct answer chunk, identify which of 
the candidate chunks is the HARDEST negative — most confusingly similar to the correct 
answer but actually wrong/irrelevant.

Query: {query}

Correct answer chunk: {positive}

Candidate chunks:
{candidates}

Return ONLY valid JSON matching this schema:
{{
  "hardest_negative_index": 2,
  "reason": "mentions similar topics but doesn't answer the query"
}}
"""


# ========================================================================
# -- GENERATOR CLASS (DVMELTSS-V, BATMAN-A, OWASP-1) --------------------
# ========================================================================


class TripletDatasetGenerator:
    """
    Generates training triplets for embedding fine-tuning.

    Features:
    - Centralized async LLM client via app.core.finetune_utils
    - Pydantic structured output validation
    - Hard negative selection via LLM + fallback
    - Memory-safe batch processing
    - Correlation ID tracing for audit trails
    """

    DOMAIN_CONTEXT: Final = {
        "legal": "legal contracts, clauses, agreements",
        "medical": "medical records, clinical notes, diagnoses",
        "invoice": "invoices, purchase orders, financial documents",
        "general": "general business documents",
    }

    def __init__(self, model: str = "gpt-4o", max_retries: int = _MAX_RETRIES):
        settings = get_settings()
        api_key = settings.openai_api_key

        # FIXED: Use centralized OpenAI client with retry config
        from app.core.llm_pool import get_llm

        self.llm = get_llm(streaming=False, temperature_override=0.7)
        self.model = model
        self.max_retries = max_retries

        # FIXED: Centralized retry config
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=max_retries,
                backoff_base=_RETRY_BASE_DELAY,
                backoff_max=_RETRY_MAX_DELAY,
                exceptions=(Exception,),
            )
        )

        logger.info(f"TripletDatasetGenerator initialized: model={model}, async=True")

    def _estimate_tokens(self, text: str) -> int:
        """BATMAN-A: Rough token estimation for prompt safety."""
        return len(text) // 4

    async def _call_llm_with_retry(
        self,
        prompt: str,
        response_schema: type[BaseModel],
        call_type: str,
        correlation_id: str,
        temperature: float = 0.7,
        max_tokens: int = 400,
    ) -> Optional[dict]:
        """DVMELTSS-E: Async LLM call with centralized retry + structured validation."""
        corr_id = correlation_id

        # FIXED: Use centralized prompt escaping
        safe_prompt = escape_prompt_content(prompt)

        if self._estimate_tokens(safe_prompt) > _MAX_PROMPT_TOKENS:
            safe_prompt = safe_prompt[: _MAX_PROMPT_TOKENS * 4]

        @retry_async(
            config=RetryConfig(
                max_attempts=self.max_retries,
                backoff_base=_RETRY_BASE_DELAY,
                backoff_max=_RETRY_MAX_DELAY,
                exceptions=(Exception,),
            )
        )
        async def _do_call():
            return await self.llm.ainvoke([{"role": "user", "content": safe_prompt}])

        try:
            response = await _do_call()
            content = response.content if hasattr(response, "content") else str(response)
            if not content:
                return None

            data = json.loads(content)
            response_schema.model_validate(data)
            return data

        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning(f"[{corr_id}] {call_type} JSON/validation error: {e}")
            return None
        except Exception as e:
            err = classify_openai_error(e)
            if err and err.error_type == "quota":
                logger.warning(f"[{corr_id}] {call_type}: quota exceeded")
                return None
            logger.warning(f"[{corr_id}] {call_type} unexpected error: {type(e).__name__}: {e}")
            return None

    def _get_chunks(
        self,
        workspace_id: str,
        domain: str,
        max_chunks: int,
        correlation_id: str,
    ) -> list[Document]:
        """Get chunks from ChromaDB filtered by domain."""
        from app.vectorstore.chroma_store import _get_chroma_client

        settings = get_settings()
        client = _get_chroma_client(settings.chroma_persist_dir)

        collection_name = f"docs_{workspace_id}"
        try:
            collection = client.get_collection(collection_name)
        except Exception:
            try:
                collection = client.get_collection(settings.chroma_collection_name)
            except Exception:
                logger.warning(f"[{correlation_id}] No collection found for workspace={workspace_id}")
                return []

        # Filter by document type if not "general"
        where = {"document_type": domain} if domain != "general" else None

        try:
            result = collection.get(limit=max_chunks, where=where, include=["documents", "metadatas"])
        except Exception:
            # Retry without filter if no results
            result = collection.get(limit=max_chunks, include=["documents", "metadatas"])

        chunks = [
            Document(page_content=doc, metadata=meta)
            for doc, meta in zip(result.get("documents", []), result.get("metadatas", []))
            if doc and len(doc.strip()) > 50
        ]
        return chunks

    async def _generate_queries_async(
        self,
        chunk_text: str,
        domain: str,
        n_queries: int,
        correlation_id: str,
    ) -> list[str]:
        """Async version: Generate realistic search queries for a chunk."""
        domain_context = self.DOMAIN_CONTEXT.get(domain, domain)
        # FIXED: Use centralized prompt escaping
        safe_chunk = escape_prompt_content(chunk_text[:_MAX_CHUNK_TEXT])

        prompt = QUERY_GENERATION_PROMPT.format(
            domain=domain_context,
            n_queries=n_queries,
            min_len=_MIN_QUERY_LENGTH,
            max_len=_MAX_QUERY_LENGTH,
            chunk_text=safe_chunk,
        )

        try:
            data = await self._call_llm_with_retry(
                prompt=prompt,
                response_schema=QueryGenerationSchema,
                call_type="query_generation",
                correlation_id=correlation_id,
                temperature=0.7,
                max_tokens=300,
            )
            if not data:
                return []

            queries = [
                q.strip()
                for q in data.get("queries", [])
                if q and _MIN_QUERY_LENGTH <= len(q.strip()) <= _MAX_QUERY_LENGTH
            ][:n_queries]
            return queries
        except Exception as e:
            logger.warning(f"[{correlation_id}] Query generation failed: {type(e).__name__}: {e}")
            return []

    async def _find_hard_negative_async(
        self,
        query: str,
        positive_chunk: Document,
        all_chunks: list[Document],
        n_candidates: int = 5,
        correlation_id: str = "",
    ) -> Optional[str]:
        """
        Async version: Find the hardest negative for a (query, positive) pair.
        """
        source = positive_chunk.metadata.get("source_file", "")

        # Same-document candidates (hardest negatives)
        same_doc = [
            c
            for c in all_chunks
            if c.metadata.get("source_file") == source
            and c.page_content != positive_chunk.page_content
            and len(c.page_content) > 30
        ]

        # Cross-document candidates
        cross_doc = [c for c in all_chunks if c.metadata.get("source_file") != source and len(c.page_content) > 30]

        # Prefer same-doc negatives
        candidates = (same_doc + cross_doc)[:n_candidates]
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0].page_content[:512]

        # Ask LLM to pick hardest negative
        # FIXED: Use centralized prompt escaping
        safe_query = escape_prompt_content(query)
        safe_positive = escape_prompt_content(positive_chunk.page_content[:300])
        candidates_text = "\n\n".join(
            f"[{i}] {escape_prompt_content(c.page_content[:200])}" for i, c in enumerate(candidates)
        )

        prompt = HARD_NEGATIVE_PROMPT.format(
            query=safe_query,
            positive=safe_positive,
            candidates=candidates_text,
        )

        try:
            data = await self._call_llm_with_retry(
                prompt=prompt,
                response_schema=HardNegativeSchema,
                call_type="hard_negative_selection",
                correlation_id=correlation_id,
                temperature=0.0,
                max_tokens=100,
            )
            if data and "hardest_negative_index" in data:
                idx = int(data.get("hardest_negative_index", 0))
                if 0 <= idx < len(candidates):
                    return candidates[idx].page_content[:512]
        except Exception as e:
            logger.warning(f"[{correlation_id}] Hard negative selection failed: {e}")

        # Fallback: random candidate
        return random.choice(candidates).page_content[:512]

    async def generate_dataset_async(
        self,
        workspace_id: str,
        domain: str = "general",
        max_chunks: int = 200,
        queries_per_chunk: int = _QUERIES_PER_CHUNK,
        save_path: Optional[str] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> TripletDataset:
        """
        Async version: Generate a complete training dataset from ChromaDB documents.
        BATMAN-A: Non-blocking, yields to event loop between chunks.
        """
        corr_id = correlation_id or generate_finetune_correlation_id("dataset_gen")

        # DVMELTSS-V: Validate domain using centralized utility
        domain = validate_domain(domain, _VALID_DOMAINS)

        # FIXED: _get_chunks is sync (ChromaDB) — offload to thread to avoid blocking event loop
        chunks = await asyncio.to_thread(self._get_chunks, workspace_id, domain, max_chunks, corr_id)
        if not chunks:
            logger.warning(f"[{corr_id}] No chunks found for workspace={workspace_id}, domain={domain}")
            return TripletDataset(domain=domain, correlation_id=corr_id)

        logger.info(
            f"[{corr_id}] Generating triplets: {len(chunks)} chunks × "
            f"{queries_per_chunk} queries = ~{len(chunks) * queries_per_chunk} triplets | "
            f"domain={domain}"
        )

        triplets = []
        semaphore = asyncio.Semaphore(5)  # Limit concurrent LLM calls

        async def process_chunk(chunk: Document, i: int) -> list[TrainingTriplet]:
            async with semaphore:
                chunk_triplets = []

                # Generate queries
                queries = await self._generate_queries_async(
                    chunk_text=chunk.page_content,
                    domain=domain,
                    n_queries=queries_per_chunk,
                    correlation_id=corr_id,
                )
                if not queries:
                    return []

                # Find hard negative (reuse for all queries from same chunk)
                hard_negative = await self._find_hard_negative_async(
                    query=queries[0],
                    positive_chunk=chunk,
                    all_chunks=chunks,
                    correlation_id=corr_id,
                )
                if not hard_negative:
                    return []

                # Create triplets
                for query in queries:
                    chunk_triplets.append(
                        TrainingTriplet(
                            id=str(uuid.uuid4())[:8],
                            anchor=query,
                            positive=chunk.page_content[:512],
                            negative=hard_negative,
                            domain=domain,
                            source_file=chunk.metadata.get("source_file", ""),
                            correlation_id=corr_id,  # FIXED: Propagate correlation_id
                        )
                    )

                if (i + 1) % 20 == 0:
                    logger.info(
                        f"[{corr_id}] Progress: {i+1}/{len(chunks)} chunks | {len(triplets) + len(chunk_triplets)} triplets"
                    )

                return chunk_triplets

        # Process chunks concurrently
        tasks = [process_chunk(c, i) for i, c in enumerate(chunks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, list):
                triplets.extend(res)

        from datetime import datetime, timezone

        dataset = TripletDataset(
            domain=domain,
            triplets=triplets,
            version=datetime.now(timezone.utc).strftime("%Y%m%d"),
            created_at=datetime.now(timezone.utc).isoformat(),
            correlation_id=corr_id,  # FIXED: Propagate correlation_id
        )

        if save_path:
            dataset.save(save_path)

        logger.info(
            f"[{corr_id}] Dataset generated: {dataset.size} triplets | " f"domain={domain} | workspace={workspace_id}"
        )
        return dataset

    def generate_dataset(
        self,
        workspace_id: str,
        domain: str = "general",
        max_chunks: int = 200,
        queries_per_chunk: int = _QUERIES_PER_CHUNK,
        save_path: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> TripletDataset:
        """
        Sync wrapper for backward compatibility.
        DVMELTSS-M: Prefer async version in new code.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(
                self.generate_dataset_async(
                    workspace_id,
                    domain,
                    max_chunks,
                    queries_per_chunk,
                    save_path,
                    correlation_id,
                ),
                loop,
            ).result()
        except RuntimeError:
            return asyncio.run(
                self.generate_dataset_async(
                    workspace_id,
                    domain,
                    max_chunks,
                    queries_per_chunk,
                    save_path,
                    correlation_id,
                )
            )


# DVMELTSS-M: Explicit module exports
__all__ = ["TripletDatasetGenerator", "TripletDataset", "TrainingTriplet"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
