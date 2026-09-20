"""Password hashing and strength rules (production-spec A2).

argon2id via ``argon2-cffi``. Hashes are opaque strings carrying their own
parameters, so raising the cost later does not invalidate existing hashes —
:func:`needs_rehash` reports when one should be upgraded on next login.

Nothing here logs or returns a password, a hash, or any part of either.
"""

from __future__ import annotations

import secrets
import unicodedata
from functools import lru_cache

MIN_LENGTH = 12
MAX_LENGTH = 1024  # refuse absurd inputs before they reach the hasher

# Passwords that a 12-character minimum would otherwise wave through. Compared
# case-insensitively after normalisation; this is a floor, not a substitute for
# a real breach list.
COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "123456789012",
        "1234567890123",
        "123456789０12",
        "passwordpassword",
        "password1234",
        "password12345",
        "qwertyuiop12",
        "qwertyuiopasdf",
        "administrator",
        "letmeinletmein",
        "iloveyouiloveyou",
        "welcomewelcome",
        "welcome123456",
        "monkeymonkey",
        "abc123abc123",
        "111111111111",
        "000000000000",
        "aaaaaaaaaaaa",
        "changemechangeme",
        "etsyassistant",
        "listyrolistyro",
        "trustno1trustno1",
        "starwarsstarwars",
        "dragondragon",
        "sunshinesunshine",
        "princessprincess",
        "football1234",
        "baseball1234",
        "superman1234",
        "qazwsxedcrfv",
        "zaq12wsxcde3",
        "1q2w3e4r5t6y",
        "passw0rdpassw0rd",
        "p@ssw0rdp@ssw0rd",
    }
)


class WeakPassword(ValueError):
    """Raised when a password fails the strength rules."""


def _normalise(password: str) -> str:
    # NFKC so visually identical passwords hash identically across platforms.
    return unicodedata.normalize("NFKC", password)


def validate_strength(password: str, *, email: str | None = None) -> None:
    """Raise :class:`WeakPassword` if ``password`` is unacceptable."""
    value = _normalise(password)
    if len(value) < MIN_LENGTH:
        raise WeakPassword(f"Password must be at least {MIN_LENGTH} characters")
    if len(value) > MAX_LENGTH:
        raise WeakPassword("Password is too long")
    folded = value.casefold()
    if folded in COMMON_PASSWORDS:
        raise WeakPassword("That password is too common; choose another")
    if len(set(folded)) < 5:
        raise WeakPassword("Password repeats too few distinct characters")
    if email:
        local = email.split("@", 1)[0].casefold()
        if local and len(local) >= 3 and local in folded:
            raise WeakPassword("Password must not contain your email address")


@lru_cache
def _hasher():  # noqa: ANN202 - argon2.PasswordHasher
    from argon2 import PasswordHasher

    # argon2id defaults from argon2-cffi, which track the RFC 9106 guidance.
    return PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2id hash. The plaintext is never retained."""
    return _hasher().hash(_normalise(password))


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time-ish verification; any failure is reported as False."""
    from argon2.exceptions import Argon2Error

    try:
        return _hasher().verify(password_hash, _normalise(password))
    except (Argon2Error, ValueError, TypeError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when ``password_hash`` uses parameters weaker than the current policy."""
    try:
        return _hasher().check_needs_rehash(password_hash)
    except (ValueError, TypeError):
        return True


def generate_temp_password() -> str:
    """A readable one-off password for the admin reset path (A4)."""
    # 4 groups of 5 url-safe chars: long enough to pass the strength rules and
    # short enough to read down a phone line.
    return "-".join(secrets.token_urlsafe(4)[:5] for _ in range(4))
