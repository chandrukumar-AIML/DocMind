"""
Reversible encryption for secrets that must be read back in plaintext later
(e.g. per-workspace BYOK LLM provider API keys sent to Groq/OpenAI/etc on every call).

Not to be confused with app.core.apikey_manager, which stores a one-way SHA-256 hash of
app-generated API keys — that pattern only works because the app never needs the
plaintext back. BYOK keys are the opposite: we must decrypt and forward them.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    settings = get_settings()
    key = getattr(settings, "encryption_key", None)
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
            "and set it in .env before storing or reading encrypted secrets."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"ENCRYPTION_KEY is not a valid Fernet key: {e}") from e


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret for storage. Returns a urlsafe-base64 token string."""
    if not plaintext:
        raise ValueError("Cannot encrypt an empty secret")
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a token produced by encrypt_secret(). Raises ValueError if invalid/tampered."""
    if not ciphertext:
        raise ValueError("Cannot decrypt an empty ciphertext")
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Ciphertext is invalid or was encrypted with a different key") from e


def clear_crypto_cache() -> None:
    """Clear cached Fernet instance — useful for tests that swap ENCRYPTION_KEY."""
    _get_fernet.cache_clear()


__all__ = ["encrypt_secret", "decrypt_secret", "clear_crypto_cache"]
