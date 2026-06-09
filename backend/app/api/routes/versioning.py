# backend/app/api/routes/versioning.py
# DVMELTSS-FIX: M/E/S + ASCALE-L + Workspace isolation
# ✅ FIXED: Input validation + timeout handling + safe registry ops + proper error codes

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.versioning.registry import VersionRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/versioning", tags=["versioning"])

# ✅ NEW: Registry operation timeout (seconds)
_REGISTRY_TIMEOUT: Final = 30.0


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class VersionHistoryResponse(BaseModel):
    source_file: str
    workspace_id: str
    versions: list[dict]


class DiffResponse(BaseModel):
    version_1: int
    version_2: int
    diff_summary: str
    changes: list[dict]


# ✅ NEW: Input validation helper
def _validate_versioning_inputs(
    source_file: Optional[str],
    v1: Optional[int],
    v2: Optional[int],
    version_num: Optional[int],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate versioning endpoint inputs before processing."""
    if source_file is not None and not isinstance(source_file, str):
        return False, "source_file must be a string or None"
    if v1 is not None and (not isinstance(v1, int) or v1 < 1):
        return False, "v1 must be >= 1"
    if v2 is not None and (not isinstance(v2, int) or v2 < 1):
        return False, "v2 must be >= 1"
    if version_num is not None and (not isinstance(version_num, int) or version_num < 1):
        return False, "version_num must be >= 1"
    return True, ""


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.get(
    "/history/{source_file}",
    summary="Get version history for a document",
    description="Returns list of all versions for a specific file in the workspace.",
)
async def get_version_history(
    source_file: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> VersionHistoryResponse:
    corr_id = generate_correlation_id("version_history")

    # ✅ Validate inputs
    is_valid, error = _validate_versioning_inputs(source_file, None, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    registry = VersionRegistry()

    try:
        versions = await asyncio.wait_for(
            registry.list_versions(
                source_file=source_file,
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
            ),
            timeout=_REGISTRY_TIMEOUT,
        )

        # ✅ FIXED: Safe serialization with fallback
        version_dicts = []
        for v in versions or []:
            if hasattr(v, "model_dump"):
                version_dicts.append(v.model_dump())
            elif hasattr(v, "to_dict"):
                version_dicts.append(v.to_dict())
            elif isinstance(v, dict):
                version_dicts.append(v)
            else:
                version_dicts.append(str(v))

        return VersionHistoryResponse(
            source_file=source_file,
            workspace_id=user.workspace_id,
            versions=version_dicts,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Version history timed out after {_REGISTRY_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Version history failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve version history")


@router.get(
    "/diff/{source_file}",
    summary="Compute diff between two versions",
    description="Returns structured diff summary between version A and B.",
)
async def get_version_diff(
    source_file: str,
    v1: int = Query(..., ge=1, description="Older version number"),
    v2: int = Query(..., ge=1, description="Newer version number"),
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> DiffResponse:
    corr_id = generate_correlation_id("version_diff")

    # ✅ Validate inputs
    is_valid, error = _validate_versioning_inputs(source_file, v1, v2, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    workspace_id = user.workspace_id if user else "default"

    # ✅ FIXED: Strict v1 < v2 check
    if v1 >= v2:
        raise HTTPException(status_code=400, detail="v1 must be strictly less than v2")

    registry = VersionRegistry()

    try:
        diff = await asyncio.wait_for(
            registry.get_or_compute_diff(
                source_file=source_file,
                workspace_id=workspace_id,
                version_1=v1,
                version_2=v2,
                correlation_id=corr_id,
            ),
            timeout=_REGISTRY_TIMEOUT,
        )

        # ✅ FIXED: Safe serialization with fallback
        changes_list = []
        raw_changes = getattr(diff, "changes", None) or getattr(diff, "modified_sections", []) or []
        for c in raw_changes:
            if hasattr(c, "model_dump"):
                changes_list.append(c.model_dump())
            elif hasattr(c, "to_dict"):
                changes_list.append(c.to_dict())
            elif isinstance(c, dict):
                changes_list.append(c)
            else:
                changes_list.append(str(c))

        return DiffResponse(
            version_1=v1,
            version_2=v2,
            diff_summary=getattr(diff, "summary", None) or getattr(diff, "change_summary", ""),
            changes=changes_list,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Diff computation timed out after {_REGISTRY_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Diff computation timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Diff computation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute diff")


@router.get(
    "/{source_file}/version/{version_num}",
    summary="Get details of a specific version",
    description="Returns metadata and chunk stats for a specific version.",
)
async def get_version_details(
    source_file: str,
    version_num: int,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> dict:
    corr_id = generate_correlation_id("version_details")

    # ✅ Validate inputs
    is_valid, error = _validate_versioning_inputs(source_file, None, None, version_num, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    workspace_id = user.workspace_id if user else "default"

    registry = VersionRegistry()

    try:
        version = await asyncio.wait_for(
            registry.get_version(
                source_file=source_file,
                workspace_id=workspace_id,
                version_number=version_num,
                correlation_id=corr_id,
            ),
            timeout=_REGISTRY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Version details timed out after {_REGISTRY_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Version details failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve version details")

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # ✅ FIXED: Safe serialization with fallback
    if hasattr(version, "model_dump"):
        version_dict = version.model_dump()
    elif hasattr(version, "to_dict"):
        version_dict = version.to_dict()
    elif isinstance(version, dict):
        version_dict = version
    else:
        version_dict = {"version": str(version)}

    return {
        "source_file": source_file,
        "workspace_id": workspace_id,
        "correlation_id": corr_id,
        "version": version_dict,
    }


def get_versioning_api_metadata() -> dict[str, Any]:
    """✅ NEW: Return versioning API metadata for monitoring."""
    return {
        "endpoints": [
            "/versioning/history/{source_file}",
            "/versioning/diff/{source_file}",
            "/versioning/{source_file}/version/{version_num}",
        ],
        "timeout_seconds": _REGISTRY_TIMEOUT,
        "version_number_min": 1,
        "workspace_scoped": True,
        "diff_computation": True,
    }


__all__ = ["router", "get_versioning_api_metadata"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
