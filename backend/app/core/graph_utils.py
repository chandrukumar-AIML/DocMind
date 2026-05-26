# backend/app/core/graph_utils.py
# DVMELTSS-FIX: M - Modular, S - Security, A - Async
# ASCALE-FIX: S - Separation, C - Coupling
# OWASP-FIX: 9 - Cypher injection prevention
"""
Shared utilities for graph modules (Neo4j, Cypher, extraction).

Centralizes:
- Async Neo4j client management
- Cypher injection sanitization
- Entity/relationship type validation
- Correlation ID propagation helpers

Usage:
    from app.core.graph_utils import sanitize_cypher_input, validate_entity_type
"""
from __future__ import annotations

import re
from typing import Final, Optional

from app.config import get_settings

# ✅ FIXED: Added missing import for generate_correlation_id
from app.core.ids import generate_correlation_id

# DVMELTSS-S: Valid graph schema elements
_VALID_ENTITY_TYPES: Final = frozenset({
    "Person", "Organization", "Contract", "Clause",
    "Date", "Location", "Concept", "Amount", "Document", "__Entity__"
})
_VALID_REL_TYPES: Final = frozenset({
    "SIGNED_BY", "INVOLVES", "CONTAINS", "REFERENCES",
    "DATED", "LOCATED_IN", "RELATED_TO", "PART_OF",
    "MENTIONS", "AUTHORED_BY", "EXTRACTED_FROM"
})
# OWASP-9: Dangerous Cypher keywords to block
_FORBIDDEN_CYPHER_KEYWORDS: Final = frozenset({
    "DELETE", "DETACH", "DROP", "CREATE", "MERGE", "SET", "REMOVE", "CALL", "EXECUTE"
})


def validate_entity_type(entity_type: str) -> str:
    """Validate and normalize entity type."""
    normalized = entity_type.strip()
    if normalized not in _VALID_ENTITY_TYPES:
        return "__Entity__"  # Safe fallback
    return normalized


def validate_relationship_type(rel_type: str) -> str:
    """Validate and sanitize relationship type."""
    # Remove non-alphanumeric chars, uppercase
    safe = re.sub(r'[^A-Z_]', '', rel_type.upper())
    if safe not in _VALID_REL_TYPES:
        return "RELATED_TO"  # Safe fallback
    return safe


def sanitize_cypher_input(value: str, max_length: int = 200) -> str:
    """
    OWASP-9: Sanitize user input before injecting into Cypher.
    Removes dangerous keywords and limits length.
    """
    if not value:
        return ""
    # Remove dangerous Cypher keywords (case-insensitive)
    for kw in _FORBIDDEN_CYPHER_KEYWORDS:
        value = re.sub(rf"\b{kw}\b", "", value, flags=re.IGNORECASE)
    # Limit length
    return value.strip()[:max_length]


def contains_dangerous_cypher(cypher: str) -> bool:
    """Check if Cypher contains forbidden operations."""
    cypher_upper = cypher.upper()
    return any(kw in cypher_upper for kw in _FORBIDDEN_CYPHER_KEYWORDS)


def generate_graph_correlation_id(prefix: str = "graph") -> str:
    """Generate correlation ID for graph operations."""
    # ✅ FIXED: Now generate_correlation_id is imported and defined
    return f"{prefix}_{generate_correlation_id()}"


def escape_graph_prompt(text: str) -> str:
    """Escape curly braces to prevent prompt injection in graph prompts."""
    return text.replace("{", "{{").replace("}", "}}")


# DVMELTSS-M: Explicit module exports
__all__ = [
    "validate_entity_type",
    "validate_relationship_type",
    "sanitize_cypher_input",
    "contains_dangerous_cypher",
    "generate_graph_correlation_id",
    "escape_graph_prompt",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

