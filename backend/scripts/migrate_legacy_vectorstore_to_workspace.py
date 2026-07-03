# backend/scripts/migrate_legacy_vectorstore_to_workspace.py
"""
One-time migration: copy documents/registry/parents from the legacy global Chroma
collections (documind_chunks / document_registry / parents) into a workspace-scoped
set of collections (docs_<workspace_id> / docs_<workspace_id>_registry /
docs_<workspace_id>_parents), then rebuild that workspace's FAISS index from the
migrated data.

Needed because ChromaVectorStore/FAISSVectorStore/VectorStoreManager used to share one
global collection/index across every workspace (see app/vectorstore/chroma_store.py,
faiss_store.py, store_manager.py) — after fixing that isolation bug, any workspace whose
documents lived in the legacy global collection would otherwise appear to have zero
documents, since docs_<workspace_id> starts out empty.

Every write is an upsert keyed by the same ids the app already uses (chunk_id /
sanitize_chroma_key(source_file, prefix="doc")), so re-running this script is always
safe and never duplicates data. The legacy collections are left untouched — this is
additive only.

Usage:
    python -m scripts.migrate_legacy_vectorstore_to_workspace --workspace-id <uuid> --dry-run
    python -m scripts.migrate_legacy_vectorstore_to_workspace --workspace-id <uuid>
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 🔧 ROBUST PATH SETUP (matches other scripts in this folder)
current_file = Path(__file__).resolve()
for parent in current_file.parents:
    if parent.name == "backend" and (parent / "requirements.txt").exists():
        backend_root = parent
        break
else:
    backend_root = current_file.parents[1]
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("migrate_legacy_vectorstore")


def _copy_collection(client, source_name: str, dest_name: str, dry_run: bool) -> int:
    """Upsert every row from source_name into dest_name. Returns row count copied."""
    try:
        source = client.get_collection(source_name)
    except Exception:
        logger.info(f"Source collection '{source_name}' does not exist — skipping (0 rows)")
        return 0

    total = source.count()
    if total == 0:
        logger.info(f"Source collection '{source_name}' is empty — skipping")
        return 0

    dest = client.get_or_create_collection(dest_name)
    batch_size = 500
    copied = 0

    for offset in range(0, total, batch_size):
        batch = source.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas", "embeddings"],
        )
        ids = batch.get("ids") or []
        if not ids:
            continue
        copied += len(ids)
        if dry_run:
            continue
        dest.upsert(
            ids=ids,
            documents=batch.get("documents"),
            metadatas=batch.get("metadatas"),
            embeddings=batch.get("embeddings"),
        )

    logger.info(f"{'[dry-run] Would copy' if dry_run else 'Copied'} {copied} rows: '{source_name}' -> '{dest_name}'")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace-id", required=True, help="Target workspace UUID that owns the legacy data")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing anything")
    parser.add_argument(
        "--source-collection",
        default=None,
        help="Override legacy Chroma collection name (default: settings.chroma_collection_name)",
    )
    args = parser.parse_args()

    from app.config import get_settings
    from app.core.workspace_utils import get_chroma_collection_name, get_faiss_index_path, validate_workspace_id
    from app.vectorstore.chroma_store import _get_chroma_client

    settings = get_settings()
    workspace_id = args.workspace_id
    try:
        # Not using validate_workspace_id() directly on workspace_id since real
        # workspace ids are UUIDs — get_chroma_collection_name() already validates.
        dest_collection = get_chroma_collection_name(workspace_id)
    except ValueError as e:
        logger.error(f"Invalid --workspace-id: {e}")
        return 1

    legacy_collection = args.source_collection or settings.chroma_collection_name
    legacy_registry = "document_registry"
    legacy_parents = "parents"
    dest_registry = f"{dest_collection}_registry"
    dest_parents = f"{dest_collection}_parents"

    logger.info(f"Migrating workspace_id={workspace_id}")
    logger.info(f"  {legacy_collection} -> {dest_collection}")
    logger.info(f"  {legacy_registry} -> {dest_registry}")
    logger.info(f"  {legacy_parents} -> {dest_parents}")
    if args.dry_run:
        logger.info("DRY RUN — no data will be written")

    client = _get_chroma_client(settings.chroma_persist_dir)

    chunks_copied = _copy_collection(client, legacy_collection, dest_collection, args.dry_run)
    registry_copied = _copy_collection(client, legacy_registry, dest_registry, args.dry_run)
    parents_copied = _copy_collection(client, legacy_parents, dest_parents, args.dry_run)

    faiss_vectors = 0
    if not args.dry_run and chunks_copied > 0:
        logger.info("Rebuilding FAISS index for workspace from migrated Chroma data...")
        from app.vectorstore.chroma_store import ChromaVectorStore
        from app.vectorstore.faiss_store import FAISSVectorStore
        from app.vectorstore.embeddings import CachedOpenAIEmbeddings

        embeddings = CachedOpenAIEmbeddings(api_key=settings.effective_embedding_api_key)
        new_chroma_store = ChromaVectorStore(embeddings, collection_name=dest_collection)
        new_faiss_store = FAISSVectorStore(
            embeddings,
            new_chroma_store,
            index_path=get_faiss_index_path(workspace_id),
        )
        faiss_vectors = new_faiss_store._count_public()
        logger.info(f"FAISS index rebuilt: {faiss_vectors} vectors")

    logger.info("=" * 60)
    logger.info(
        f"{'DRY RUN SUMMARY' if args.dry_run else 'MIGRATION COMPLETE'}: "
        f"chunks={chunks_copied}, registry_rows={registry_copied}, "
        f"parent_rows={parents_copied}, faiss_vectors={faiss_vectors}"
    )
    logger.info("Legacy collections were left untouched — verify the workspace's document")
    logger.info("list/search in the UI before manually cleaning up the legacy collections.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
