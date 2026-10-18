"""The answers given to Etsy in the Commercial Access request, kept true by the code.

Form: uploads or edits listings, reads sales data, sends no email; nothing is published
without the seller's approval or a schedule they set; sales are read only for a private
profit report; no buyer data is stored; caching follows the API Terms (section 1).
Each test pins one of those statements to the code that makes it true.
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import shop as shop_api
from app.core.config import Settings
from app.db.models import EtsyConnection, ListingProfile
from app.etsy import api as etsy_api
from app.etsy import publisher
from app.workers import retention
from tests.test_publisher import _seed

APP = Path(__file__).resolve().parents[1] / "app"
NOW = datetime.now(timezone.utc)


def _sources() -> dict[str, str]:
    return {p.relative_to(APP).as_posix(): p.read_text(encoding="utf-8") for p in APP.rglob("*.py")}


# --- nothing goes live without the seller --------------------------------------------------


def test_one_place_makes_a_listing_live_and_it_checks_the_sellers_approval_first() -> None:
    live = re.compile(r"""["']state["']\s*:\s*["']active["']""")
    found = {name for name, text in _sources().items() if live.search(text)}
    assert found == {"etsy/publisher.py"}, found
    go_live = inspect.getsource(publisher.publish_live)
    assert len(live.findall(inspect.getsource(publisher))) == len(live.findall(go_live)) == 1
    # Approval and the compliance gate are checked on every run, before the request.
    send = go_live.index("update_listing(")
    assert -1 < go_live.index("if not content.approved") < send
    assert -1 < go_live.index("_has_blocking_finding(") < send
    # A draft is created as a draft: the create call never names a state.
    assert "state" not in inspect.getsource(etsy_api.EtsyApiClient.create_draft_listing).split('"""')[-1]
    # publish_live runs only in the go-live job, which is queued by the seller's Publish
    # (api/publish.py) or by the time they scheduled (workers/schedule.py, which checks
    # again at that moment); the rest only name the job (cost, category, registry, restart).
    callers = {name for name, text in _sources().items() if re.search(r"\bpublish_live\(", text)} - {"etsy/publisher.py"}
    assert callers == {"workers/publish.py"}, callers
    named = {name for name, text in _sources().items() if "run_publish_live_job" in text}
    assert named == {"api/publish.py", "workers/schedule.py", "workers/publish.py", "workers/gate.py",
                     "workers/recovery.py", "workers/settings.py", "etsy/categories.py"}, named
    schedule = _sources()["workers/schedule.py"]
    assert "not content.approved" in schedule and "no longer connected" in schedule


# --- the official API only, through the limiter ---------------------------------------------


def test_every_etsy_request_goes_through_one_method_that_spends_quota_and_waits_for_the_bucket() -> None:
    client = inspect.getsource(etsy_api.EtsyApiClient)
    assert client.count("self._http.request(") == 1 and client.count("self._http.") == 1
    request = inspect.getsource(etsy_api.EtsyApiClient._request)
    assert request.index("guard.check(") < request.index("self._quota.reserve") < request.index("self._bucket.acquire()") < request.index("self._http.request(")
    assert etsy_api.ETSY_API_BASE == "https://api.etsy.com/v3"


def test_the_receipts_endpoints_are_never_called() -> None:
    """getShopReceipts and getShopReceipt return the buyer's name, address and email.
    Sales come from the shop's transactions endpoint, reading four fields of each line."""
    for name, text in _sources().items():
        assert not re.search(r"/receipts\b", text), f"{name} names a receipts endpoint"
        assert not re.search(r"\bget_shop_receipts?\(", text), name  # (docstrings may name it, to say it is not used)
    assert "/application/shops/{shop_id}/transactions" in inspect.getsource(etsy_api.EtsyApiClient.get_shop_transactions)


def test_every_scope_asked_for_is_used_and_none_reads_email_or_addresses() -> None:
    asked = Settings(_env_file=None).etsy_scopes.split()
    assert sorted(asked) == ["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"]
    assert not {"email_r", "address_r", "address_w", "profile_r", "profile_w", "billing_r", "transactions_w", "listings_d"} & set(asked)
    used_by = {
        "listings_r": ("get_listings_by_shop", "get_listing", "get_listing_inventory"),
        "listings_w": ("create_draft_listing", "upload_listing_image", "update_listing_inventory", "update_listing"),
        "shops_r": ("get_shop_sections", "get_shop_shipping_profiles", "get_shop_return_policies"),
        "shops_w": ("create_shop_section", "create_shop_shipping_profile", "create_shop_return_policy"),
        "transactions_r": ("get_shop_transactions", "get_ledger_entries"),
    }
    assert set(used_by) == set(asked)
    callers = "\n".join(text for name, text in _sources().items() if name != "etsy/api.py")
    for scope, methods in used_by.items():
        for method in methods:
            assert hasattr(etsy_api.EtsyApiClient, method), method
        # And the app really calls them (not only defines them).
        assert any(re.search(rf"\.{m}\(", callers) for m in methods), scope


def test_no_email_is_sent() -> None:
    for name, text in _sources().items():
        assert not re.search(r"^\s*(import|from) (smtplib|aiosmtplib|email\.mime|sendgrid|mailgun|postmark)\b", text, re.M), name


# --- caching: other Etsy content is kept a day at most ------------------------------------


async def test_a_shops_section_list_and_listing_counts_are_cleared_after_a_day(async_sm: async_sessionmaker) -> None:
    assert retention.OTHER_CONTENT_MAX_AGE_SECONDS == ListingProfile.CACHE_MAX_AGE_SECONDS == shop_api.COUNTS_FRESH_SECONDS == 24 * 3600
    stale_ids = await _seed(async_sm)
    async with async_sm() as s:  # the seed gives every shop the same Etsy ids: make room for a second one
        (await s.get(EtsyConnection, stale_ids[1])).etsy_user_id = 901
        await s.commit()
    fresh_ids = await _seed(async_sm)
    async with async_sm() as s:
        for ids, at in ((stale_ids, NOW - timedelta(hours=25)), (fresh_ids, NOW - timedelta(hours=2))):
            shop = await s.get(EtsyConnection, ids[1])
            shop.sections, shop.sections_at = [{"id": 1, "title": "Tees"}], at
            shop.listing_counts, shop.listing_counts_at = {"active": 4}, at
        await s.commit()
    async with async_sm() as s:
        await retention.purge_expired_rows(s, now=NOW)
    async with async_sm() as s:
        stale, fresh = await s.get(EtsyConnection, stale_ids[1]), await s.get(EtsyConnection, fresh_ids[1])
        assert (stale.sections, stale.sections_at, stale.listing_counts, stale.listing_counts_at) == (None, None, None, None)
        assert fresh.sections == [{"id": 1, "title": "Tees"}] and fresh.listing_counts == {"active": 4}


def test_listing_counts_older_than_a_day_are_never_shown() -> None:
    class Shop:
        listing_counts = {"active": 4}
        listing_counts_at = NOW - timedelta(hours=25)

    assert shop_api.fresh_counts(Shop()) is None
    Shop.listing_counts_at = NOW - timedelta(hours=1)
    assert shop_api.fresh_counts(Shop()) == {"active": 4}
    assert shop_api.fresh_counts(None) is None
    # Analytics shows them through the same check.
    analytics = (APP / "api" / "analytics.py").read_text(encoding="utf-8")
    assert '"listing_counts": fresh_counts(c)' in analytics and "c.listing_counts" not in analytics
