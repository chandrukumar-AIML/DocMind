# backend/app/api/routes/templates.py
"""Extraction template builder API: create, list, extract, and view results."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id
from app.core.template_extractor import (
    BUILTIN_TEMPLATES,
    run_extraction,
)
from app.database.engine import async_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])

_VALID_FIELD_TYPES = {"string", "number", "date", "boolean", "list", "email", "phone"}


# ── Pydantic models ────────────────────────────────────────────


class TemplateField(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z_][a-z0-9_]*$")
    type: str = Field(...)
    description: str = Field(..., min_length=1, max_length=300)
    required: bool = False


class TemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    fields: list[TemplateField] = Field(..., min_length=1)


class ExtractionRunRequest(BaseModel):
    template_id: str
    source_file: str = Field(..., min_length=1, max_length=1024)


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/builtins")
async def list_builtins() -> dict[str, Any]:
    return {
        "templates": [
            {"slug": slug, "name": tmpl["name"], "field_count": len(tmpl["fields"])}
            for slug, tmpl in BUILTIN_TEMPLATES.items()
        ],
        "total": len(BUILTIN_TEMPLATES),
    }


@router.get("/builtins/{slug}")
async def get_builtin_template(slug: str) -> dict[str, Any]:
    tmpl = BUILTIN_TEMPLATES.get(slug)
    if not tmpl:
        raise HTTPException(status_code=404, detail=f"Built-in template '{slug}' not found")
    return {"slug": slug, **tmpl}


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_template(
    req: TemplateCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("tpl-create")

    for field in req.fields:
        if field.type not in _VALID_FIELD_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid field type '{field.type}'. Valid: {_VALID_FIELD_TYPES}",
            )

    tmpl_id = str(uuid.uuid4())
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO extraction_templates
                    (id, workspace_id, name, fields, is_builtin, created_by)
                VALUES
                    (:id, :ws, :name, CAST(:fields AS jsonb), FALSE, :by)
            """),
                {
                    "id": tmpl_id,
                    "ws": user.workspace_id,
                    "name": req.name,
                    "fields": json.dumps([f.model_dump() for f in req.fields]),
                    "by": user.user_id,
                },
            )
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to create template: {e}")
        raise HTTPException(status_code=500, detail="Failed to create template")

    return {"template_id": tmpl_id, "name": req.name, "correlation_id": corr_id}


@router.get("/list")
async def list_templates(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("tpl-list")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(
                text("""
                SELECT id, name, is_builtin, created_at,
                       jsonb_array_length(fields) as field_count
                FROM extraction_templates
                WHERE workspace_id = :ws
                ORDER BY created_at DESC
            """),
                {"ws": user.workspace_id},
            )
            templates = rows.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list templates: {e}")

    return {
        "templates": [
            {
                "template_id": str(t[0]),
                "name": t[1],
                "is_builtin": t[2],
                "created_at": t[3].isoformat() if t[3] else None,
                "field_count": t[4] or 0,
            }
            for t in templates
        ],
        "builtins": list(BUILTIN_TEMPLATES.keys()),
        "total": len(templates),
        "correlation_id": corr_id,
    }


@router.post("/extract")
async def extract_with_template(
    req: ExtractionRunRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("tpl-extract")

    # Support builtin slug or UUID
    template_id = req.template_id
    if req.template_id in BUILTIN_TEMPLATES:
        # Seed builtin template into DB for this workspace if not exists
        builtin = BUILTIN_TEMPLATES[req.template_id]
        template_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user.workspace_id}:{req.template_id}"))
        try:
            async with async_engine.begin() as conn:
                await conn.execute(
                    text("""
                    INSERT INTO extraction_templates (id, workspace_id, name, slug, fields, is_builtin)
                    VALUES (:id, :ws, :name, :slug, CAST(:fields AS jsonb), TRUE)
                    ON CONFLICT (id) DO NOTHING
                """),
                    {
                        "id": template_id,
                        "ws": user.workspace_id,
                        "name": builtin["name"],
                        "slug": req.template_id,
                        "fields": json.dumps(builtin["fields"]),
                    },
                )
        except Exception as e:
            logger.warning(f"[{corr_id}] Could not seed builtin template: {e}")

    try:
        result = await run_extraction(user.workspace_id, template_id, req.source_file)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[{corr_id}] Extraction failed: {e}")
        raise HTTPException(status_code=500, detail="Extraction failed")

    return result


@router.get("/results/{source_file:path}")
async def get_extraction_results(
    source_file: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("tpl-results")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(
                text("""
                SELECT er.id, et.name, er.fields, er.confidence, er.created_at
                FROM extraction_results er
                JOIN extraction_templates et ON er.template_id = et.id
                WHERE er.workspace_id = :ws AND er.source_file = :sf
                ORDER BY er.created_at DESC
                LIMIT 50
            """),
                {"ws": user.workspace_id, "sf": source_file},
            )
            results = rows.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch results: {e}")

    return {
        "source_file": source_file,
        "extractions": [
            {
                "result_id": str(r[0]),
                "template_name": r[1],
                "fields": r[2] if isinstance(r[2], dict) else {},
                "confidence": r[3] if isinstance(r[3], dict) else {},
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in results
        ],
        "correlation_id": corr_id,
    }


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Template routes smoke test")
        field = TemplateField(
            name="invoice_number",
            type="string",
            description="Invoice number",
            required=True,
        )
        assert field.type in _VALID_FIELD_TYPES
        req = TemplateCreateRequest(name="My Invoice", fields=[field])
        assert len(req.fields) == 1
        print("TemplateCreateRequest validation OK")
        print("Template routes checks passed")

    asyncio.run(smoke())
