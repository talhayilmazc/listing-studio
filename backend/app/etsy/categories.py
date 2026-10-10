"""What an Etsy request was for, in the terms an admin thinks in.

Every request is made inside a job, and the job's name says what it is for
(``etsy.calllog.current_job``). Requests are counted per account, per day and
per category, so "929 of 1,000 used" can be told apart: a one-off first read of
a shop's sales is not a seller who needs a higher ceiling.
"""

from __future__ import annotations

#: job function -> category
_BY_JOB: dict[str, str] = {
    "sync_shop_listings": "shop_sync",
    "sync_shop_counts": "shop_sync",
    "sync_shop_sections": "shop_sync",
    "create_shop_section": "sections",
    "update_sku_on_etsy": "sku_updates",
    "refresh_profile": "profiles",
    "refresh_profile_images": "profiles",
    "detect_profiles": "profiles",
    "refresh_shop_links": "profiles",
    "link_profile": "profile_setup",
    "create_link_resources": "profile_setup",
    "sync_sales": "sales",
    "estimate_sales": "sales",
    "sync_ledger": "ledger",
    "estimate_ledger": "ledger",
    "backfill_ledger": "ledger",
    "read_listing_stats": "listing_stats",
    "run_publish_job": "drafts",
    "run_publish_live_job": "publishing",
    "run_replace_images_job": "replace_images",
}

#: category -> what the admin reads, in the order shown
LABELS: dict[str, str] = {
    "drafts": "Creating drafts",
    "publishing": "Publishing",
    "replace_images": "Replacing images",
    "shop_sync": "Shop sync",
    "profiles": "Profile refresh",
    "profile_setup": "Setting profiles up in shops",
    "sales": "Sales read",
    "ledger": "Fee ledger read",
    "listing_stats": "Listing views and favourites",
    "sections": "Creating shop sections",
    "sku_updates": "Updating SKUs on drafts",
    "other": "Other",
}


def category_of(job: str | None) -> str:
    """The category of the job a request is being made in (``"function:argument"``)."""
    if not job:
        return "other"
    return _BY_JOB.get(job.split(":", 1)[0], "other")
