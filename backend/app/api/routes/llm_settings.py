"""Per-workspace LLM provider (BYOK) settings — configure, test, and clear."""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import AuthenticatedUser, require_workspace_admin
from app.core.llm_providers import PROVIDER_REGISTRY
from app.core.workspace_llm_config import (
    delete_workspace_llm_config,
    get_workspace_llm_config_masked,
    upsert_workspace_llm_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-settings", tags=["llm-settings"])


class LlmSettingsUpsertRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=50)
    api_key: str = Field(..., min_length=1, max_length=500)
    model: Optional[str] = Field(None, max_length=100)
    base_url: Optional[str] = Field(None, max_length=500)


@router.get("/providers")
async def list_providers() -> dict:
    return {
        "providers": [
            {"id": key, "label": v["label"], "default_model": v["default_model"], "base_url": v["base_url"]}
            for key, v in PROVIDER_REGISTRY.items()
        ]
    }


@router.get("")
async def get_settings_route(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    config = await get_workspace_llm_config_masked(user.workspace_id)
    if config is None:
        return {"configured": False}
    return {"configured": True, **config}


@router.put("")
async def update_settings(
    body: LlmSettingsUpsertRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    if body.provider not in PROVIDER_REGISTRY:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{body.provider}'. Supported: {list(PROVIDER_REGISTRY)}",
        )
    try:
        result = await upsert_workspace_llm_config(
            workspace_id=user.workspace_id,
            provider=body.provider,
            api_key=body.api_key,
            model=body.model,
            base_url=body.base_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"configured": True, **result}


@router.delete("")
async def clear_settings(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    deleted = await delete_workspace_llm_config(user.workspace_id)
    return {"deleted": deleted}


@router.post("/test")
async def test_settings(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    """Fire a trivial completion against the workspace's configured provider."""
    from langchain_core.messages import HumanMessage

    from app.core.llm_pool import get_llm_for_workspace
    from app.core.workspace_llm_config import get_workspace_llm_config

    config = await get_workspace_llm_config(user.workspace_id)
    if config is None:
        raise HTTPException(status_code=404, detail="No LLM settings configured for this workspace")

    start = time.monotonic()
    try:
        llm = await get_llm_for_workspace(user.workspace_id, streaming=False)
        response = await llm.ainvoke([HumanMessage(content="Reply with just the word: OK")])
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "success": True,
            "provider": config.provider,
            "model": config.model,
            "latency_ms": latency_ms,
            "sample_response": (response.content or "")[:100],
        }
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        logger.warning(f"LLM test failed for workspace {user.workspace_id}: {e}")
        return {
            "success": False,
            "provider": config.provider,
            "model": config.model,
            "latency_ms": latency_ms,
            "error": str(e)[:300],
        }
