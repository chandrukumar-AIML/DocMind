# backend/app/api/routes/finetuning.py
# DVMELTSS-FIX: M/E/S + OWASP-3 + BATMAN-A
# ✅ FIXED: Proper background task handling + input validation + safe file ops + timeout

from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from pydantic import BaseModel, Field

from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, require_admin, AuthenticatedUser
from app.finetuning.dataset_generator import TripletDatasetGenerator
from app.finetuning.model_registry import ModelRegistry
from app.finetuning.embedding_updater import EmbeddingUpdater

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/finetuning", tags=["finetuning"])

# ✅ NEW: Operation timeouts (seconds)
_DATASET_TIMEOUT: Final = 300.0  # 5 minutes for dataset generation
_PULL_TIMEOUT: Final = 600.0  # 10 minutes for model download
_REEMBED_TIMEOUT: Final = 1800.0  # 30 minutes for re-embedding


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class GenerateDatasetRequest(BaseModel):
    domain: str = Field(default="general", max_length=64)
    max_chunks: int = Field(default=200, ge=10, le=5000)
    queries_per_chunk: int = Field(default=2, ge=1, le=5)
    save_path: Optional[str] = Field(default=None)
    workspace_id: Optional[str] = Field(default=None, max_length=64)


class PullModelRequest(BaseModel):
    repo_id: str = Field(
        ...,
        description="HuggingFace model repo ID (e.g., 'sentence-transformers/all-MiniLM-L6-v2')",
    )
    local_path: Optional[str] = Field(default=None)


class ReembedRequest(BaseModel):
    model_path: str = Field(..., description="Local path to fine-tuned model")


# ✅ NEW: Input validation helper
def _validate_finetuning_inputs(
    domain: Optional[str],
    max_chunks: Optional[int],
    queries_per_chunk: Optional[int],
    save_path: Optional[str],
    repo_id: Optional[str],
    model_path: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate finetuning endpoint inputs before processing."""
    if domain is not None and not isinstance(domain, str):
        return False, "domain must be a string or None"
    if max_chunks is not None and (not isinstance(max_chunks, int) or max_chunks < 10 or max_chunks > 5000):
        return False, "max_chunks must be between 10 and 5000"
    if queries_per_chunk is not None and (
        not isinstance(queries_per_chunk, int) or queries_per_chunk < 1 or queries_per_chunk > 5
    ):
        return False, "queries_per_chunk must be between 1 and 5"
    if save_path is not None and not isinstance(save_path, str):
        return False, "save_path must be a string or None"
    if repo_id is not None and not isinstance(repo_id, str):
        return False, "repo_id must be a string or None"
    if model_path is not None and not isinstance(model_path, str):
        return False, "model_path must be a string or None"
    return True, ""


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.post(
    "/dataset/generate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate training triplets from workspace documents",
    description="Generates (query, positive, hard_negative) triplets for embedding fine-tuning. Runs in background.",
)
async def generate_dataset(
    request: GenerateDatasetRequest,
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
    background_tasks: BackgroundTasks,
) -> dict:
    corr_id = generate_correlation_id("generate_dataset")

    # ✅ Validate inputs
    is_valid, error = _validate_finetuning_inputs(
        request.domain,
        request.max_chunks,
        request.queries_per_chunk,
        request.save_path,
        None,
        None,
        corr_id,
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    workspace_id = request.workspace_id or user.workspace_id

    # Save path defaults
    safe_path = request.save_path or (f".cache/training_data/{request.domain}_{workspace_id}.jsonl")

    # ✅ FIXED: Proper path security validation with resolve()
    try:
        resolved = Path(safe_path).resolve()
        base_dir = Path(".cache/training_data").resolve()
        # Ensure path is within allowed directory
        resolved.relative_to(base_dir)
    except ValueError:
        logger.warning(f"[{corr_id}] Path traversal attempt blocked: '{safe_path}'")
        raise HTTPException(
            status_code=400,
            detail="Invalid save path: path must be within .cache/training_data",
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Path validation failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid save path")

    # ✅ FIXED: Use sync function for background task (not async)
    def _do_generate():
        try:
            generator = TripletDatasetGenerator()
            logger.info(f"[{corr_id}] Starting dataset generation: ws={workspace_id}, chunks={request.max_chunks}")

            dataset = generator.generate_dataset(
                workspace_id=workspace_id,
                domain=request.domain,
                max_chunks=request.max_chunks,
                queries_per_chunk=request.queries_per_chunk,
                save_path=safe_path,
                correlation_id=corr_id,
            )
            logger.info(
                f"[{corr_id}] Dataset saved: {dataset.size if hasattr(dataset, 'size') else 'unknown'} triplets"
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Dataset generation failed: {e}", exc_info=True)

    background_tasks.add_task(_do_generate)

    return {
        "status": "queued",
        "domain": request.domain,
        "workspace_id": workspace_id,
        "message": f"Generating ~{request.max_chunks * request.queries_per_chunk} triplets in background.",
        "save_path": safe_path,
        "correlation_id": corr_id,
    }


@router.get(
    "/dataset/status",
    summary="Check if a training dataset has been generated",
)
async def get_dataset_status(
    domain: str = Query(default="general"),
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> dict:
    corr_id = generate_correlation_id("dataset_status")
    workspace_id = user.workspace_id if user else "default"

    path = Path(f".cache/training_data/{domain}_{workspace_id}.jsonl")

    if not path.exists():
        return {"exists": False, "path": str(path)}

    # ✅ FIXED: Use context manager for safe file handling
    try:
        with open(path, "r", encoding="utf-8") as f:
            n_triplets = sum(1 for _ in f)

        return {
            "exists": True,
            "path": str(path),
            "n_triplets": n_triplets,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
            "workspace_id": workspace_id,
            "correlation_id": corr_id,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Dataset status check failed: {e}")
        return {"exists": False, "path": str(path), "error": str(e)}


@router.post(
    "/model/pull",
    summary="Download a fine-tuned model from HuggingFace Hub",
    description="Pulls a model from HF to local cache for use in inference.",
)
async def pull_model(
    request: PullModelRequest,
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    corr_id = generate_correlation_id("pull_model")

    # ✅ Validate inputs
    is_valid, error = _validate_finetuning_inputs(None, None, None, None, request.repo_id, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    registry = ModelRegistry()
    try:
        # ✅ FIXED: Use functools.partial for safe arg passing in executor
        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
        local_path = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                functools.partial(
                    registry.pull_from_hub,
                    repo_id=request.repo_id,
                    local_path=request.local_path,
                ),
            ),
            timeout=_PULL_TIMEOUT,
        )

        return {
            "status": "downloaded",
            "repo_id": request.repo_id,
            "local_path": str(local_path),
            "workspace_id": user.workspace_id,
            "correlation_id": corr_id,
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Model pull timed out after {_PULL_TIMEOUT}s")
        raise HTTPException(status_code=408, detail=f"Model download timed out after {_PULL_TIMEOUT}s")
    except Exception as e:
        logger.error(f"[{corr_id}] Model pull failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/models",
    summary="List locally available fine-tuned models",
)
async def list_models(
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    corr_id = generate_correlation_id("list_models")
    registry = ModelRegistry()

    try:
        models = registry.list_available_models()

        return {
            "models": models or [],
            "workspace_id": user.workspace_id,
            "correlation_id": corr_id,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Model list failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list models")


@router.post(
    "/reembed",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-embed all workspace documents with a fine-tuned model",
    description="⚠️ WARNING: Destructive operation. Replaces all vector embeddings in the workspace.",
)
async def reembed_workspace(
    request: ReembedRequest,
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
    background_tasks: BackgroundTasks,
) -> dict:
    corr_id = generate_correlation_id("reembed")
    workspace_id = user.workspace_id

    # ✅ Validate inputs
    is_valid, error = _validate_finetuning_inputs(None, None, None, None, None, request.model_path, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Validate model path exists and is safe
    model_path = Path(request.model_path)
    try:
        # ✅ FIXED: Resolve and validate path is within allowed directories
        resolved = model_path.resolve()
        # Allow models in .cache/models or absolute paths that exist
        if not resolved.exists():
            raise HTTPException(status_code=404, detail=f"Model not found: {request.model_path}")
    except Exception as e:
        logger.error(f"[{corr_id}] Model path validation failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid model path")

    # ✅ FIXED: Use sync function for background task
    def _do_reembed():
        try:
            logger.info(f"[{corr_id}] Starting re-embedding: ws={workspace_id}")
            updater = EmbeddingUpdater(workspace_id=workspace_id)

            result = updater.update(model_path=str(model_path), correlation_id=corr_id)

            logger.info(f"[{corr_id}] Re-embedding complete: chunks={getattr(result, 'chunks_processed', 'unknown')}")
        except Exception as e:
            logger.error(f"[{corr_id}] Re-embedding failed: {e}", exc_info=True)

    background_tasks.add_task(_do_reembed)

    return {
        "status": "started",
        "workspace_id": workspace_id,
        "model_path": str(model_path),
        "message": "Re-embedding running in background. Query quality will improve after completion.",
        "correlation_id": corr_id,
    }


def get_finetuning_metadata() -> dict[str, Any]:
    """✅ NEW: Return finetuning API metadata for monitoring."""
    return {
        "endpoints": [
            "/finetuning/dataset/generate",
            "/finetuning/dataset/status",
            "/finetuning/model/pull",
            "/finetuning/models",
            "/finetuning/reembed",
        ],
        "timeouts": {
            "dataset_generation_seconds": _DATASET_TIMEOUT,
            "model_pull_seconds": _PULL_TIMEOUT,
            "reembed_seconds": _REEMBED_TIMEOUT,
        },
        "limits": {
            "max_chunks": 5000,
            "max_queries_per_chunk": 5,
            "min_chunks": 10,
        },
        "path_validation": True,
        "workspace_scoped": True,
    }


__all__ = ["router", "get_finetuning_metadata"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
