# backend/app/core/regional_language_processor.py
"""Indian regional language support: Tanglish, cross-language search, Indian formats."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Script detection Unicode ranges ──────────────────────────

_SCRIPT_RANGES = {
    "tamil":     (0x0B80, 0x0BFF),
    "telugu":    (0x0C00, 0x0C7F),
    "kannada":   (0x0C80, 0x0CFF),
    "malayalam": (0x0D00, 0x0D7F),
    "hindi":     (0x0900, 0x097F),
    "bengali":   (0x0980, 0x09FF),
    "gujarati":  (0x0A80, 0x0AFF),
    "punjabi":   (0x0A00, 0x0A7F),
    "odia":      (0x0B00, 0x0B7F),
}

# ── Indian number format ──────────────────────────────────────

_LAKH = 100_000
_CRORE = 10_000_000

_INDIAN_NUMBER_WORDS = {
    "lakh": _LAKH,
    "lakhs": _LAKH,
    "lac": _LAKH,
    "lacs": _LAKH,
    "crore": _CRORE,
    "crores": _CRORE,
    "cr": _CRORE,
}

# ── Validation patterns ───────────────────────────────────────

_PAN_PATTERN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b")
_AADHAAR_PATTERN = re.compile(r"\b[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}\b")
_GSTIN_PATTERN = re.compile(
    r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{1}Z[A-Z0-9]{1}\b"
)
_INDIAN_MOBILE_PATTERN = re.compile(r"\b(?:\+91[\s\-]?)?[6-9]\d{9}\b")
_PINCODE_PATTERN = re.compile(r"\b[1-9][0-9]{5}\b")

# ── Tanglish keyword normalization ───────────────────────────

_TANGLISH_MAP = {
    # Query-level synonyms for common doc concepts
    "aadayam": "income",
    "selevu": "expense",
    "niraivu": "balance",
    "ozhukkam": "compliance",
    "ozhukkukaaran": "compliance officer",
    "ottam": "total",
    "thodakkam": "start date",
    "mudivu": "end date",
    "gnaabagam": "memory",
    "pudiya": "new",
    "payar": "name",
    "thedi": "search",
    "kattam": "table",
    "oozhiyar": "employee",
    "nidhi": "fund",
    "vanigam": "business",
    "seimurai": "procedure",
    "amaippu": "organization",
    "arasaangam": "government",
    "vilakku": "explanation",
    "kanam": "amount",
}


def detect_script(text: str) -> Optional[str]:
    """Detect the dominant Indian script in text."""
    counts: dict[str, int] = {lang: 0 for lang in _SCRIPT_RANGES}
    for ch in text:
        cp = ord(ch)
        for lang, (start, end) in _SCRIPT_RANGES.items():
            if start <= cp <= end:
                counts[lang] += 1
    if not any(counts.values()):
        return None
    return max(counts, key=counts.get)


def contains_indian_script(text: str) -> bool:
    return detect_script(text) is not None


def normalize_tanglish_query(query: str) -> str:
    """Expand Tanglish keywords to English for cross-language retrieval."""
    tokens = query.lower().split()
    expanded = [_TANGLISH_MAP.get(t, t) for t in tokens]
    result = " ".join(expanded)
    if result != query.lower():
        logger.debug(f"Tanglish expand: '{query}' → '{result}'")
    return result


def normalize_indian_number(text: str) -> Optional[float]:
    """Parse Indian number expressions like '5.2 crores', '18 lakhs'."""
    text = text.lower().strip()
    for word, multiplier in _INDIAN_NUMBER_WORDS.items():
        pattern = re.compile(rf"([\d,]+(?:\.\d+)?)\s*{word}", re.IGNORECASE)
        m = pattern.search(text)
        if m:
            try:
                num = float(m.group(1).replace(",", ""))
                return num * multiplier
            except ValueError:
                pass
    # Plain number with commas (e.g., "1,00,000")
    clean = re.sub(r"[^\d.]", "", text)
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def extract_indian_entities(text: str) -> dict[str, list[str]]:
    """Extract PAN, Aadhaar, GSTIN, mobile numbers, and pincodes from text."""
    return {
        "pan": _PAN_PATTERN.findall(text),
        "aadhaar": [a.replace(" ", "").replace("-", "") for a in _AADHAAR_PATTERN.findall(text)],
        "gstin": _GSTIN_PATTERN.findall(text),
        "mobile": _INDIAN_MOBILE_PATTERN.findall(text),
        "pincode": _PINCODE_PATTERN.findall(text),
    }


def validate_pan(pan: str) -> bool:
    return bool(_PAN_PATTERN.fullmatch(pan.strip().upper()))


def validate_gstin(gstin: str) -> bool:
    return bool(_GSTIN_PATTERN.fullmatch(gstin.strip().upper()))


def validate_aadhaar(aadhaar: str) -> bool:
    clean = re.sub(r"[\s\-]", "", aadhaar)
    return bool(re.fullmatch(r"[2-9]\d{11}", clean))


def parse_indian_date(text: str) -> Optional[str]:
    """Parse Indian date formats: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY → ISO."""
    patterns = [
        r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100:
                return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def preprocess_regional_query(query: str) -> dict[str, Any]:
    """Full preprocessing pipeline for Indian language queries."""
    script = detect_script(query)
    tanglish_normalized = normalize_tanglish_query(query)
    entities = extract_indian_entities(query)
    amounts = []
    for token in query.split():
        n = normalize_indian_number(token)
        if n:
            amounts.append(n)

    return {
        "original_query": query,
        "normalized_query": tanglish_normalized,
        "detected_script": script,
        "extracted_entities": entities,
        "extracted_amounts": amounts,
        "is_multilingual": script is not None or tanglish_normalized != query.lower(),
    }


if __name__ == "__main__":
    print("Regional language processor smoke test")

    assert validate_pan("ABCDE1234F")
    assert not validate_pan("INVALID")
    print("PAN validation OK")

    assert validate_gstin("27AAPFU0939F1ZV")
    assert not validate_gstin("BADGSTIN")
    print("GSTIN validation OK")

    assert validate_aadhaar("2345 6789 0123")
    print("Aadhaar validation OK")

    n = normalize_indian_number("5.2 crores")
    assert n == 52_000_000.0, f"Expected 52M, got {n}"
    n2 = normalize_indian_number("18 lakhs")
    assert n2 == 1_800_000.0, f"Expected 1.8M, got {n2}"
    print("Indian number normalization OK")

    q = preprocess_regional_query("aadayam kanam ottam")
    assert q["normalized_query"] == "income amount total"
    print("Tanglish normalization OK")

    d = parse_indian_date("31/12/2024")
    assert d == "2024-12-31", f"Got {d}"
    print("Date parsing OK")

    entities = extract_indian_entities("PAN: ABCDE1234F GSTIN: 27AAPFU0939F1ZV")
    assert "ABCDE1234F" in entities["pan"]
    print("Entity extraction OK")

    print("All regional language processor checks passed")
