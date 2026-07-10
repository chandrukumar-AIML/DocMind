"""
Shared prompt utilities for DocuMind AI.

Centralizes token estimation, prompt escaping, and context window management
to prevent duplication across CRAG, agent, and domain modules.

Usage:
    from app.core.prompts import escape_prompt_content, estimate_tokens_approx
"""

from __future__ import annotations

import re
from typing import Final

# DVMELTSS-S: Immutable prompt utilities — safe defaults
_CHARS_PER_TOKEN_ESTIMATE: Final = 4  # Conservative estimate for English
_PROMPT_ESCAPE_REGEX: Final = re.compile(r"\{(?!\w+:\w+\})")

# BATMAN-A: Context window safety margins
DEFAULT_MAX_PROMPT_TOKENS: Final = 7000
SAFETY_MARGIN_TOKENS: Final = 500  # Buffer for unexpected tokens


def estimate_tokens_approx(text: str, chars_per_token: int = _CHARS_PER_TOKEN_ESTIMATE) -> int:
    """
    Rough token estimation for context window safety checks.

    Note: For production accuracy, use tiktoken library.
    This is a fast approximation for pre-checks.

    Args:
        text: Text to estimate
        chars_per_token: Override default chars-per-token ratio

    Returns:
        Approximate token count
    """
    if not text:
        return 0
    return len(text) // chars_per_token


def escape_prompt_content(text: str) -> str:
    """
    OWASP-1: Escape curly braces in user content to prevent prompt injection.

    Escapes ALL curly braces — user content should never contain template syntax.
    Safe for use in f-string and LangChain prompt templates.

    Args:
        text: Raw user content

    Returns:
        Escaped text safe for f-string/LangChain prompt templates
    """
    if not text or not isinstance(text, str):
        return ""

    # Previous regex preserved {word:word} patterns which could still cause
    # format errors when } was later double-replaced. User content should
    # never contain template syntax — escape everything.
    return text.replace("{", "{{").replace("}", "}}")


def truncate_for_prompt(text: str, max_tokens: int, chars_per_token: int = _CHARS_PER_TOKEN_ESTIMATE) -> str:
    """
    Truncate text to fit within token budget.

    Args:
        text: Text to truncate
        max_tokens: Maximum allowed tokens
        chars_per_token: Override default chars-per-token ratio

    Returns:
        Truncated text that should fit within max_tokens
    """
    if not text:
        return ""

    max_chars = max_tokens * chars_per_token
    if len(text) <= max_chars:
        return text

    # Truncate and add ellipsis
    truncated = text[: max_chars - 3] + "..."
    return truncated


def build_safe_prompt(template: str, **variables: str) -> str:
    """
    Build prompt with escaped variables to prevent injection.

    Args:
        template: LangChain-style template with {var} placeholders
        **variables: Variables to inject (will be escaped)

    Returns:
        Safe prompt string ready for LLM invocation
    """
    # Escape all variable values before formatting
    safe_vars = {k: escape_prompt_content(v) for k, v in variables.items()}
    return template.format(**safe_vars)


def build_grading_prompt(query: str, documents: list[str], template: str) -> str:
    """
    Build document grading prompt with token-aware truncation.

    Args:
        query: User query to grade against
        documents: List of document snippets to include
        template: Prompt template with {query} and {documents} placeholders

    Returns:
        Safe, token-bounded prompt string
    """
    # Estimate base template tokens
    base_tokens = estimate_tokens_approx(template)
    query_tokens = estimate_tokens_approx(query)

    # Calculate remaining budget for documents
    remaining_tokens = DEFAULT_MAX_PROMPT_TOKENS - SAFETY_MARGIN_TOKENS - base_tokens - query_tokens
    remaining_chars = remaining_tokens * _CHARS_PER_TOKEN_ESTIMATE

    # Truncate documents to fit budget
    doc_parts = []
    used_chars = 0
    for doc in documents:
        if used_chars + len(doc) > remaining_chars:
            break
        doc_parts.append(doc)
        used_chars += len(doc)

    documents_text = "\n\n".join(doc_parts)

    return build_safe_prompt(template, query=query, documents=documents_text)


# DVMELTSS-M: Explicit module exports
__all__ = [
    "estimate_tokens_approx",
    "escape_prompt_content",
    "truncate_for_prompt",
    "build_safe_prompt",
    "build_grading_prompt",
    "DEFAULT_MAX_PROMPT_TOKENS",
    "SAFETY_MARGIN_TOKENS",
]
# Local smoke test entry point. Run: python -m

