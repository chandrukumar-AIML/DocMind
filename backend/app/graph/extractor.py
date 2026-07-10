
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Final, Optional, Any

from langchain_core.documents import Document
from pydantic import BaseModel, ValidationError, Field

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.llm_pool import get_llm
from app.core.graph_utils import (
    validate_entity_type,
    validate_relationship_type,
    escape_graph_prompt,
    generate_graph_correlation_id,
)
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & VALIDATION (DVMELTSS-S, V) -----------------------------
# ========================================================================

# DVMELTSS-V: Extraction limits
_MAX_ENTITIES: Final = 15
_MAX_RELATIONSHIPS: Final = 20
_MAX_TEXT_LENGTH: Final = 3000
_CHARS_PER_TOKEN: Final = 4
_MAX_PROMPT_TOKENS: Final = 6000
_LLM_TIMEOUT: Final = 30.0  # ✅ NEW: Per-LLM-call timeout

# DVMELTSS-E: Retry configuration
_MAX_RETRIES: Final = 3
_RETRY_BASE_DELAY: Final = 1.0
_RETRY_MAX_DELAY: Final = 30.0


# DVMELTSS-V: Pydantic schemas for structured LLM output
class EntitySchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: str
    description: str = Field(default="", max_length=500)


class RelationshipSchema(BaseModel):
    from_entity: str = Field(..., min_length=1)
    to_entity: str = Field(..., min_length=1)
    type: str
    description: str = Field(default="", max_length=500)


class ExtractionResponseSchema(BaseModel):
    entities: list[EntitySchema] = Field(default_factory=list, max_length=_MAX_ENTITIES)
    relationships: list[RelationshipSchema] = Field(default_factory=list, max_length=_MAX_RELATIONSHIPS)

    model_config = {"extra": "forbid"}


# ========================================================================
# -- IMMUTABLE DATA MODELS (DVMELTSS-M, V) ------------------------------
# ========================================================================


@dataclass
class ExtractedEntity:
    """
    Extracted entity with deterministic ID.
    ✅ FIXED: Proper field defaults + validation in __post_init__.
    """

    id: str
    name: str
    entity_type: str
    description: str = ""
    properties: dict = field(default_factory=dict)
    correlation_id: Optional[str] = None

    def __post_init__(self):
        self.entity_type = validate_entity_type(self.entity_type)
        # Sanitize name: lowercase, strip, remove dangerous sequences
        safe_name = self.name.lower().strip().replace("::", ":").replace("{", "").replace("}", "")
        object.__setattr__(self, "name", safe_name)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "description": self.description,
            "properties": self.properties,
            "correlation_id": self.correlation_id,
        }


@dataclass
class ExtractedRelationship:
    """Immutable extracted relationship."""

    from_entity_id: str
    to_entity_id: str
    relationship_type: str
    properties: dict = field(default_factory=dict)
    correlation_id: Optional[str] = None

    def __post_init__(self):
        self.relationship_type = validate_relationship_type(self.relationship_type)

    def to_dict(self) -> dict:
        return {
            "from_entity_id": self.from_entity_id,
            "to_entity_id": self.to_entity_id,
            "relationship_type": self.relationship_type,
            "properties": self.properties,
            "correlation_id": self.correlation_id,
        }


@dataclass
class ExtractionResult:
    """Collection of extracted entities and relationships for a chunk."""

    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelationship]
    source_file: str
    page_number: int
    chunk_id: str
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
            "source_file": self.source_file,
            "page_number": self.page_number,
            "chunk_id": self.chunk_id,
            "correlation_id": self.correlation_id,
        }


# ========================================================================
# -- PROMPT TEMPLATE (OWASP-1: Structured, safe) -----------------------
# ========================================================================

EXTRACTION_SYSTEM_PROMPT = """You are a knowledge graph extraction expert.
Extract entities and relationships from the given text.

Return ONLY valid JSON matching this schema:
{{
  "entities": [
    {{"name": "entity name", "type": "Person|Organization|Contract|Clause|Date|Location|Concept|Amount", "description": "brief description"}}
  ],
  "relationships": [
    {{"from": "entity name", "to": "entity name", "type": "SIGNED_BY|INVOLVES|CONTAINS|REFERENCES|DATED|LOCATED_IN|RELATED_TO|PART_OF|MENTIONS|AUTHORED_BY", "description": "why this relationship exists"}}
  ]
}}

Rules:
- Extract ONLY entities clearly present in the text
- Entity names must be specific (not generic like "the company")
- Relationships must reference entities in the entities list
- Maximum {max_entities} entities and {max_rels} relationships per chunk
- Return empty lists if nothing meaningful to extract
- Do NOT invent entities not mentioned in the text
"""


# ========================================================================
# -- EXTRACTOR CLASS (DVMELTSS-V, BATMAN-A, OWASP-1) -------------------
# ========================================================================


class GraphExtractor:
    """
    Extracts entities and relationships from text chunks using GPT-4o.

    Features:
    - Centralized LLM pool via app.core.llm_pool
    - Pydantic structured output validation
    - Deterministic entity IDs with safe name sanitization
    - Centralized retry decorator for rate limits
    - Correlation ID tracing for audit trails
    """

    def __init__(self, model: str = "gpt-4o", max_retries: int = _MAX_RETRIES):
        settings = get_settings()
        self.llm = get_llm(streaming=False, model_override=model, temperature_override=0.0)
        self.model = model
        self.max_retries = max_retries

        logger.info(f"GraphExtractor initialized: model={model}, async=True")

    def _validate_inputs(
        self,
        text: str,
        source_file: str,
        chunk_id: str,
        corr_id: str,
    ) -> tuple[bool, str]:
        """Validate inputs before processing."""
        if not isinstance(text, str) or not text.strip():
            return False, "text must be a non-empty string"
        if not isinstance(source_file, str) or not source_file.strip():
            return False, "source_file must be a non-empty string"
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            return False, "chunk_id must be a non-empty string"
        return True, ""

    def _estimate_tokens(self, text: str) -> int:
        """BATMAN-A: Rough token estimation for prompt safety."""
        return len(text) // _CHARS_PER_TOKEN

    def _make_entity_id(self, name: str, entity_type: str, workspace_id: str) -> str:
        """
        DVMELTSS-S: Deterministic entity ID with safe name sanitization.
        Prevents ID collision or injection via malicious names.
        """
        # ✅ Stricter sanitization: allow only alphanumeric, underscore, hyphen
        safe_name = re.sub(r"[^a-z0-9_\-\s]", "", name.lower().strip())
        safe_name = re.sub(r"\s+", "_", safe_name)  # Replace spaces with underscore
        raw = f"{workspace_id}::{entity_type.lower()}::{safe_name}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @retry_async(
        config=RetryConfig(
            max_attempts=_MAX_RETRIES,
            backoff_base=_RETRY_BASE_DELAY,
            backoff_max=_RETRY_MAX_DELAY,
            exceptions=(Exception,),
        )
    )
    async def _call_llm_with_retry(self, prompt: str, corr_id: str) -> str:
        """DVMELTSS-E: Async LLM call with centralized retry."""
        # ✅ Check if llm.ainvoke is truly async
        if inspect.iscoroutinefunction(self.llm.ainvoke):
            response = await asyncio.wait_for(
                self.llm.ainvoke([{"role": "user", "content": prompt}]),
                timeout=_LLM_TIMEOUT,
            )
        else:
            # Fallback: run sync call in thread
            import sys

            if sys.version_info >= (3, 9):
                response = await asyncio.wait_for(
                    asyncio.to_thread(lambda: self.llm.invoke([{"role": "user", "content": prompt}])),
                    timeout=_LLM_TIMEOUT,
                )
            else:
                loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self.llm.invoke([{"role": "user", "content": prompt}]),
                    ),
                    timeout=_LLM_TIMEOUT,
                )
        return response.content if hasattr(response, "content") else str(response)

    async def extract_from_chunk_async(
        self,
        text: str,
        source_file: str,
        page_number: int,
        chunk_id: str,
        workspace_id: str = "default",
        document_type: str = "general",
        correlation_id: Optional[str] = None,
    ) -> ExtractionResult:
        """
        Async version: Extract entities and relationships from a single text chunk.
        BATMAN-A: Non-blocking, yields to event loop.
        ✅ FIXED: Input validation + proper async handling.
        """
        corr_id = correlation_id or generate_graph_correlation_id("extract")

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(text, source_file, chunk_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return self._empty_result(source_file, page_number, chunk_id, corr_id)

        if not text or len(text.strip()) < 50:
            return self._empty_result(source_file, page_number, chunk_id, corr_id)

        # Build prompt with focus hints
        focus_hints = {
            "legal": "Focus on: parties, clauses, obligations, dates, legal terms.",
            "medical": "Focus on: patients, diagnoses, medications, procedures, providers.",
            "invoice": "Focus on: vendors, amounts, dates, line items, PO numbers.",
            "general": "Focus on: key people, organizations, concepts, and their relationships.",
        }
        hint = focus_hints.get(document_type, focus_hints["general"])
        safe_text = escape_graph_prompt(text[:_MAX_TEXT_LENGTH])

        prompt = (
            EXTRACTION_SYSTEM_PROMPT.format(max_entities=_MAX_ENTITIES, max_rels=_MAX_RELATIONSHIPS)
            + f"\n\n{hint}\n\nText to analyze:\n{safe_text}"
        )

        try:
            content = await self._call_llm_with_retry(prompt, corr_id)
            data = json.loads(content)
            ExtractionResponseSchema.model_validate(data)
            return self._parse_extraction(data, source_file, page_number, chunk_id, workspace_id, corr_id)
        except json.JSONDecodeError as e:
            logger.error(f"[{corr_id}] Failed to parse LLM JSON: {e}")
            return self._empty_result(source_file, page_number, chunk_id, corr_id)
        except ValidationError as e:
            logger.error(f"[{corr_id}] LLM output validation failed: {e}")
            return self._empty_result(source_file, page_number, chunk_id, corr_id)
        except Exception as e:
            logger.error(f"[{corr_id}] Graph extraction failed: {type(e).__name__}: {e}")
            return self._empty_result(source_file, page_number, chunk_id, corr_id)

    def _parse_extraction(
        self,
        data: dict,
        source_file: str,
        page_number: int,
        chunk_id: str,
        workspace_id: str,
        correlation_id: str,
    ) -> ExtractionResult:
        """Parse LLM JSON output into typed dataclasses with validation."""
        raw_entities = data.get("entities", [])[:_MAX_ENTITIES]
        raw_rels = data.get("relationships", [])[:_MAX_RELATIONSHIPS]

        # Build entity map: name -> ExtractedEntity
        entity_map: dict[str, ExtractedEntity] = {}
        for e in raw_entities:
            name = str(e.get("name", "")).strip()
            etype = str(e.get("type", "Concept")).strip()
            if not name:
                continue

            entity_id = self._make_entity_id(name, etype, workspace_id)
            entity_map[name] = ExtractedEntity(
                id=entity_id,
                name=name,
                entity_type=etype,
                description=str(e.get("description", ""))[:500],
                correlation_id=correlation_id,
            )

        # Parse relationships — both from/to must be in entity_map
        relationships: list[ExtractedRelationship] = []
        for r in raw_rels:
            from_name = str(r.get("from", "")).strip()
            to_name = str(r.get("to", "")).strip()
            rel_type = str(r.get("type", "RELATED_TO")).strip()

            if from_name not in entity_map or to_name not in entity_map:
                continue  # skip dangling relationships

            relationships.append(
                ExtractedRelationship(
                    from_entity_id=entity_map[from_name].id,
                    to_entity_id=entity_map[to_name].id,
                    relationship_type=rel_type,
                    properties={"description": str(r.get("description", ""))[:500]},
                    correlation_id=correlation_id,
                )
            )

        return ExtractionResult(
            entities=list(entity_map.values()),
            relationships=relationships,
            source_file=source_file,
            page_number=page_number,
            chunk_id=chunk_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _empty_result(source_file: str, page_number: int, chunk_id: str, correlation_id: str) -> ExtractionResult:
        return ExtractionResult(
            entities=[],
            relationships=[],
            source_file=source_file,
            page_number=page_number,
            chunk_id=chunk_id,
            correlation_id=correlation_id,
        )

    async def extract_from_document_async(
        self,
        chunks: list[Document],
        source_file: str,
        workspace_id: str = "default",
        document_type: str = "general",
        correlation_id: Optional[str] = None,
        concurrency: int = 3,
    ) -> list[ExtractionResult]:
        """
        Async version: Extract from all chunks of a document.
        BATMAN-A: Concurrent extraction with controlled parallelism.
        ✅ FIXED: Per-task exception handling.
        """
        corr_id = correlation_id or generate_graph_correlation_id("doc_extract")
        semaphore = asyncio.Semaphore(concurrency)
        results = []

        async def process_chunk(chunk: Document) -> Optional[ExtractionResult]:
            async with semaphore:
                try:
                    meta = chunk.metadata
                    page_num = meta.get("page_number", 0)
                    chunk_id = meta.get("chunk_id", "")
                    block_type = meta.get("block_type", "paragraph")

                    # Skip low-signal blocks
                    if block_type in {"footer", "figure_caption", "header"}:
                        return self._empty_result(source_file, page_num, chunk_id, corr_id)

                    return await self.extract_from_chunk_async(
                        text=chunk.page_content,
                        source_file=source_file,
                        page_number=page_num,
                        chunk_id=chunk_id,
                        workspace_id=workspace_id,
                        document_type=document_type,
                        correlation_id=corr_id,
                    )
                except Exception as e:
                    logger.warning(f"[{corr_id}] Failed to process chunk: {e}")
                    return None

        tasks = [process_chunk(c) for c in chunks]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result:  # Skip None results from failed tasks
                results.append(result)

        total_entities = sum(len(r.entities) for r in results)
        total_rels = sum(len(r.relationships) for r in results)
        logger.info(
            f"[{corr_id}] Graph extraction complete: {source_file} | "
            f"chunks={len(chunks)} | entities={total_entities} | "
            f"relationships={total_rels}"
        )
        return results

    # ====================================================================
    # -- SYNC WRAPPERS FOR BACKWARD COMPATIBILITY -----------------------
    # ====================================================================

    def extract_from_chunk(
        self,
        text: str,
        source_file: str,
        page_number: int,
        chunk_id: str,
        workspace_id: str = "default",
        document_type: str = "general",
        correlation_id: Optional[str] = None,
    ) -> ExtractionResult:
        """
        Sync wrapper — use extract_from_chunk_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return empty
            logger.warning(
                "⚠️ GraphExtractor.extract_from_chunk() called from async context — "
                "use extract_from_chunk_async() instead. Returning empty result."
            )
            return self._empty_result(source_file, page_number, chunk_id, correlation_id or "sync_wrapper")
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(
                self.extract_from_chunk_async(
                    text,
                    source_file,
                    page_number,
                    chunk_id,
                    workspace_id,
                    document_type,
                    correlation_id,
                )
            )

    def extract_from_document(
        self,
        chunks: list[Document],
        source_file: str,
        workspace_id: str = "default",
        document_type: str = "general",
        correlation_id: Optional[str] = None,
    ) -> list[ExtractionResult]:
        """
        Sync wrapper — use extract_from_document_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return empty
            logger.warning(
                "⚠️ GraphExtractor.extract_from_document() called from async context — "
                "use extract_from_document_async() instead. Returning empty result."
            )
            return []
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(
                self.extract_from_document_async(chunks, source_file, workspace_id, document_type, correlation_id)
            )


def get_graph_extractor_metadata() -> dict[str, Any]:
    """✅ NEW: Return graph extractor metadata for monitoring."""
    return {
        "model": get_settings().openai_chat_model,
        "max_entities": _MAX_ENTITIES,
        "max_relationships": _MAX_RELATIONSHIPS,
        "max_text_length": _MAX_TEXT_LENGTH,
        "llm_timeout": _LLM_TIMEOUT,
        "retry_config": {
            "max_attempts": _MAX_RETRIES,
            "backoff_base": _RETRY_BASE_DELAY,
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "GraphExtractor",
    "ExtractedEntity",
    "ExtractedRelationship",
    "ExtractionResult",
    "get_graph_extractor_metadata",
]
# Local smoke test entry point. Run: python -m

