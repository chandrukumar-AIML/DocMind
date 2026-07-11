# ACID-INDEX: C - Constraints (Indexes), E - Error handling

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Final, List, Optional

try:
    from neo4j import AsyncGraphDatabase, AsyncDriver
    from neo4j.exceptions import (
        Neo4jError,
        ServiceUnavailable,
        SessionExpired,
        TransientError,
        AuthError,
    )
    _NEO4J_AVAILABLE = True
except ImportError:  # pragma: no cover
    AsyncGraphDatabase = None  # type: ignore[assignment,misc]
    AsyncDriver = None  # type: ignore[assignment,misc]
    Neo4jError = Exception  # type: ignore[assignment,misc]
    ServiceUnavailable = Exception  # type: ignore[assignment,misc]
    SessionExpired = Exception  # type: ignore[assignment,misc]
    TransientError = Exception  # type: ignore[assignment,misc]
    AuthError = Exception  # type: ignore[assignment,misc]
    _NEO4J_AVAILABLE = False

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.graph_utils import (
    validate_entity_type,
    validate_relationship_type,
    generate_graph_correlation_id,
)

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-M) -------------------------
# ========================================================================

# DVMELTSS-E: Retry configuration for transient Neo4j errors
_MAX_RETRIES: Final = 3
_RETRY_BASE_DELAY: Final = 1.0

# BATMAN-M: Connection pool settings
_MAX_POOL_SIZE: Final = 50
_CONNECTION_TIMEOUT: Final = 30.0
_NEO4J_QUERY_TIMEOUT: Final = 60.0  # ✅ NEW: Per-query timeout

_neo4j_instances: Dict[str, Neo4jStore] = {}


@dataclass(frozen=True)
class Neo4jQueryResult:
    """
    Immutable result wrapper for Neo4j queries.
    DVMELTSS-M: Frozen dataclass prevents runtime mutation.
    """

    records: List[Dict[str, Any]]
    summary: str
    success: bool
    error: Optional[str] = None
    correlation_id: Optional[str] = None

    def __post_init__(self):
        if self.success and self.error:
            object.__setattr__(self, "error", None)

    def to_dict(self) -> dict:
        return {
            "records": self.records[:50],  # Limit for API safety
            "summary": self.summary,
            "success": self.success,
            "error": self.error,
            "count": len(self.records),
            "correlation_id": self.correlation_id,
        }


class Neo4jStore:
    """
    Async Neo4j graph database interface for DocuMind AI.

    Features (DVMELTSS-V, BATMAN-A, ACID-C):
    - Async execution via AsyncGraphDatabase (non-blocking)
    - Connection pooling & health checks
    - Schema enforcement (valid labels/types)
    - Workspace isolation via mandatory filtering
    - Retry logic for transient errors
    - Correlation ID tracing for distributed debugging
    """

    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database
        self._driver: Optional[AsyncDriver] = None
        logger.info("Neo4jStore initialized (lazy connection)")

    def __del__(self):
        """Cleanup on garbage collection."""
        if self._driver:
            # Don't await here — just log for observability
            logger.debug("Neo4jStore cleanup: driver should be closed explicitly")

    async def _connect(self) -> AsyncDriver:
        """Establish async connection pool."""
        if self._driver:
            return self._driver

        try:
            self._driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password),
                max_connection_pool_size=_MAX_POOL_SIZE,
                connection_timeout=_CONNECTION_TIMEOUT,
            )
            await self._driver.verify_connectivity()
            logger.info(f"Neo4j connected: {self.uri}")

            # Initialize schema indexes
            await self._create_indexes()
            return self._driver
        except Exception as e:
            logger.error(f"Neo4j connection failed: {e}")
            raise

    async def close(self):
        """Close connection pool."""
        if self._driver:
            await self._driver.close()
            logger.info("Neo4j connection closed.")
            self._driver = None

    async def _create_indexes(self):
        """Create indexes idempotently for performance."""
        indexes = [
            "CREATE INDEX entity_id_idx IF NOT EXISTS FOR (n:__Entity__) ON (n.id)",
            "CREATE INDEX entity_workspace_idx IF NOT EXISTS FOR (n:__Entity__) ON (n.workspace_id)",
            "CREATE INDEX doc_source_idx IF NOT EXISTS FOR (n:Document) ON (n.source_file)",
            "CREATE INDEX doc_workspace_idx IF NOT EXISTS FOR (n:Document) ON (n.workspace_id)",
        ]
        async with self._driver.session(database=self.database) as session:
            for idx in indexes:
                try:
                    await session.run(idx)
                except Exception as e:
                    logger.debug(f"Index creation skipped: {e}")

    def _validate_cypher_query(self, query: str, corr_id: str) -> tuple[bool, str]:
        """Validate that query is a safe Cypher string."""
        if not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string"

        # Reject obviously dangerous patterns
        dangerous = [
            r"\bDROP\b",
            r"\bDELETE\s+DATABASE\b",
            r"\bDETACH\s+DELETE\b.*\b__Entity__\b",
            r"\bCALL\s+dbms\b",
            r"\bCALL\s+apoc\b.*\bwrite\b",
        ]
        for pattern in dangerous:
            if re.search(pattern, query, re.IGNORECASE):
                return False, f"Query contains dangerous pattern: {pattern}"

        return True, ""

    async def _execute_with_retry(self, query: str, params: Dict[str, Any], corr_id: str) -> List[Dict[str, Any]]:
        """Execute query with retry logic for transient errors."""
        attempt = 0
        while attempt <= _MAX_RETRIES:
            try:
                async with self._driver.session(database=self.database) as session:
                    result = await asyncio.wait_for(
                        session.run(query, params),
                        timeout=_NEO4J_QUERY_TIMEOUT,
                    )
                    records = [record.data() async for record in result]
                    return records
            except asyncio.TimeoutError:
                logger.error(f"[{corr_id}] Neo4j query timed out after {_NEO4J_QUERY_TIMEOUT}s")
                return []
            except (ServiceUnavailable, SessionExpired, TransientError) as e:
                attempt += 1
                if attempt > _MAX_RETRIES:
                    logger.error(f"[{corr_id}] Neo4j query failed after retries: {e}")
                    return []
                wait = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(f"[{corr_id}] Neo4j transient error, retry {attempt}/{_MAX_RETRIES} in {wait}s: {e}")
                await asyncio.sleep(wait)
            except AuthError as e:
                logger.error(f"[{corr_id}] Neo4j auth failed: {e}")
                return []
            except Neo4jError as e:
                # Handle other Neo4j-specific errors
                logger.error(f"[{corr_id}] Neo4j query error: {e}")
                return []
            except Exception as e:
                # Log unexpected errors but don't retry
                logger.error(f"[{corr_id}] Unexpected Neo4j error: {type(e).__name__}: {e}")
                return []
        return []

    async def execute_query_async(
        self,
        query: str,
        parameters: Dict[str, Any],
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> Neo4jQueryResult:
        """Main async execution entry point."""
        corr_id = correlation_id or generate_graph_correlation_id("neo4j_query")

        # ✅ Validate Cypher query
        is_valid, error = self._validate_cypher_query(query, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid Cypher query: {error}")
            return Neo4jQueryResult(
                records=[],
                summary="Invalid query",
                success=False,
                error=error,
                correlation_id=corr_id,
            )

        # Ensure driver is connected
        await self._connect()

        # DVMELTSS-V: Validate workspace_id presence
        if "workspace_id" not in parameters and "workspace_id" not in query:
            logger.warning(f"[{corr_id}] Query missing workspace_id parameter")
            return Neo4jQueryResult(
                records=[],
                summary="Missing workspace_id",
                success=False,
                error="Missing workspace_id",
                correlation_id=corr_id,
            )

        params = {"workspace_id": workspace_id, **(parameters or {})}
        records = await self._execute_with_retry(query, params, corr_id)

        return Neo4jQueryResult(
            records=records,
            summary=f"Returned {len(records)} records",
            success=True,
            correlation_id=corr_id,
        )

    async def upsert_document_async(
        self,
        source_file: str,
        document_type: str,
        page_count: int,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> str:
        """Upsert Document node."""
        corr_id = correlation_id or generate_graph_correlation_id("upsert_doc")
        query = """
        MERGE (d:Document {source_file: $source_file, workspace_id: $workspace_id})
        SET d.document_type = $document_type, d.page_count = $page_count, d.updated_at = datetime()
        RETURN elementId(d) AS node_id
        """
        result = await self.execute_query_async(
            query,
            {
                "source_file": source_file,
                "document_type": document_type,
                "page_count": page_count,
            },
            workspace_id,
            correlation_id=corr_id,
        )

        if result.records:
            return result.records[0].get("node_id", "")
        return ""

    async def upsert_entity_async(
        self,
        entity_id: str,
        entity_type: str,
        name: str,
        properties: Dict,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> str:
        """Upsert Entity node."""
        corr_id = correlation_id or generate_graph_correlation_id("upsert_entity")
        entity_type = validate_entity_type(entity_type)

        # Neo4j doesn't support parameterized labels, so we validate and whitelist
        safe_type = re.sub(r"[^a-zA-Z0-9_]", "", entity_type)  # Remove special chars
        if not safe_type:
            safe_type = "Entity"

        query = f"""
        MERGE (e:__Entity__ {{id: $entity_id, workspace_id: $workspace_id}})
        SET e:{safe_type}, e.name = $name, e.entity_type = $entity_type, e.updated_at = datetime()
        SET e += $properties
        RETURN elementId(e) AS node_id
        """
        result = await self.execute_query_async(
            query,
            {"entity_id": entity_id, "name": name, "properties": properties or {}},
            workspace_id,
            correlation_id=corr_id,
        )

        if result.records:
            return result.records[0].get("node_id", "")
        return ""

    async def upsert_relationship_async(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: Dict,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ):
        """Upsert Relationship."""
        corr_id = correlation_id or generate_graph_correlation_id("upsert_rel")
        safe_type = validate_relationship_type(rel_type)

        query = f"""
        MATCH (a:__Entity__ {{id: $from_id, workspace_id: $workspace_id}})
        MATCH (b:__Entity__ {{id: $to_id, workspace_id: $workspace_id}})
        MERGE (a)-[r:{safe_type}]->(b)
        SET r += $properties
        SET r.updated_at = datetime()
        """
        await self.execute_query_async(
            query,
            {"from_id": from_id, "to_id": to_id, "properties": properties or {}},
            workspace_id,
            correlation_id=corr_id,
        )

    def _run_async_task(self, coro):
        """Helper for sync wrappers with proper event loop handling."""
        try:
            loop = asyncio.get_running_loop()
            # If already in async context, return coroutine for caller to await
            # This prevents deadlock — caller should use async version instead
            logger.warning("Sync method called from async context — use async version instead")
            return None
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(coro)

    # Sync Wrappers with correlation_id support
    def execute_query(
        self,
        query: str,
        parameters: Dict[str, Any] = None,
        workspace_id: str = "default",
        correlation_id: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        result = self._run_async_task(self.execute_query_async(query, parameters or {}, workspace_id, correlation_id))
        return result.records if result else None

    def upsert_document(
        self,
        source_file: str,
        document_type: str,
        page_count: int,
        workspace_id: str = "default",
        correlation_id: Optional[str] = None,
    ) -> Optional[str]:
        return self._run_async_task(
            self.upsert_document_async(source_file, document_type, page_count, workspace_id, correlation_id)
        )

    def upsert_entity(
        self,
        entity_id: str,
        entity_type: str,
        name: str,
        properties: Dict = None,
        workspace_id: str = "default",
        correlation_id: Optional[str] = None,
    ) -> Optional[str]:
        return self._run_async_task(
            self.upsert_entity_async(
                entity_id,
                entity_type,
                name,
                properties or {},
                workspace_id,
                correlation_id,
            )
        )

    def upsert_relationship(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: Dict = None,
        workspace_id: str = "default",
        correlation_id: Optional[str] = None,
    ):
        return self._run_async_task(
            self.upsert_relationship_async(from_id, to_id, rel_type, properties or {}, workspace_id, correlation_id)
        )

    def get_schema_summary(self, workspace_id: str = "default") -> dict[str, dict[str, int]]:
        """Return graph schema counts; degrade to empty counts if Neo4j is unavailable."""
        try:
            records = (
                self.execute_query(
                    """
                MATCH (n {workspace_id: $workspace_id})
                WITH labels(n) AS labels, count(*) AS count
                RETURN labels, count
                """,
                    {"workspace_id": workspace_id},
                    workspace_id=workspace_id,
                )
                or []
            )
        except Exception as exc:
            logger.warning("Neo4j schema summary unavailable: %s", exc)
            records = []
        nodes: dict[str, int] = {}
        for row in records:
            for label in row.get("labels", []) or []:
                nodes[label] = nodes.get(label, 0) + int(row.get("count", 0) or 0)
        return {"nodes": nodes, "relationships": {}}

    def get_entity_neighborhood(
        self,
        entity_name: str,
        hops: int = 2,
        workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Compatibility API for graph route visualization."""
        return []


def get_neo4j_store() -> Neo4jStore:
    """
    Singleton Neo4j store.
    ✅ FIXED: Use dict cache with version key for proper singleton behavior.
    """
    settings = get_settings()
    # Create version key from settings to invalidate cache if config changes
    version_key = f"{settings.neo4j_uri}:{settings.neo4j_database}"

    if version_key not in _neo4j_instances:
        _neo4j_instances[version_key] = Neo4jStore(
            uri=settings.neo4j_uri,
            username=settings.neo4j_username,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
        )
    return _neo4j_instances[version_key]


def get_neo4j_metadata() -> dict[str, Any]:
    """✅ NEW: Return Neo4j metadata for monitoring."""
    return {
        "uri": get_settings().neo4j_uri,
        "database": get_settings().neo4j_database,
        "max_pool_size": _MAX_POOL_SIZE,
        "connection_timeout": _CONNECTION_TIMEOUT,
        "query_timeout": _NEO4J_QUERY_TIMEOUT,
        "retry_config": {
            "max_attempts": _MAX_RETRIES,
            "backoff_base": _RETRY_BASE_DELAY,
        },
        "active_instances": len(_neo4j_instances),
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["Neo4jStore", "get_neo4j_store", "Neo4jQueryResult", "get_neo4j_metadata"]
# Local smoke test entry point. Run: python -m

