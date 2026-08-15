"""Token encryption round-trip tests."""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core.crypto import TokenCipher


def test_encrypt_decrypt_roundtrip() -> None:
    cipher = TokenCipher(Fernet.generate_key())
    secret = "etsy-access-token-abc123"
    blob = cipher.encrypt(secret)
    assert isinstance(blob, bytes)
    assert secret.encode() not in blob  # plaintext not present in ciphertext
    assert cipher.decrypt(blob) == secret


def test_decrypt_with_wrong_key_fails() -> None:
    blob = TokenCipher(Fernet.generate_key()).encrypt("secret")
    other = TokenCipher(Fernet.generate_key())
    with pytest.raises(InvalidToken):
        other.decrypt(blob)
