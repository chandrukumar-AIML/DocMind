"""GSTIN Lookup Integration — Feature #10 (also covers Feature #11 from plan).

Validates a GSTIN format client-side and enriches it with decoded information
(state, PAN, entity type, registration type) without external API dependency.
Optionally calls the GST public API if configured.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import get_current_user, AuthenticatedUser

router = APIRouter(prefix="/gstin", tags=["gstin"])

# ── GSTIN structure ──────────────────────────────────────────────────────────
# Format: 2-digit state code + 10-char PAN + 1 entity number + Z + 1 checksum

_STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman & Diu", "26": "Dadra & Nagar Haveli", "27": "Maharashtra",
    "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman & Nicobar", "36": "Telangana",
    "37": "Andhra Pradesh (new)", "38": "Ladakh", "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

_PAN_TYPE = {
    "P": "Individual",
    "C": "Company",
    "H": "HUF",
    "F": "Firm / LLP",
    "A": "AOP",
    "T": "Trust",
    "B": "Body of Individuals",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
}

_GSTIN_RE = re.compile(r"^([0-9]{2})([A-Z]{5}[0-9]{4}[A-Z])([1-9A-Z])Z[0-9A-Z]$")


def _validate_gstin(gstin: str) -> dict:
    g = gstin.strip().upper()
    if len(g) != 15:
        return {"valid": False, "error": f"GSTIN must be 15 characters (got {len(g)})"}

    m = _GSTIN_RE.match(g)
    if not m:
        return {"valid": False, "error": "GSTIN format invalid — expected: 2-digit state + PAN + entity no + Z + checksum"}

    state_code = m.group(1)
    pan        = m.group(2)
    entity_no  = m.group(3)

    pan_4th = pan[3]
    entity_type = _PAN_TYPE.get(pan_4th, f"Unknown ({pan_4th})")
    state = _STATE_CODES.get(state_code, f"Unknown state ({state_code})")

    return {
        "valid": True,
        "gstin": g,
        "state_code": state_code,
        "state": state,
        "pan": pan,
        "entity_type": entity_type,
        "entity_number": entity_no,
        "registration_type": "Regular" if entity_no.isdigit() and int(entity_no) == 1 else "Additional Place / Branch",
    }


# ── Schema ────────────────────────────────────────────────────────────────────

class GstinResult(BaseModel):
    valid:             bool
    gstin:             Optional[str] = None
    state_code:        Optional[str] = None
    state:             Optional[str] = None
    pan:               Optional[str] = None
    entity_type:       Optional[str] = None
    entity_number:     Optional[str] = None
    registration_type: Optional[str] = None
    error:             Optional[str] = None


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/validate", response_model=GstinResult)
async def validate_gstin(
    gstin: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Decode and validate a GSTIN — returns state, PAN, entity type."""
    return GstinResult(**_validate_gstin(gstin))


@router.get("/extract", response_model=list[GstinResult])
async def extract_gstins(
    text: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Extract all GSTINs from a block of text and validate each."""
    found = re.findall(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", text.upper())
    unique = list(dict.fromkeys(found))[:20]  # dedupe, max 20
    return [GstinResult(**_validate_gstin(g)) for g in unique]
