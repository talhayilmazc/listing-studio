"""Click-through acceptance of the Terms of Service and the Privacy Policy.

An account can use the app only after it has agreed to the current version, by
an explicit tick (signup) or an explicit button (the one-time screen an existing
account sees at its next sign-in). What it agreed to and when is kept on the
account (``tenant.terms_version``, ``tenant.terms_accepted_at``).

**Changing the Terms or the Privacy Policy in a way sellers must agree to means
bumping** :data:`TERMS_VERSION`: every account is then asked again before it can
go on. The version is the date of the text, the same date the two pages show as
"Last updated" (``frontend/lib/legal.ts``; ``tests/test_legal_texts.py`` fails if
they differ).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: The version of the Terms of Service and Privacy Policy now in force.
TERMS_VERSION = "2026-10-13"

#: What an account that has not agreed is told (403 on every seller endpoint).
REQUIRED = "terms acceptance required"

#: The sentence beside the tick and on the one-time screen.
STATEMENT = "I agree to the Terms of Service and Privacy Policy"


def accepted(tenant: Any) -> bool:
    """Has this account agreed to the version now in force?"""
    return getattr(tenant, "terms_version", None) == TERMS_VERSION and getattr(tenant, "terms_accepted_at", None) is not None


def accept(tenant: Any, *, now: datetime | None = None) -> None:
    tenant.terms_version = TERMS_VERSION
    tenant.terms_accepted_at = now or datetime.now(timezone.utc)
