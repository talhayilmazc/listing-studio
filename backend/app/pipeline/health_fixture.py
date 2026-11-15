"""A synthetic shop shaped like the early-access print-on-demand shops, for Listing health (docs/listing-health.md).

Nothing here is a real seller's. The shape is what those shops look like in general: long keyword titles of
110-140 characters written as comma-separated phrases (the long keyword style most of them used before the
app), all 13 tags, a long reference description, 6-10 mockup photos, often no materials and sometimes no
section, attributes partly filled; sales concentrated in a few listings, most listings selling rarely or never.

Used by the diagnosis (``scripts/health_diagnosis.py``), the tests (``tests/test_listing_health.py``) and the
demo account (``scripts/demo_screens_seed.py``), so the three agree on the same listings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

TAXONOMY = 1623
#: The category's required attribute in this synthetic shop (the profile's ``category_properties``).
REQUIRED = {"id": 1, "name": "Primary color", "required": True}
#: The profile's reference listing: what "Add missing tags" and "Fill attributes" copy from.
REFERENCE_TAGS = ["cotton tee", "soft graphic top", "unisex shirt", "everyday wear", "printed to order", "crew neck tee"]
REFERENCE_ATTRIBUTES = [
    {"property_id": 1, "property_name": "Primary color", "value_ids": [1], "values": ["Black"]},
    {"property_id": 2, "property_name": "Sleeve length", "value_ids": [9], "values": ["Short"]},
]

#: Only the required attribute filled: the reference listing also fills Sleeve length.
PARTIAL = [dict(REFERENCE_ATTRIBUTES[0])]

LONG_DESCRIPTION = (
    "A soft cotton tee with a hand-drawn design across the chest, printed to order and shipped in a recycled mailer. "
    "Unisex fit; see the size chart in the photos. Wash inside out on cold, hang to dry. "
) * 6

_TAGS13 = ["nurse shirt", "rn gift", "nursing school", "er nurse tee", "nurse week", "funny nurse", "icu crew",
           "scrub life", "night shift", "hospital crew", "medical tee", "healthcare top", "nurse grad"]
_TAGS13B = ["teacher shirt", "teacher gift", "back to school", "teacher tee", "school staff", "kinder crew", "class tee",
            "teacher life", "math teacher", "reading club", "first grade", "end of year", "teach love"]
_TAGS13C = ["dog mom shirt", "dog lover tee", "pet mom gift", "paw print top", "rescue dog", "fur mama", "dog walker",
            "puppy love", "dog owner", "cute dog tee", "dog park", "pet lover", "doodle mom"]


@dataclass(frozen=True)
class Item:
    listing_id: int
    payload: dict[str, Any]
    #: Units sold per day-ago (0 = today), within the 13 months the sales read keeps.
    sales: dict[int, int] = field(default_factory=dict)
    #: What the listing is in the fixture for (said in the diagnosis).
    why: str = ""


def _images(n: int) -> list[dict[str, Any]]:
    return [{"listing_image_id": 900 + i, "url_170x135": f"https://i.etsystatic.com/demo/{i}.jpg",
             "url_fullxfull": f"https://i.etsystatic.com/demo/{i}-full.jpg"} for i in range(n)]


def _row(lid: int, title: str, tags: list[str], created_days: int, now: datetime, **more: Any) -> dict[str, Any]:
    made = int((now - timedelta(days=created_days)).timestamp())
    payload = {
        "listing_id": lid, "state": "active", "title": title, "tags": list(tags), "url": f"https://www.etsy.com/listing/{lid}",
        "description": LONG_DESCRIPTION, "materials": [], "images": _images(8), "shop_section_id": 11, "taxonomy_id": TAXONOMY,
        "personalization": None, "num_favorers": 0, "original_creation_timestamp": made, "state_timestamp": made,
        "_properties": [dict(a) for a in REFERENCE_ATTRIBUTES],
    }
    payload.update(more)
    return payload


def _steady(every: int, days: int = 390, units: int = 1, start: int = 0) -> dict[int, int]:
    """One sale every ``every`` days from ``start`` days ago back to ``days``."""
    return {d: units for d in range(start, days, every)}


def shop(now: datetime) -> list[Item]:
    """About forty listings. Deterministic: the same listings and sales every time."""
    out: list[Item] = []
    sellers = [
        "Nurse Shirt, Funny Nurse Gift, RN Shirt, Nursing School Graduation Gift, ER Nurse Tee, Nurse Week Gift for Her",
        "Teacher Shirt, Teacher Gift, Back to School Teacher Tee, Kindergarten Teacher Shirt, Cute Teacher Appreciation Gift",
        "Dog Mom Shirt, Dog Lover Gift, Dog Mama Tee, Cute Dog Shirt for Women, Fur Mom Gift, Pet Lover Shirt, Dog Owner Tee",
        "Retro Mountain Shirt, Hiking Shirt, Camping Tee, Nature Lover Gift, Outdoor Adventure Shirt, Mountain Tee for Men",
        "Coffee Lover Shirt, Funny Coffee Tee, But First Coffee Shirt, Caffeine Addict Gift, Coffee Gift for Her, Mom Shirt",
        "Bookworm Shirt, Book Lover Gift, Reading Shirt, Librarian Tee, Bookish Gift for Her, Book Club Shirt, Read More Tee",
        "Fishing Shirt, Funny Fishing Gift for Men, Fisherman Tee, Bass Fishing Shirt, Dad Fishing Gift, Lake Life Shirt",
        "Cat Mom Shirt, Cat Lover Gift, Funny Cat Tee, Black Cat Shirt, Crazy Cat Lady Gift, Cat Shirt for Women, Kitty Tee",
        "Gardening Shirt, Plant Lady Tee, Plant Mom Gift, Gardener Shirt, Botanical Tee, Garden Lover Gift, Plant Shirt",
        "Mama Shirt, Mom Life Tee, Mother's Day Gift, New Mom Shirt, Cute Mama Tee, Mom Gift for Her, Best Mom Ever Shirt",
        "Halloween Shirt, Spooky Season Tee, Ghost Shirt, Retro Halloween Tee, Fall Shirt, Pumpkin Spice Gift, Witchy Top",
        "Christmas Shirt, Christmas Tree Tee, Holiday Shirt for Women, Merry Christmas Gift, Xmas Tee, Winter Holiday Top",
        "Camping Shirt, Happy Camper Tee, Camp Life Gift, RV Shirt, Campfire Tee, Outdoor Lover Shirt, Camping Gift Men",
        "Pickleball Shirt, Funny Pickleball Gift, Pickleball Player Tee, Dink Shirt, Paddle Sport Tee, Pickleball Lover Gift",
    ]
    tagsets = [_TAGS13, _TAGS13B, _TAGS13C]
    # The shop's sellers: long keyword titles and every tag used, most with no materials, a few in no section,
    # attributes not all filled: what a long-running print-on-demand shop looks like.
    for n, title in enumerate(sellers):
        lid = 5000 + n
        extra: dict[str, Any] = {"num_favorers": 120 - n * 6}
        if n % 4 == 0:
            extra["materials"] = ["cotton"]
        if n % 5 == 2:
            extra["shop_section_id"] = None
        if n % 3 == 1:
            extra["images"] = _images(4)
        if n % 2 == 1:
            extra["_properties"] = PARTIAL
        out.append(Item(lid, _row(lid, title, tagsets[n % 3], 400 - n * 10, now, **extra),
                        _steady(2 + n, start=n % 3), "a best seller in the long keyword style"))
    # Selling, and something is wrong all the same: a brand in the title, stray characters.
    out.append(Item(5100, _row(5100, "DisneyWorld Family Trip Shirt, Matching Vacation Tees, Family Trip Tee", _TAGS13, 300, now,
                               num_favorers=60), _steady(5), "selling, with a brand name in the title (Fix now)"))
    out.append(Item(5101, _row(5101, "Retro Sunset Shirt, Beach Vacation Tee, Summer Shirt", _TAGS13C, 250, now,
                               description="A soft tee for the beach.\n}]", num_favorers=30), _steady(7), "selling, stray characters in the description (Fix now)"))
    # Not selling for 60 days, with something we can fix with one click.
    out.append(Item(5200, _row(5200, "Frog Reading Club Shirt, Book Lover Tee", _TAGS13B[:6], 200, now, num_favorers=14),
                    _steady(20, start=80), "no sale in 60 days, 6 tags (Add missing tags)"))
    out.append(Item(5201, _row(5201, "Vintage Bicycle Tee, Cycling Shirt", _TAGS13[:13], 220, now, _properties=PARTIAL, num_favorers=9),
                    _steady(30, start=95), "no sale in 60 days, attributes missing (Fill attributes)"))
    out.append(Item(5202, _row(5202, "Cute Nurse Shirt, Perfect Nurse Gift for Her, Best Nurse Ever Shirt, Free Shipping", _TAGS13, 180, now,
                               num_favorers=22), {120: 1, 150: 1, 200: 2}, "no sale in 60 days, a title against the search guidance (Rewrite)"))
    out.append(Item(5203, _row(5203, "Christmas Pajama Shirt, Family Christmas Tee", ["christmas 2024", "xmas 2024", *_TAGS13B[:11]], 380, now,
                               num_favorers=40), {330: 2, 340: 3, 350: 2}, "no sale in 60 days, tags dated a past year (Rewrite, season planner)"))
    out.append(Item(5204, _row(5204, "Moon Phase Tee, Celestial Shirt", ["moon phase", "celestial"], 160, now), {},
                    "never sold, 2 tags (Add missing tags)"))
    # Not selling, nothing we can fix with one click: left alone.
    for k in range(6):
        lid = 5300 + k
        out.append(Item(lid, _row(lid, f"Botanical Line Art Tee {k + 1}, Wildflower Shirt", _TAGS13C, 150 + k * 10, now,
                                  num_favorers=k), {}, "no sale, nothing we can fix with one click"))
    # Real problems while not selling.
    out.append(Item(5400, _row(5400, "Lighthouse Tee, Coastal Shirt", _TAGS13, 140, now, images=[]), {}, "no main photo (Fix now)"))
    out.append(Item(5401, _row(5401, "Sailboat Tee, Nautical Shirt", _TAGS13, 140, now, _properties=[
        {"property_id": 2, "property_name": "Sleeve length", "value_ids": [9], "values": ["Short"]}]), {}, "a required attribute missing (Fix now)"))
    out.append(Item(5402, _row(5402, "Anchor Tee, Ocean Shirt", [*_TAGS13[:12], "ocean-tee-for-beach-lovers"], 140, now), {},
                    "a tag over 20 characters, which Etsy rejects (Fix now)"))
    out.append(Item(5403, _row(5403, "Retro Arcade Tee, Gamer Shirt", _TAGS13, 300, now, state="expired", num_favorers=35),
                    _steady(6, start=70), "expired, used to sell (Fix now)"))
    out.append(Item(5404, _row(5404, "Taco Tuesday Tee, Funny Food Shirt", _TAGS13, 300, now, state="inactive", num_favorers=8),
                    _steady(15, start=100), "inactive, used to sell (Fix now)"))
    out.append(Item(5405, _row(5405, "Old Pattern Tee", _TAGS13, 500, now, state="expired"), {}, "expired, never sold: not listed"))
    # Too new to judge.
    for k in range(4):
        lid = 5500 + k
        out.append(Item(lid, _row(lid, f"Autumn Leaves Tee {k + 1}, Fall Shirt", _TAGS13C[:7], 5 + k * 6, now), {},
                        "younger than 30 days (Too new to judge)"))
    return out


def sales_units(item: Item, *, within_days: int | None = None) -> int:
    return sum(u for d, u in item.sales.items() if within_days is None or d < within_days)
