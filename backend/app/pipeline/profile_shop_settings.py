"""A profile's settings per shop (B7: the Shops table on a profile card).

Shipping profile, return policy and processing profile per shop are the account's map
(pipeline/setting_map.py; one choice serves every profile using the same main-shop setting).
Two more are the profile's own, per shop, kept in ``listing_profile.shop_settings``:

* ``section_title``: the section its listings default to in that shop (by the shop's own
  title; a seller's choice on a listing's card, or the title chosen for it in another shop,
  comes first; a title the shop does not have is not used, and no section is ever made);
* ``title_prefix``: the prefix its titles carry in that shop. The main shop's prefix is the
  profile's own (``listing_profile.title_prefix``); another shop not set follows it.

The shop's daily limit (``etsy_connection.daily_limit``) is the shop's, for every profile.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.db.models import ListingProfile

FIELDS = ("section_title", "title_prefix")
#: Etsy's longest section title.
MAX_SECTION_TITLE = 24
MAX_PREFIX = 60


def _settings(profile: ListingProfile, connection_id: Any) -> dict[str, Any]:
    return dict(((profile.shop_settings or {}).get(str(connection_id))) or {})


def shop_prefix(profile: ListingProfile, connection_id: uuid.UUID | str) -> str | None:
    """The title prefix the profile's listings carry in this shop."""
    if str(connection_id) == str(profile.connection_id):
        return profile.title_prefix
    own = _settings(profile, connection_id)
    return own["title_prefix"] if "title_prefix" in own else profile.title_prefix


def shop_section(profile: ListingProfile | None, connection_id: uuid.UUID | str) -> str | None:
    """The section title the profile's listings default to in this shop, or None."""
    if profile is None:
        return None
    value = _settings(profile, connection_id).get("section_title")
    return str(value) if value else None


def clean(field: str, value: Any) -> str | None:
    """A value as stored; ValueError (safe to show) when it cannot be."""
    if field not in FIELDS:
        raise ValueError(f"unknown setting {field}")
    if value is None:
        return None
    text = " ".join(str(value).split())
    if field == "section_title":
        if len(text) > MAX_SECTION_TITLE:
            raise ValueError(f"a section title is at most {MAX_SECTION_TITLE} characters on Etsy")
        return text or None
    if len(text) > MAX_PREFIX:
        raise ValueError(f"a title prefix is at most {MAX_PREFIX} characters")
    return text


def set_value(profile: ListingProfile, connection_id: uuid.UUID | str, field: str, value: Any, *, follow: bool = False) -> None:
    """Set one field for one shop; ``follow`` drops it (the shop follows the profile again).
    The main shop's prefix is the profile's own prefix."""
    if field == "title_prefix" and str(connection_id) == str(profile.connection_id):
        profile.title_prefix = None if follow else clean(field, value)
        return
    all_settings = {k: dict(v) for k, v in (profile.shop_settings or {}).items()}
    mine = all_settings.setdefault(str(connection_id), {})
    if follow:
        mine.pop(field, None)
    else:
        mine[field] = clean(field, value)
    all_settings = {k: v for k, v in all_settings.items() if v}
    profile.shop_settings = all_settings or None
