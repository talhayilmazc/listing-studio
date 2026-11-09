"""The support address is the product's mailbox, never a person's (fix/onboarding-followups, Part G).

The address shown in every footer, the Terms, the Privacy Policy and the sign-in screens comes from
``SUPPORT_EMAIL`` (default support@listyro.com) through ``/api/meta``. A personal mailbox configured
there (it was a gmail.com address) is replaced by the default, so no page can show one. No user-facing
backend text names a person or a personal mailbox, except the provider line Etsy's API terms ask for
(``PROVIDER_DISCLAIMER``, word for word; the frontend test lib/supportAddress.test.ts does the same
for the pages, and e2e/layout.spec.ts for every rendered page).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.api import schemas
from app.core.config import DEFAULT_SUPPORT_EMAIL, Settings
from tests.test_publish_api import ctx  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
PERSONAL = re.compile(r"gmail\.com|talha", re.IGNORECASE)


def test_the_default_is_the_products_mailbox() -> None:
    assert DEFAULT_SUPPORT_EMAIL == "support@listyro.com"
    assert Settings(_env_file=None).support_email == "support@listyro.com"


def test_a_personal_mailbox_is_never_shown() -> None:
    for personal in ("someone99@gmail.com", "Someone@GMAIL.com ", "x@outlook.com", "x@icloud.com", "x@yahoo.com.tr"):
        assert Settings(_env_file=None, support_email=personal).support_email == "support@listyro.com"
    assert Settings(_env_file=None, support_email="help@listyro.com").support_email == "help@listyro.com"
    assert Settings(_env_file=None, support_email="  ").support_email == "support@listyro.com"


async def test_meta_gives_the_products_mailbox(ctx, monkeypatch) -> None:  # noqa: F811
    from app.api import meta

    monkeypatch.setattr(meta, "get_settings", lambda: Settings(_env_file=None, support_email="someone99@gmail.com"))
    body = (await ctx["client"].get("/api/meta")).json()
    assert body["support_email"] == "support@listyro.com"
    assert "gmail" not in str(body).lower()


def _user_text_files() -> list[Path]:
    """Backend files whose strings reach a seller: API schemas and messages, prompts, legal texts."""
    out = list((ROOT / "backend/app").rglob("*.py")) + list((ROOT / "backend/app").rglob("*.md"))
    # config.py holds the list of personal mail domains the guard refuses: not text anyone reads.
    return [p for p in out if "__pycache__" not in p.parts and p.name != "config.py"]


def test_no_backend_text_names_a_person_or_a_personal_mailbox() -> None:
    found = []
    for path in _user_text_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PERSONAL.search(line) and schemas.PROVIDER_DISCLAIMER not in line:
                found.append(f"{path.relative_to(ROOT)}:{n}")
    assert found == []


def test_the_example_env_files_use_the_products_mailbox() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "SUPPORT_EMAIL=support@listyro.com" in text
    assert not PERSONAL.search(text)
