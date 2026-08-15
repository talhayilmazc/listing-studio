"""Symmetric encryption for secrets at rest (OAuth tokens).

Tokens are encrypted with Fernet (AES-128-CBC + HMAC). The key is read from
``ENCRYPTION_KEY`` in the environment and never hardcoded. Plaintext tokens are
never logged or persisted; only ciphertext (``bytea``) is stored.

Used by the token-storage layer in work-order step 2; included here because it
is small and self-contained.
"""

from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings


class TokenCipher:
    """Encrypt/decrypt short secrets (tokens) with a Fernet key."""

    def __init__(self, key: str | bytes) -> None:
        # Raises if the key is not a valid 32-byte url-safe base64 Fernet key.
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        """Return ciphertext bytes for ``plaintext``."""
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        """Return the plaintext for previously encrypted ``token``."""
        return self._fernet.decrypt(token).decode("utf-8")


@lru_cache
def get_cipher() -> TokenCipher:
    """Return a process-wide cipher built from ``ENCRYPTION_KEY``."""
    return TokenCipher(get_settings().encryption_key)
