# ACID-INDEX: E - Error handling (graceful degradation)

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional, Any
from types import SimpleNamespace

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.workspace_utils import (
    validate_workspace_id,
    get_chroma_collection_name,
    get_neo4j_namespace,
    get_bm25_index_path,
    get_embeddings_cache_path,
    generate_workspace_correlation_id,
)
from app.core.retry import retry_async, RetryConfig
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution

logger = logging.getLogger(__name__)

# DVMELTSS-E: Retry configuration for provisioning operations
_PROVISION_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=0.5,
    backoff_max=5.0,
    exceptions=(Exception,),
)

_PROVISION_TIMEOUT: Final = 60


@dataclass(frozen=True)
class WorkspaceResources:
    """
    Immutable status of all storage resources for a workspace.
    DVMELTSS-M: Frozen dataclass prevents runtime mutation.
    """

    workspace_id: str
    chroma_collection: str
    neo4j_namespace: str
    bm25_index_path: str
    embeddings_cache_path: str
    postgres_rls: bool
    errors: list[str] = field(default_factory=list)
    correlation_id: Optional[str] = None

    @property
    def is_healthy(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        """Serialize for API responses / logging."""
        return {
            "workspace_id": self.workspace_id,
            "chroma_collection": self.chroma_collection,
            "neo4j_namespace": self.neo4j_namespace,
            "bm25_index_path": self.bm25_index_path,
            "postgres_rls": self.postgres_rls,
            "is_healthy": self.is_healthy,
            "error_count": len(self.errors),
            "correlation_id": self.correlation_id,
        }


def _validate_workspace_inputs(
    workspace_id: Optional[str],
    correlation_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate workspace manager inputs before processing."""
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return False, "workspace_id must be a non-empty string"
    if correlation_id is not None and not isinstance(correlation_id, str):
        return False, "correlation_id must be a string or None"
    return True, ""


class WorkspaceManager:
    """
    Provisions, validates, and tears down workspace-specific storage.

    Called when:
    - A new workspace is created (provision)
    - A workspace is deleted (teardown)
    - Health check needs to verify isolation (validate)

    Storage provisioning per workspace:
    1. ChromaDB: create collection docs_{workspace_id}
    2. Neo4j:    create workspace namespace index
    3. BM25:     initialize empty index file
    4. PostgreSQL: RLS policy automatically applies

    Features (DVMELTSS-V, BATMAN-A, ACID-E):
    - Async-safe operations with retry logic
    - Centralized path/key generation via app.core.workspace_utils
    - Correlation ID propagation for distributed tracing
    - Graceful degradation on optional component failures
    """

    def __init__(self):
        self.settings = get_settings()

    async def list_user_workspaces(self, user_id: str) -> list[Any]:
        """Compatibility API for workspace route handlers."""
        default_workspace = getattr(self.settings, "default_workspace_id", "default")
        return [
            SimpleNamespace(
                workspace_id=default_workspace,
                name=default_workspace,
                description="Default workspace",
                created_at="",
                owner_id=user_id,
            )
        ]

    async def get_workspace_async(self, workspace_id: str) -> Optional[Any]:
        """Compatibility API for workspace route handlers."""
        default_workspace = getattr(self.settings, "default_workspace_id", "default")
        if workspace_id not in {default_workspace, "default"}:
            return None
        return SimpleNamespace(
            workspace_id=default_workspace,
            name=default_workspace,
            description="Default workspace",
            created_at="",
            owner_id=None,
        )

    async def workspace_exists_async(self, workspace_id: str) -> bool:
        """ADDED: Compatibility existence check used by workspace API routes."""
        default_workspace = getattr(self.settings, "default_workspace_id", "default")
        return workspace_id in {default_workspace, "default"}

    async def create_workspace_async(
        self,
        workspace_id: str,
        owner_id: str,
        description: str = "",
        correlation_id: Optional[str] = None,
    ) -> Any:
        """ADDED: Compatibility create hook backed by storage provisioning."""
        resources = await self.provision_async(workspace_id, correlation_id=correlation_id)
        return SimpleNamespace(
            workspace_id=resources.workspace_id,
            name=resources.workspace_id,
            description=description,
            created_at="",
            owner_id=owner_id,
            resources=resources,
        )

    async def provision_async(
        self,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> WorkspaceResources:
        """
        Async: Provision all storage resources for a new workspace.
        Idempotent — safe to call multiple times.
        BATMAN-A: Non-blocking, yields to event loop between operations.
        """
        corr_id = correlation_id or generate_workspace_correlation_id("provision")

        # ✅ Validate inputs
        is_valid, error = _validate_workspace_inputs(workspace_id, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid workspace inputs: {error}")
            return WorkspaceResources(
                workspace_id=workspace_id or "unknown",
                chroma_collection="",
                neo4j_namespace="",
                bm25_index_path="",
                embeddings_cache_path="",
                postgres_rls=False,
                errors=[error],
                correlation_id=corr_id,
            )

        # DVMELTSS-V: Validate workspace_id early
        safe_id = validate_workspace_id(workspace_id)

        resources = WorkspaceResources(
            workspace_id=safe_id,
            chroma_collection=get_chroma_collection_name(safe_id),
            neo4j_namespace=get_neo4j_namespace(safe_id),
            bm25_index_path=str(get_bm25_index_path(safe_id)),
            embeddings_cache_path=str(get_embeddings_cache_path(safe_id)),
            postgres_rls=False,
            correlation_id=corr_id,
        )

        # ChromaDB collection (required)
        try:
            await asyncio.wait_for(
                self._provision_chroma_async(safe_id, corr_id),
                timeout=_PROVISION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] ChromaDB provision timed out after {_PROVISION_TIMEOUT}s")
            resources = WorkspaceResources(
                **{
                    **resources.__dict__,
                    "errors": resources.errors + ["chroma: timeout"],
                }
            )
        except Exception as e:
            logger.error(f"[{corr_id}] ChromaDB provision failed: {e}")
            resources = WorkspaceResources(**{**resources.__dict__, "errors": resources.errors + [f"chroma: {e}"]})

        # Neo4j workspace indexes (optional)
        try:
            await asyncio.wait_for(
                self._provision_neo4j_async(safe_id, corr_id),
                timeout=_PROVISION_TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"[{corr_id}] Neo4j provision failed (non-fatal): {e}")
            # Neo4j is optional — don't fail workspace creation

        # BM25 index initialization (optional)
        try:
            await asyncio.wait_for(
                self._provision_bm25_async(safe_id, corr_id),
                timeout=_PROVISION_TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"[{corr_id}] BM25 provision failed (non-fatal): {e}")

        resources = WorkspaceResources(
            workspace_id=resources.workspace_id,
            chroma_collection=resources.chroma_collection,
            neo4j_namespace=resources.neo4j_namespace,
            bm25_index_path=resources.bm25_index_path,
            embeddings_cache_path=resources.embeddings_cache_path,
            postgres_rls=True,
            errors=resources.errors,
            correlation_id=resources.correlation_id,
        )

        logger.info(f"[{corr_id}] Workspace provisioned: {safe_id} | " f"errors={resources.errors}")
        return resources

    async def teardown_async(
        self,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """
        Async: Delete all storage resources for a workspace.
        Called when workspace is permanently deleted.
        Returns True if fully cleaned up.
        """
        corr_id = correlation_id or generate_workspace_correlation_id("teardown")

        # ✅ Validate inputs
        is_valid, error = _validate_workspace_inputs(workspace_id, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid teardown inputs: {error}")
            return False

        safe_id = validate_workspace_id(workspace_id)
        errors = []

        # ChromaDB (required)
        try:
            await asyncio.wait_for(
                self._teardown_chroma_async(safe_id, corr_id),
                timeout=_PROVISION_TIMEOUT,
            )
        except Exception as e:
            errors.append(f"chroma: {e}")

        # Neo4j (optional)
        try:
            await asyncio.wait_for(
                self._teardown_neo4j_async(safe_id, corr_id),
                timeout=_PROVISION_TIMEOUT,
            )
        except Exception as e:
            errors.append(f"neo4j: {e}")

        # BM25 (optional)
        try:
            await asyncio.wait_for(
                self._teardown_bm25_async(safe_id, corr_id),
                timeout=_PROVISION_TIMEOUT,
            )
        except Exception as e:
            errors.append(f"bm25: {e}")

        if errors:
            logger.error(f"[{corr_id}] Workspace teardown errors: {safe_id} | {errors}")
            return False

        logger.info(f"[{corr_id}] Workspace torn down: {safe_id}")
        return True

    def get_store_manager(self, workspace_id: str, correlation_id: Optional[str] = None):
        """
        Get a workspace-scoped VectorStoreManager.
        FIXED: No longer mutates os.environ — uses proper constructor args.
        """
        from app.vectorstore.store_manager import VectorStoreManager
        from app.vectorstore.embeddings import CachedOpenAIEmbeddings

        # ✅ Validate inputs
        is_valid, error = _validate_workspace_inputs(workspace_id, correlation_id, "store_manager")
        if not is_valid:
            logger.error(f"Invalid store manager inputs: {error}")
            raise ValueError(error)

        safe_id = validate_workspace_id(workspace_id)
        settings = get_settings()

        embeddings = CachedOpenAIEmbeddings(
            api_key=settings.openai_api_key,
            cache_dir=str(get_embeddings_cache_path(safe_id)),
        )

        return VectorStoreManager(
            collection_name=get_chroma_collection_name(safe_id),
            embeddings=embeddings,
            persist_directory=settings.chroma_persist_dir,
        )

    async def get_usage_stats_async(
        self,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> dict:
        """Async: Get storage usage statistics for a workspace."""
        corr_id = correlation_id or generate_workspace_correlation_id("stats")

        # ✅ Validate inputs
        is_valid, error = _validate_workspace_inputs(workspace_id, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid stats inputs: {error}")
            return {"error": error, "correlation_id": corr_id}

        safe_id = validate_workspace_id(workspace_id)

        stats = {
            "workspace_id": safe_id,
            "chroma_chunks": 0,
            "neo4j_entities": 0,
            "bm25_docs": 0,
            "correlation_id": corr_id,
        }

        # ChromaDB stats
        try:
            from app.vectorstore.chroma_store import _get_chroma_client

            client = _get_chroma_client(self.settings.chroma_persist_dir)
            coll = client.get_collection(get_chroma_collection_name(safe_id))
            stats["chroma_chunks"] = coll.count()
        except Exception as e:
            logger.debug(f"[{corr_id}] Chroma stats failed: {e}")

        # Neo4j stats
        try:
            from app.graph.neo4j_store import get_neo4j_store

            neo4j = get_neo4j_store()
            schema = neo4j.get_schema_summary(workspace_id=safe_id)
            stats["neo4j_entities"] = sum(n.get("count", 0) for n in schema.get("nodes", []) if isinstance(n, dict))
        except Exception as e:
            logger.debug(f"[{corr_id}] Neo4j stats failed: {e}")

        # BM25 stats
        try:
            from app.retrieval.bm25_retriever import get_bm25_index

            bm25 = get_bm25_index(safe_id)
            stats["bm25_docs"] = bm25.count() if hasattr(bm25, "count") else 0
        except Exception as e:
            logger.debug(f"[{corr_id}] BM25 stats failed: {e}")

        return stats

    # -- Private async provisioning methods ----------------------------------

    @retry_async(config=_PROVISION_RETRY_CONFIG)
    async def _provision_chroma_async(self, workspace_id: str, corr_id: str):
        """Async: Create workspace ChromaDB collection with retry."""
        from app.vectorstore.chroma_store import _get_chroma_client

        client = _get_chroma_client(self.settings.chroma_persist_dir)
        client.get_or_create_collection(
            name=get_chroma_collection_name(workspace_id),
            metadata={"workspace_id": workspace_id},
        )
        logger.info(f"[{corr_id}] ChromaDB collection ready: {get_chroma_collection_name(workspace_id)}")

    @retry_async(config=_PROVISION_RETRY_CONFIG)
    async def _provision_neo4j_async(self, workspace_id: str, corr_id: str):
        """Async: Ensure Neo4j workspace indexes exist with retry."""
        from app.graph.neo4j_store import get_neo4j_store

        neo4j = get_neo4j_store()
        if hasattr(neo4j, "ensure_indexes"):
            await neo4j.ensure_indexes(workspace_id=workspace_id)
        elif hasattr(neo4j, "_create_indexes"):
            neo4j._create_indexes()
        logger.info(f"[{corr_id}] Neo4j namespace ready: {workspace_id}")

    async def _provision_bm25_async(self, workspace_id: str, corr_id: str):
        """Async: Initialize empty BM25 index for workspace."""
        index_path = get_bm25_index_path(workspace_id)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[{corr_id}] BM25 index directory ready: {index_path.parent}")

    async def _teardown_chroma_async(self, workspace_id: str, corr_id: str):
        """Async: Delete workspace ChromaDB collection."""
        from app.vectorstore.chroma_store import _get_chroma_client

        client = _get_chroma_client(self.settings.chroma_persist_dir)
        try:
            client.delete_collection(get_chroma_collection_name(workspace_id))
            logger.info(f"[{corr_id}] ChromaDB collection deleted: {get_chroma_collection_name(workspace_id)}")
        except Exception:
            pass  # collection may not exist

    async def _teardown_neo4j_async(self, workspace_id: str, corr_id: str):
        """Async: Delete Neo4j workspace data."""
        from app.graph.neo4j_store import get_neo4j_store

        neo4j = get_neo4j_store()
        neo4j.execute_query(
            "MATCH (n {workspace_id: $workspace_id}) DETACH DELETE n",
            workspace_id=workspace_id,
        )
        logger.info(f"[{corr_id}] Neo4j workspace deleted: {workspace_id}")

    async def _teardown_bm25_async(self, workspace_id: str, corr_id: str):
        """Async: Delete BM25 index file for workspace."""
        path = get_bm25_index_path(workspace_id)
        if path.exists():
            path.unlink()
            logger.info(f"[{corr_id}] BM25 index deleted: {path}")

    # -- Sync wrappers for backward compatibility ---------------------------

    def provision(
        self,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> WorkspaceResources:
        """
        Sync wrapper — prefers async version in new code.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_provision():
            return await self.provision_async(workspace_id, correlation_id)

        return run_async_in_task(_do_provision)

    def teardown(
        self,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """
        Sync wrapper — prefers async version in new code.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_teardown():
            return await self.teardown_async(workspace_id, correlation_id)

        return run_async_in_task(_do_teardown)

    def get_usage_stats(
        self,
        workspace_id: str,
        correlation_id: Optional[str] = None,
    ) -> dict:
        """
        Sync wrapper — prefers async version in new code.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_stats():
            return await self.get_usage_stats_async(workspace_id, correlation_id)

        return run_async_in_task(_do_stats)


def get_workspace_metadata() -> dict[str, Any]:
    """✅ NEW: Return workspace manager metadata for debugging."""
    return {
        "provision_timeout_seconds": _PROVISION_TIMEOUT,
        "retry_config": {
            "max_attempts": _PROVISION_RETRY_CONFIG.max_attempts,
            "backoff_base": _PROVISION_RETRY_CONFIG.backoff_base,
            "backoff_max": _PROVISION_RETRY_CONFIG.backoff_max,
        },
        "supported_stores": ["chromadb", "neo4j", "bm25", "postgres"],
        "async_safe": True,
        "graceful_degradation": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "WorkspaceManager",
    "WorkspaceResources",
    "get_workspace_metadata",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.workspace.manager) ---
# ========================================================================

