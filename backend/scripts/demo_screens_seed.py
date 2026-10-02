"""Demo account for the public site's product screenshots (sample data only).

Run:  docker compose exec -T api python - < backend/scripts/demo_screens_seed.py
Prints the batch id and a session token. Steps and clean-up: docs/marketing-screens.md.
The designs are the site's own SVG drawings (components/marketing/Mockup.tsx); nothing
here is a real seller's work.
"""
"""Sample mockup images (the site's own SVG drawings) as an HTML page to screenshot."""
import json

G = {
    "mountain": ("#e9e2d3", "#3f3a33", "#c2542d", "#f3efe7"),
    "heart": ("#2f3a4a", "#f4efe6", "#e8a7a1", "#eef0f3"),
    "paw": ("#c9d3c4", "#2f3b2f", "#7a8f6a", "#f1f4ef"),
    "books": ("#f4efe6", "#33302b", "#4c1d95", "#f5f3f0"),
    "leaf": ("#3d4a3d", "#e9e2d3", "#a9c29a", "#eef1ee"),
    "cup": ("#d9c7b8", "#3b2f29", "#8a4b2f", "#f6f1ec"),
}
ART = {
    "mountain": lambda i, a: f'<circle cx="100" cy="98" r="15" fill="{a}"/><path d="M66 126 L90 94 L104 112 L116 100 L134 126 Z" fill="{i}"/><rect x="70" y="131" width="60" height="3" rx="1.5" fill="{i}"/><rect x="80" y="138" width="40" height="3" rx="1.5" fill="{i}" opacity=".55"/>',
    "heart": lambda i, a: f'<path d="M100 132 C76 114 74 96 88 92 C95 90 100 96 100 100 C100 96 105 90 112 92 C126 96 124 114 100 132 Z" fill="{a}"/><rect x="97" y="101" width="6" height="18" rx="1" fill="{i}"/><rect x="91" y="107" width="18" height="6" rx="1" fill="{i}"/>',
    "paw": lambda i, a: f'<g fill="{i}"><ellipse cx="100" cy="120" rx="15" ry="12"/><circle cx="82" cy="104" r="6"/><circle cx="94" cy="96" r="6"/><circle cx="106" cy="96" r="6"/><circle cx="118" cy="104" r="6"/></g><rect x="78" y="138" width="44" height="3" rx="1.5" fill="{a}"/>',
    "books": lambda i, a: f'<rect x="74" y="122" width="52" height="10" rx="2" fill="{i}"/><rect x="80" y="110" width="46" height="10" rx="2" fill="{a}"/><rect x="72" y="98" width="48" height="10" rx="2" fill="{i}" opacity=".7"/><rect x="84" y="86" width="34" height="10" rx="2" fill="{a}" opacity=".6"/>',
    "leaf": lambda i, a: f'<path d="M100 134 C100 112 84 104 80 90 C98 90 112 100 112 118" fill="{a}"/><path d="M100 134 C100 116 112 108 122 98 C124 114 116 126 104 130" fill="{i}" opacity=".85"/><rect x="99" y="118" width="2.5" height="20" fill="{i}"/>',
    "cup": lambda i, a: f'<path d="M80 104 H118 V122 A14 14 0 0 1 104 136 H94 A14 14 0 0 1 80 122 Z" fill="{i}"/><path d="M118 108 H124 A7 7 0 0 1 124 122 H118" fill="none" stroke="{i}" stroke-width="4"/><path d="M90 96 C87 91 93 89 90 84 M100 96 C97 91 103 89 100 84 M110 96 C107 91 113 89 110 84" fill="none" stroke="{a}" stroke-width="2.5" stroke-linecap="round"/>',
}
SHIRT = 'M70 40 C80 50 120 50 130 40 L166 58 L152 88 L138 80 L138 168 Q138 172 134 172 L66 172 Q62 172 62 168 L62 80 L48 88 L34 58 Z'


def svg(name, side):
    shirt, ink, acc, ground = G[name]
    art = ART[name](ink, acc) if side == "front" else ""
    zoom = 'viewBox="40 60 120 100"' if side == "detail" else 'viewBox="0 0 200 200"'
    if side == "detail":
        art = ART[name](ink, acc)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" {zoom} width="1000" height="1250" preserveAspectRatio="xMidYMid slice">'
            f'<rect x="-50" y="-50" width="300" height="300" fill="{ground}"/><path d="{SHIRT}" fill="{shirt}" stroke="rgba(0,0,0,.08)"/>'
            f'<path d="M70 40 C80 50 120 50 130 40 C122 58 78 58 70 40 Z" fill="rgba(0,0,0,.07)"/>{art}</svg>')



import asyncio, random
from datetime import datetime, timedelta, timezone
import pyvips
from redis.asyncio import Redis
from app.api.deps import get_storage
from app.core.config import get_settings
from app.core.sessions import SessionStore
from app.db.models import *
from app.db.session import get_sessionmaker

random.seed(11)
DESIGNS = [
    ("NW1042", "mountain", "Retro Camping Shirt, Mountain Sunset, Vintage Outdoor Style",
     ["hiking gift", "camp crew tee", "national park trip", "outdoor dad gift", "road trip tee", "nature lover gift", "summer camp shirt", "mountain lover", "adventure tee", "family camping", "trail runner gift", "70s retro tee", "graphic tshirt"]),
    ("NW1043", "heart", "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke",
     ["nurses week", "er nurse gift", "medical humor", "rn graduation", "icu nurse tee", "hospital staff", "nursing student", "coworker gift", "healthcare tee", "nurse life", "scrub top tee", "night shift gift", "funny nurse tee"]),
    ("NW1044", "paw", "Dog Mom Sweatshirt, Paw Print Heart, Rescue Mama",
     ["dog lover gift", "fur mama tee", "rescue dog mom", "puppy mom gift", "pet lover", "mothers day gift", "cozy crewneck", "dog walker", "adopt dont shop", "gift for her", "minimal paw art", "fall sweatshirt", "animal lover"]),
    ("NW1045", "books", "Book Lover Shirt, Stacked Books, Reading Teacher",
     ["librarian gift", "bookish tee", "reading teacher", "book club shirt", "english teacher", "bookworm gift", "back to school", "library tee", "teacher gift", "literary gift", "reader tee", "school staff", "book nerd"]),
    ("NW1046", "leaf", "Plant Lady Shirt, Botanical Leaves, Gardening Humor",
     ["plant mom gift", "garden club", "botanical tee", "gardener gift", "house plant tee", "nature shirt", "spring tee", "plant lover", "mothers day tee", "green thumb", "boho tee", "earth day shirt", "gift for mom"]),
    ("NW1047", "cup", "Coffee First Hoodie, Morning Person Humor, Cozy Cafe",
     ["barista gift", "caffeine tee", "coffee lover", "morning humor", "cozy hoodie", "cafe aesthetic", "latte lover", "gift for coworker", "funny mom gift", "fall hoodie", "espresso tee", "work from home", "teacher coffee"]),
]
OPENING = {
    "NW1042": "A warm retro sunset over the mountains, drawn in a faded 70s style for anyone who would rather be on the trail. It suits campers, hikers and national park road-trippers, and makes an easy gift for an outdoorsy dad.",
    "NW1043": "A gentle flu season joke for the nurses who keep reminding everyone to wash their hands. It is an easy gift for an ER or ICU nurse, a nursing student, or the whole unit during nurses week.",
}
REF = "Thanks for visiting the shop.\n\nSIZING\nUnisex fit. Order your usual size; size up one for a relaxed look.\n\nCARE\nWash inside out in cold water. Tumble dry low.\n\nPRODUCTION\nPrinted to order. Ships in 2-4 business days."
IMAGES = [{"listing_image_id": 900 + i, "rank": i + 1, "url": None, "display_url": None, "kind": "size_chart" if i == 2 else "artwork"} for i in range(3)]
PAYLOAD = {"payload_version": 2, "description": REF, "images": IMAGES, "price": 24.0, "images_classified": True, "taxonomy_id": 482,
           "category_attributes": {"Holiday": ["Christmas", "Mother's Day", "Father's Day"], "Occasion": ["Birthday", "Graduation"], "Primary color": ["Red", "Blue", "Green", "Black", "Pink", "Orange"]},
           "category_names": ["Clothing", "T-shirts"]}


def jpg(name, side):
    img = pyvips.Image.new_from_buffer(svg(name, side).encode(), "")
    return img.flatten(background=[255, 255, 255]).jpegsave_buffer(Q=90)


async def main():
    sm = get_sessionmaker(); storage = get_storage()
    now = datetime.now(timezone.utc); today = now.date()
    async with sm() as s:
        t = Tenant(email="demo-shots@example.test", password_hash="!", status=TenantStatus.active, daily_quota=1000,
                   time_zone="America/Chicago", cost_settings={"product_cost": "9.20"})
        s.add(t); await s.flush()
        shops = []
        for i, name in enumerate(("Northwind Tees", "Juniper Print Co.")):
            c = EtsyConnection(tenant_id=t.id, status=ConnectionStatus.active, etsy_user_id=999001100 + i, shop_id=999001100 + i,
                               shop_name=name, position=i, sales_synced_at=now - timedelta(hours=3),
                               scopes=["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"])
            s.add(c); shops.append(c)
        await s.flush()
        profs = {}
        for shop, names in ((shops[0], ("Standard Tee", "Crewneck Sweatshirt")), (shops[1], ("Standard Tee",))):
            for name in names:
                p = ListingProfile(tenant_id=t.id, connection_id=shop.id, name=name, reference_listing_id=7001, content_template="apparel",
                                   confirmed=True, updated_at=now, images_updated_at=now, cached_payload=PAYLOAD, fixed_image_ids=[902],
                                   listing_style="search", title_prefix="")
                s.add(p); profs[(shop.shop_name, name)] = p
        b = UploadBatch(tenant_id=t.id, status=UploadBatchStatus.ready, file_count=18, connection_id=shops[0].id, name="Summer drop")
        s.add(b); await s.flush()
        for i, (sku, art, title, tags) in enumerate(DESIGNS):
            prof = profs[("Northwind Tees", "Crewneck Sweatshirt" if art == "paw" else "Standard Tee")]
            covers = []
            for j, side in enumerate(("front", "back", "detail")):
                key = f"demo-shots/{b.id}/{sku}-{side}.jpg"
                storage.put(key, jpg(art, side), "image/jpeg")
                a = Asset(batch_id=b.id, tenant_id=t.id, original_filename=f"{sku}/{side}.jpg", storage_key=key, processed_key=key,
                          status=AssetStatus.processed, rank=j + 1, group_key=sku, parsed_sku=sku, mime_type="image/jpeg", width=1000, height=1250)
                s.add(a); covers.append(a)
            s.add(ListingGroupSetting(tenant_id=t.id, batch_id=b.id, group_key=sku, connection_id=shops[0].id, profile_id=prof.id, manual=i == 0))
            await s.flush()
            opening = OPENING.get(sku, "A " + title.split(",")[0].lower() + " design with a clean, simple look. It makes an easy gift and works for everyday wear.")
            attrs = {"vision": {"theme": art}, "search": {"opening": opening}, "listing": {"Primary color": ["Orange", "Blue", "Green", "Black", "Green", "Black"][i]}}
            if sku == "NW1044":
                attrs["listing"]["Holiday"] = "Mother's Day"
            c = GeneratedContent(tenant_id=t.id, batch_id=b.id, asset_id=covers[0].id, approved=i < 3, listing_profile_id=prof.id,
                                 title=title, tags=tags, description=opening + "\n\n" + REF.split("\n\n", 1)[1], attributes=attrs,
                                 model_used="claude-sonnet-5", input_tokens=4200, output_tokens=520)
            s.add(c); await s.flush()
            if i == 0:
                s.add(ListingPublication(tenant_id=t.id, content_id=c.id, connection_id=shops[0].id, etsy_listing_id=7100, state="draft",
                                         profile_id=prof.id, title=title, sku=sku, scheduled_for=now + timedelta(days=1, hours=5)))
            if i == 1:
                s.add(ListingPublication(tenant_id=t.id, content_id=c.id, connection_id=shops[0].id, etsy_listing_id=7101, state="active",
                                         profile_id=prof.id, title=title, sku=sku, published_at=now - timedelta(hours=20)))
        for n in range(12):
            lid = 7000 + n; d = DESIGNS[n % 6]
            s.add(ShopListingCache(tenant_id=t.id, connection_id=shops[0].id, listing_id=lid, fetched_at=now,
                                   payload={"listing_id": lid, "state": "active", "title": d[2], "url": "https://www.etsy.com/listing/%d" % lid,
                                            "skus": ["%s-%d" % (d[0], n)], "price": {"amount": 2400, "divisor": 100, "currency_code": "USD"},
                                            "original_creation_timestamp": int((now - timedelta(days=30 + n * 25)).timestamp()),
                                            "state_timestamp": int((now - timedelta(days=30 + n * 25)).timestamp())}))
            rate = [0.55, 0.4, 0.33, 0.28, 0.22, 0.18, 0.15, 0.12, 0.1, 0.08, 0.06, 0.05][n]
            for back in range(390):
                season = 1.6 if (today - timedelta(days=back)).month in (11, 12) else 1.0
                if random.random() < rate * season:
                    units = 1 + (random.random() < 0.25)
                    s.add(SalesDaily(connection_id=shops[0].id, listing_id=lid, day=today - timedelta(days=back), tenant_id=t.id,
                                     units=units, orders=1, revenue_minor=2400 * units, currency="USD"))
        s.add(SalesSync(connection_id=shops[0].id, tenant_id=t.id, state="complete", window_start=today - timedelta(days=396),
                        total_count=1800, window_count=1700, pages_estimate=18, read_count=1700, requests_used=24, last_update_requests=1,
                        started_at=now - timedelta(days=3), finished_at=now - timedelta(days=3)))
        end = int(now.timestamp()); mid = end - end % 86400
        for back in range(395):
            for kind, amount in (("transaction", -290), ("payment_processing_fee", -180), ("listing", -60), ("prolist", -240)):
                s.add(LedgerDaily(connection_id=shops[0].id, day=today - timedelta(days=back), ledger_type=kind, tenant_id=t.id,
                                  amount_minor=amount, entries=3, currency="USD"))
        s.add(LedgerSync(connection_id=shops[0].id, tenant_id=t.id, state="complete", window_start=mid - 90 * 86400, window_end=end,
                         total_count=900, next_offset=900, read_count=900, synced_until=end, requests_used=10, backfill_state="complete",
                         backfill_target=mid - 395 * 86400, covered_from=mid - 395 * 86400, backfill_requests=40))
        await s.commit()
        print(b.id, await SessionStore(Redis.from_url(get_settings().redis_url)).create(t.id))

asyncio.run(main())
