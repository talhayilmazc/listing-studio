"""The legal pages say what the code does, and keep saying it.

The Privacy Policy prints every retention period from ``frontend/lib/legal.ts``
(RETENTION). This test reads that file and fails when a figure there differs
from the setting the code actually uses, when the policy types a period into
its text instead of taking it from there, or when the trademark notice, the
provider disclaimer or the terms version exist in two copies that differ.

The frontend is outside the dev container's mount (only backend/ is there):
run it with the repository mounted, as the other repo-level tests are.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api import schemas
from app.api.item_options import SECTIONS_MAX_AGE
from app.core import app_settings, terms
from app.core.config import Settings
from app.db.models import (
    AiCall, DraftAttempt, DraftDismissal, InviteRequest, ListingProfile, ListingSnapshot, MockupZip, SellerEvent,
    ShopListingCache, UploadBatch,
)
from app.etsy import oauth
from app.pipeline import sales as sales_pipeline
from app.workers import accounts as account_jobs

ROOT = Path(__file__).resolve().parents[2]
LEGAL = ROOT / "frontend" / "lib" / "legal.ts"
PRIVACY = ROOT / "frontend" / "app" / "(marketing)" / "privacy" / "page.tsx"
TERMS = ROOT / "frontend" / "app" / "(marketing)" / "terms" / "page.tsx"

pytestmark = pytest.mark.skipif(not LEGAL.exists(), reason="frontend/ is not here (the dev container mounts only backend/)")

NOTICE = "The term 'Etsy' is a trademark of Etsy, Inc. This Application uses Etsy's API, but is not endorsed or certified by Etsy."
DISCLAIMER = "THIS APPLICATION IS SOLELY PROVIDED BY TALHA YILMAZ AND AHMET DÜZGÜN, OPERATING AS LISTYRO."


def _legal() -> str:
    return LEGAL.read_text(encoding="utf-8")


def _const(name: str) -> str:
    match = re.search(rf'export const {name}\s*=\s*\n?\s*"([^"]+)"', _legal())
    assert match, f"{name} is not a plain string constant in lib/legal.ts"
    return match.group(1)


def _retention() -> dict[str, int]:
    block = re.search(r"export const RETENTION = \{(.*?)\} as const;", _legal(), re.S)
    assert block, "RETENTION is missing from lib/legal.ts"
    return {key: int(value) for key, value in re.findall(r"^\s*(\w+):\s*(\d+),", block.group(1), re.M)}


def _backup_days() -> int:
    text = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    return int(re.search(r'RETENTION_DAYS="\$\{RETENTION_DAYS:-(\d+)\}"', text).group(1))


def _cleanup_minutes() -> int:
    text = (ROOT / "backend" / "app" / "workers" / "settings.py").read_text(encoding="utf-8")
    minutes = sorted(int(m) for m in re.search(r"cron\(purge_expired, minute=\{([^}]*)\}", text).group(1).split(","))
    return minutes[1] - minutes[0]


def test_the_trademark_notice_and_the_disclaimer_are_the_exact_texts_everywhere() -> None:
    assert _const("TRADEMARK_NOTICE") == schemas.TRADEMARK_NOTICE == NOTICE
    assert _const("PROVIDER_DISCLAIMER") == schemas.PROVIDER_DISCLAIMER == DISCLAIMER
    # One copy: no page or component types the notice out for itself (or keeps the old wording).
    # (The documentation's wording, used on 18 October 2026; the API Terms' wording above also
    # meets that criterion: the trademark sentence and the not-endorsed-or-certified statement.)
    old = "uses the Etsy API but is not endorsed"
    for path in (ROOT / "frontend").rglob("*.ts*"):
        if "node_modules" in path.parts or ".next" in path.parts or path == LEGAL or path.name.endswith(".test.ts"):
            continue  # (a test pins the exact text on purpose)
        text = path.read_text(encoding="utf-8")
        assert old not in text, f"{path.relative_to(ROOT)} still carries the old notice"
        assert "The term 'Etsy' is a trademark" not in text, f"{path.relative_to(ROOT)} types the notice instead of importing it"


def test_the_notice_is_in_every_footer_beside_connect_and_in_the_terms() -> None:
    def uses(relative: str, needle: str = "TRADEMARK_NOTICE") -> bool:
        text = (ROOT / "frontend" / relative).read_text(encoding="utf-8")
        return "@/lib/legal" in text and f"{{{needle}}}" in text

    assert uses("components/marketing/Site.tsx")  # the public site's footer, on every marketing page
    assert uses("components/Footer.tsx")  # the app's footer, on every signed-in page
    assert uses("components/AuthShell.tsx")  # sign-in, sign-up, the acceptance screen
    assert uses("app/(app)/connect/page.tsx")  # beside "Connect a shop"
    assert uses("app/(marketing)/terms/page.tsx")
    # Prominent on the home page, not only in its footer (Etsy's Commercial Access review
    # criteria): in the hero, before the first section below it.
    home = (ROOT / "frontend/app/(marketing)/page.tsx").read_text(encoding="utf-8")
    assert uses("app/(marketing)/page.tsx")
    hero = home[home.index('aria-labelledby="hero-title"'): home.index("<Apart />")]
    assert 'data-testid="home-trademark-notice">{TRADEMARK_NOTICE}</p>' in hero
    # Once in each place, never twice (e2e/layout.spec.ts checks the rendered pages too).
    for place in ("components/marketing/Site.tsx", "components/Footer.tsx", "components/AuthShell.tsx",
                  "app/(app)/connect/page.tsx", "app/(marketing)/terms/page.tsx", "app/(marketing)/page.tsx"):
        assert (ROOT / "frontend" / place).read_text(encoding="utf-8").count("{TRADEMARK_NOTICE}") == 1, place
    # The frames that carry those footers are the ones every page is rendered in.
    assert "<SiteFooter />" in (ROOT / "frontend/app/(marketing)/layout.tsx").read_text(encoding="utf-8")
    assert "<Footer />" in (ROOT / "frontend/components/AppShell.tsx").read_text(encoding="utf-8")
    # Who provides the application: in the Terms, at signup and on the acceptance screen.
    for relative in ("app/(marketing)/terms/page.tsx", "app/(app)/register/page.tsx", "app/(app)/accept-terms/page.tsx"):
        assert uses(relative, "PROVIDER_DISCLAIMER"), relative


def test_the_terms_version_is_one_date_in_both_places() -> None:
    assert _const("TERMS_VERSION") == terms.TERMS_VERSION
    year, month, day = terms.TERMS_VERSION.split("-")
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    assert _const("LAST_UPDATED") == f"{int(day)} {months[int(month) - 1]} {year}"
    assert _const("CONSENT_STATEMENT") == terms.STATEMENT == "I agree to the Terms of Service and Privacy Policy"
    assert "updated={LAST_UPDATED}" in TERMS.read_text(encoding="utf-8")
    # The Privacy Policy has its own date: it may tell sellers more without asking them to agree
    # again (the version stays), and it is never earlier than the version.
    assert "updated={PRIVACY_UPDATED}" in PRIVACY.read_text(encoding="utf-8")
    from datetime import datetime as _dt

    privacy_day = _dt.strptime(_const("PRIVACY_UPDATED"), "%d %B %Y").date()
    assert privacy_day >= _dt.strptime(terms.TERMS_VERSION, "%Y-%m-%d").date()


def test_the_retention_figures_in_the_policy_are_the_settings_the_code_uses() -> None:
    settings = Settings(_env_file=None)
    expected = {
        "listingCacheHours": ShopListingCache.STALE_SECONDS // 3600,
        "referenceImagesHours": ListingProfile.DISPLAY_MAX_AGE_SECONDS // 3600,
        "otherEtsyContentHours": ListingProfile.CACHE_MAX_AGE_SECONDS // 3600,
        "uploadsAfterDraftsDays": settings.drafted_retention_days,
        "uploadsOtherwiseDays": settings.unpublished_retention_days,
        "deletedBatchDays": UploadBatch.RESTORE_DAYS,
        "sellerEventDays": SellerEvent.RETENTION_DAYS,
        "analyticsMonths": app_settings.ANALYTICS_MONTHS,
        "listingSnapshotDays": ListingSnapshot.RETENTION_DAYS,
        "draftAttemptDays": DraftAttempt.RETENTION_DAYS,
        "draftDismissalDays": DraftDismissal.RETENTION_DAYS,
        "salesRefreshDays": sales_pipeline.REFRESH_DAYS,
        "inviteRequestDays": InviteRequest.PENDING_RETENTION_DAYS,
        "backupDays": _backup_days(),
        "sessionDays": settings.session_ttl_days,
        "counterMinutes": settings.auth_rate_limit_window_seconds // 60,
        "accountDeletionDays": account_jobs.DEADLINE_DAYS,
        "aiCallMonths": round(AiCall.RETENTION_DAYS / 30.4375),
        "mockupZipHours": MockupZip.RETENTION_HOURS,
        "cleanupMinutes": _cleanup_minutes(),
    }
    assert _retention() == expected
    # The figures the brief names, as they are today.
    assert (expected["listingCacheHours"], expected["otherEtsyContentHours"]) == (6, 24)
    assert (expected["uploadsAfterDraftsDays"], expected["uploadsOtherwiseDays"], expected["analyticsMonths"]) == (3, 30, 24)
    # Two settings share one figure in the policy: they must really be the same.
    assert InviteRequest.DECIDED_RETENTION_DAYS == InviteRequest.PENDING_RETENTION_DAYS
    assert SECTIONS_MAX_AGE.total_seconds() // 3600 == expected["otherEtsyContentHours"]


def test_the_policy_prints_every_period_from_the_constants_and_types_none() -> None:
    text = PRIVACY.read_text(encoding="utf-8")
    assert 'import { PRIVACY_UPDATED, PROVIDER_DISCLAIMER, RETENTION as R } from "@/lib/legal";' in text
    for key in _retention():
        assert f"R.{key}" in text, f"the Privacy Policy does not state {key}"
    # No period typed into the prose: "6 hours", "24 hours", "13 months", "30 days", ...
    prose = re.sub(r"\{[^{}]*\}", " ", text)  # what is left when every {expression} is taken out
    typed = {" ".join(found.split()) for found in re.findall(r"\b\d+\s+(?:hours?|days?|months?|minutes?)\b", prose)}
    # The two that are not retention periods: how fast we answer a request, and the incident deadline.
    assert sorted(typed) == ["24 hours", "30 days"], typed
    flat = " ".join(text.split())
    assert "we reply within 30 days" in flat and "within 24 hours of discovering it" in flat
    # Analytics history shows the figure in force, not only the default.
    assert text.count("<AnalyticsMonths fallback={R.analyticsMonths} />") >= 4
    # What the brief asks the policy to say.
    for said in ("No buyer names, postal addresses, email", "Anthropic", "Cloudflare", "Türkiye",
                 "We do not sell, rent or share your data", "advertising or", "Delete account", "Encrypted tokens", "HTTPS"):
        assert said in text, said


def test_the_terms_say_what_the_brief_asks() -> None:
    text = " ".join(TERMS.read_text(encoding="utf-8").split())
    for said in (
        "{PROVIDER_DISCLAIMER}", "{TRADEMARK_NOTICE}",
        "AI suggests a title, 13 tags and a description from your own images and the reference listing you chose.",
        "Seller Policy", "No duplicate shops", "No search manipulation", "Creativity Standards",
        "Review every AI suggestion before you approve it.",
        "Nothing is ever published without you.",
        "free of charge",
        "at least 30 days&rsquo; written notice before we suspend or withdraw the Service",
        "we stop all use of Etsy data immediately",
        "delete your whole account in <strong>Settings</strong>",
        "Limitation of liability",
    ):
        assert said in text, said
    # Statements the code contradicted, gone for good.
    assert "Each account can connect one shop" not in text


def test_the_scope_reasons_shown_to_sellers_cover_the_scopes_asked_for() -> None:
    shown = set(re.findall(r'\{ scope: "(\w+)", reason:', _legal()))
    assert shown == set(oauth.SCOPE_REASONS) == set(Settings(_env_file=None).etsy_scopes.split())


def test_no_external_script_or_tracker_is_loaded_by_any_page() -> None:
    """Item 11: fonts are self-hosted at build time; there is no analytics, pixel or CDN script."""
    hosts: set[str] = set()
    for path in (ROOT / "frontend").rglob("*"):
        if path.suffix not in (".ts", ".tsx", ".js", ".mjs", ".css") or {"node_modules", ".next"} & set(path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "<script src=" not in text and "next/script" not in text, f"{path.relative_to(ROOT)} loads a script"
        hosts |= set(re.findall(r"https?://([a-zA-Z0-9.-]+)", text))
    known = {
        "listyro.com", "api.listyro.com",  # ours
        "listyro.test",  # ours: the shop-connection browser test (e2e/oauth.spec.ts), mapped to this machine
        "www.etsy.com", "i.etsystatic.com", "etsy.com",  # links to Etsy and the seller's own listing images
        "www.anthropic.com",  # links in the Privacy Policy
        "api", "localhost", "www.w3.org", "nextjs.org", "example.com", "example.test",  # the API inside the network; XML namespaces; docs in comments
    }
    assert hosts <= known, sorted(hosts - known)
