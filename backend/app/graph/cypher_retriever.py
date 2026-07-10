
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from typing import Final, Optional, Any

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.llm_pool import get_llm
from app.core.graph_utils import (
    sanitize_cypher_input,
    contains_dangerous_cypher,
    escape_graph_prompt,
    generate_graph_correlation_id,
)
from app.core.retry import retry_async, RetryConfig
from .neo4j_store import Neo4jStore, get_neo4j_store

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & SAFETY (DVMELTSS-S, OWASP-1/9) -------------------------
# ========================================================================

# Pre-written safe templates for common query patterns
CYPHER_TEMPLATES: Final = {
    "entity_search": """
        MATCH (e:__Entity__ {workspace_id: $workspace_id})
        WHERE toLower(e.name) CONTAINS toLower($search_term)
        OPTIONAL MATCH (e)-[r]->(neighbor:__Entity__ {workspace_id: $workspace_id})
        RETURN e.name AS entity, e.entity_type AS type,
               e.description AS description,
               collect(DISTINCT {
                   rel: type(r),
                   target: neighbor.name,
                   target_type: neighbor.entity_type
               }) AS connections
        LIMIT 20
    """,
    "document_entities": """
        MATCH (e:__Entity__ {workspace_id: $workspace_id})
              -[:EXTRACTED_FROM]->
              (d:Document {source_file: $source_file, workspace_id: $workspace_id})
        RETURN e.name AS entity, e.entity_type AS type,
               e.description AS description
        ORDER BY e.entity_type, e.name
        LIMIT 50
    """,
    "relationship_path": """
        MATCH path = (a:__Entity__ {workspace_id: $workspace_id})
                     -[*1..3]->
                     (b:__Entity__ {workspace_id: $workspace_id})
        WHERE toLower(a.name) CONTAINS toLower($from_entity)
          AND toLower(b.name) CONTAINS toLower($to_entity)
        RETURN [n IN nodes(path) | n.name]    AS path_nodes,
               [r IN relationships(path) | type(r)] AS path_rels,
               length(path) AS hops
        ORDER BY hops
        LIMIT 5
    """,
    "entity_neighborhood": """
        MATCH (start:__Entity__ {workspace_id: $workspace_id})
        WHERE toLower(start.name) CONTAINS toLower($entity_name)
        MATCH (start)-[r]->(neighbor:__Entity__ {workspace_id: $workspace_id})
        RETURN start.name AS entity,
               type(r) AS relationship,
               neighbor.name AS connected_to,
               neighbor.entity_type AS connected_type
        LIMIT 30
    """,
}

# BATMAN-T: Timeout for LLM calls
_LLM_TIMEOUT: Final = 30.0
_NEO4J_TIMEOUT: Final = 60.0
# DVMELTSS-E: Retry config for LLM calls
_LLM_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=1.0,
    backoff_max=30.0,
    exceptions=(Exception,),
)


class CypherRetriever:
    """
    Converts natural language queries to Cypher and retrieves graph context.

    Features (DVMELTSS-V, BATMAN-A, OWASP-9):
    - Async execution with Neo4j async driver
    - Centralized LLM pool via app.core.llm_pool
    - Safety checks: rejects generated Cypher with DELETE/DROP/etc.
    - Injection prevention in prompts
    - Correlation ID tracing for distributed debugging
    """

    CYPHER_GENERATION_PROMPT = """You are a Neo4j Cypher expert for a document AI system.

Graph schema:
- Nodes: (:__Entity__) with labels like Person, Organization, Contract, Clause, Date, Location, Concept, Amount, Document
- Key properties on __Entity__: id, name, entity_type, description, workspace_id
- Key relationships: SIGNED_BY, INVOLVES, CONTAINS, REFERENCES, DATED, LOCATED_IN, RELATED_TO, MENTIONS, EXTRACTED_FROM
- Document nodes: source_file, document_type, page_count, workspace_id

Rules for generated Cypher:
1. ALWAYS filter by workspace_id: WHERE n.workspace_id = $workspace_id
2. Use toLower() for case-insensitive name matching
3. Use CONTAINS for partial name matches
4. Limit results: LIMIT 20
5. Return human-readable properties, not node objects
6. Use optional matches for flexible queries
7. Return ONLY the Cypher query — no explanation, no markdown

Example output:
MATCH (e:__Entity__ {workspace_id: $workspace_id})
WHERE toLower(e.name) CONTAINS toLower($search_term)
RETURN e.name AS name, e.entity_type AS type, e.description AS description
LIMIT 20
"""

    def __init__(self, neo4j_store: Optional[Neo4jStore] = None, model: str = "gpt-4o"):
        settings = get_settings()
        self.store = neo4j_store or get_neo4j_store()
        self.llm = get_llm(streaming=False, model_override=model, temperature_override=0.0)
        self.model = model

        logger.info(f"CypherRetriever initialized: model={model}, async=True")

    def _validate_inputs(
        self,
        query: str,
        workspace_id: str,
        template_params: Optional[dict],
        corr_id: str,
    ) -> tuple[bool, str]:
        """Validate inputs before processing."""
        if not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string"
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return False, "workspace_id must be a non-empty string"
        if template_params is not None and not isinstance(template_params, dict):
            return False, "template_params must be a dict or None"
        return True, ""

    def _validate_cypher(self, cypher: str) -> bool:
        """OWASP-9: Reject generated Cypher containing dangerous keywords."""
        return not contains_dangerous_cypher(cypher)

    def _format_as_context(self, records: list[dict], query: str) -> str:
        """DVMELTSS-M: Format Neo4j records as readable text context."""
        if not records:
            return ""

        lines = [f"Knowledge Graph Context (for query: '{query}'):"]
        for rec in records[:20]:  # cap at 20 records
            parts = []
            for key, val in rec.items():
                if val is not None and str(val).strip():
                    # Truncate long values
                    val_str = str(val)[:200]
                    parts.append(f"{key}: {val_str}")
            if parts:
                lines.append("  • " + " | ".join(parts))

        return "\n".join(lines)

    @retry_async(config=_LLM_RETRY_CONFIG)
    async def _call_llm_async(self, prompt: str, corr_id: str) -> str:
        """Centralized LLM call with retry logic."""
        if inspect.iscoroutinefunction(self.llm.ainvoke):
            response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
        else:
            # Fallback: run sync call in thread
            import sys

            if sys.version_info >= (3, 9):
                response = await asyncio.to_thread(lambda: self.llm.invoke([{"role": "user", "content": prompt}]))
            else:
                loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                response = await loop.run_in_executor(
                    None, lambda: self.llm.invoke([{"role": "user", "content": prompt}])
                )
        return response.content if hasattr(response, "content") else str(response)

    async def retrieve_async(
        self,
        query: str,
        workspace_id: str = "default",
        use_text_to_cypher: bool = True,
        template_name: Optional[str] = None,
        template_params: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> tuple[str, list[dict]]:
        """
        Async version: Retrieve graph context for a natural language query.
        BATMAN-A: Non-blocking, yields to event loop.
        ✅ FIXED: Input validation + safe sync wrapper.
        """
        corr_id = correlation_id or generate_graph_correlation_id("cypher")

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(query, workspace_id, template_params, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return "", []

        safe_query = sanitize_cypher_input(query)

        try:
            if use_text_to_cypher:
                records = await self._text_to_cypher_retrieve_async(safe_query, workspace_id, corr_id)
            elif template_name and template_name in CYPHER_TEMPLATES:
                params = template_params or {}
                params["workspace_id"] = workspace_id
                cypher = CYPHER_TEMPLATES[template_name]
                result = await asyncio.wait_for(
                    self.store.execute_query_async(cypher, params, workspace_id),
                    timeout=_NEO4J_TIMEOUT,
                )
                records = result.records
            else:
                # Fallback: keyword entity search
                records = await self._keyword_entity_search_async(safe_query, workspace_id, corr_id)

            context_text = self._format_as_context(records, query)
            return context_text, records

        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Graph retrieval timed out after {_NEO4J_TIMEOUT}s")
            return "", []
        except Exception as e:
            logger.error(f"[{corr_id}] Graph retrieval failed: {e}")
            return "", []

    async def _text_to_cypher_retrieve_async(self, query: str, workspace_id: str, correlation_id: str) -> list[dict]:
        """Async: Generate Cypher from natural language using centralized LLM."""
        safe_prompt = escape_graph_prompt(f"{self.CYPHER_GENERATION_PROMPT}\n\nGenerate Cypher for: {query}")

        try:
            content = await self._call_llm_async(safe_prompt, correlation_id)
            cypher = re.sub(r"```(?:cypher)?\s*", "", content)
            cypher = re.sub(r"```", "", cypher).strip()

            # Safety check: reject dangerous operations
            if not self._validate_cypher(cypher):
                logger.warning(f"[{correlation_id}] Rejected unsafe generated Cypher: {cypher[:100]}")
                return await self._keyword_entity_search_async(query, workspace_id, correlation_id)

            logger.info(f"[{correlation_id}] Generated Cypher: {cypher[:200]}")

            result = await asyncio.wait_for(
                self.store.execute_query_async(
                    cypher,
                    {"workspace_id": workspace_id},
                    workspace_id=workspace_id,
                ),
                timeout=_NEO4J_TIMEOUT,
            )
            return result.records

        except asyncio.TimeoutError:
            logger.warning(f"[{correlation_id}] Text-to-Cypher timed out")
            return await self._keyword_entity_search_async(query, workspace_id, correlation_id)
        except Exception as e:
            logger.error(f"[{correlation_id}] Text-to-Cypher failed: {e}")
            return await self._keyword_entity_search_async(query, workspace_id, correlation_id)

    async def _keyword_entity_search_async(self, query: str, workspace_id: str, correlation_id: str) -> list[dict]:
        """Async fallback: extract keywords from query and search entities."""
        try:
            import nltk
            from nltk import word_tokenize, pos_tag

            # Download required resources if not present
            try:
                nltk.data.find("tokenizers/punkt")
            except LookupError:
                nltk.download("punkt", quiet=True)
            try:
                nltk.data.find("taggers/averaged_perceptron_tagger")
            except LookupError:
                nltk.download("averaged_perceptron_tagger", quiet=True)

            tokens = word_tokenize(query)
            tagged = pos_tag(tokens)
            # Extract nouns (NN, NNS, NNP, NNPS) longer than 3 chars
            keywords = [word for word, pos in tagged if pos.startswith("NN") and len(word) > 3]
            search_term = keywords[0] if keywords else query[:20]
        except ImportError:
            # Fallback: simple word extraction
            words = [w for w in query.split() if len(w) > 3 and w.isalpha()]
            search_term = words[0] if words else query[:20]

        search_term = sanitize_cypher_input(search_term)

        cypher = CYPHER_TEMPLATES["entity_search"]
        result = await asyncio.wait_for(
            self.store.execute_query_async(
                cypher,
                {"search_term": search_term, "workspace_id": workspace_id},
                workspace_id=workspace_id,
            ),
            timeout=_NEO4J_TIMEOUT,
        )
        return result.records

    # ====================================================================
    # -- SYNC WRAPPER ---------------------------------------------------
    # ====================================================================

    def retrieve(
        self,
        query: str,
        workspace_id: str = "default",
        use_text_to_cypher: bool = True,
        template_name: Optional[str] = None,
        template_params: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> tuple[str, list[dict]]:
        """
        Sync wrapper — use retrieve_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return empty
            logger.warning(
                "⚠️ CypherRetriever.retrieve() called from async context — "
                "use retrieve_async() instead. Returning empty result."
            )
            return "", []
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(
                self.retrieve_async(
                    query,
                    workspace_id,
                    use_text_to_cypher,
                    template_name,
                    template_params,
                    correlation_id,
                )
            )


def get_cypher_metadata() -> dict[str, Any]:
    """✅ NEW: Return Cypher retriever metadata for monitoring."""
    return {
        "templates": list(CYPHER_TEMPLATES.keys()),
        "llm_timeout": _LLM_TIMEOUT,
        "neo4j_timeout": _NEO4J_TIMEOUT,
        "retry_config": {
            "max_attempts": _LLM_RETRY_CONFIG.max_attempts,
            "backoff_base": _LLM_RETRY_CONFIG.backoff_base,
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["CypherRetriever", "get_cypher_metadata"]
# Local smoke test entry point. Run: python -m

