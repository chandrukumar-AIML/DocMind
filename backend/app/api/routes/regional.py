"""Indian regional language support API: query normalization, entity extraction, validation."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id
from app.core.regional_language_processor import (
    preprocess_regional_query,
    validate_pan,
    validate_gstin,
    validate_aadhaar,
    normalize_indian_number,
    extract_indian_entities,
    detect_script,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/regional", tags=["regional"])


# ── Pydantic models ────────────────────────────────────────────


class QueryPreprocessRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class EntityExtractionRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)


class ValidationRequest(BaseModel):
    value: str = Field(..., min_length=1, max_length=50)
    type: str = Field(..., pattern="^(pan|gstin|aadhaar)$")


class NumberParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=100)


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/preprocess-query")
async def preprocess_query(
    req: QueryPreprocessRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("regional-qp")
    result = preprocess_regional_query(req.query)
    result["correlation_id"] = corr_id
    return result


@router.post("/extract-entities")
async def extract_entities(
    req: EntityExtractionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("regional-ent")
    entities = extract_indian_entities(req.text)
    script = detect_script(req.text)
    return {
        "entities": entities,
        "detected_script": script,
        "total_entities": sum(len(v) for v in entities.values()),
        "correlation_id": corr_id,
    }


@router.post("/validate")
async def validate_indian_id(
    req: ValidationRequest,
) -> dict[str, Any]:
    corr_id = generate_correlation_id("regional-val")
    validators = {
        "pan": validate_pan,
        "gstin": validate_gstin,
        "aadhaar": validate_aadhaar,
    }
    is_valid = validators[req.type](req.value)
    return {
        "value": req.value,
        "type": req.type,
        "is_valid": is_valid,
        "correlation_id": corr_id,
    }


@router.post("/parse-number")
async def parse_number(
    req: NumberParseRequest,
) -> dict[str, Any]:
    corr_id = generate_correlation_id("regional-num")
    parsed = normalize_indian_number(req.text)
    return {
        "input": req.text,
        "parsed_value": parsed,
        "formatted": f"₹{parsed:,.2f}" if parsed else None,
        "correlation_id": corr_id,
    }


@router.get("/scripts")
async def list_supported_scripts() -> dict[str, Any]:
    return {
        "scripts": [
            "tamil",
            "telugu",
            "kannada",
            "malayalam",
            "hindi",
            "bengali",
            "gujarati",
            "punjabi",
            "odia",
        ],
        "features": [
            "Tanglish query normalization",
            "Cross-language search (transliteration-aware)",
            "Indian date formats (DD/MM/YYYY)",
            "Indian number formats (lakhs, crores)",
            "PAN validation",
            "Aadhaar validation",
            "GSTIN validation",
            "Indian mobile number detection",
            "Pincode detection",
        ],
    }

