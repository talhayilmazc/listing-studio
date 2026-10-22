"""Demo account for the public site's product screenshots (sample data only).

Run:  docker compose exec -T api python - < backend/scripts/demo_screens_seed.py
Prints the batch id and a session token. Steps and clean-up: docs/marketing-screens.md.
The designs are the site's own SVG drawings (components/marketing/Mockup.tsx); nothing
here is a real seller's work.
"""
"""Sample mockup images (the site's own SVG drawings) as an HTML page to screenshot."""
import json
import os

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
           "shipping_profile_id": 5101, "return_policy_id": 5201, "readiness_state_id": 5301,
           "category_attributes": {"Holiday": ["Christmas", "Mother's Day", "Father's Day"], "Occasion": ["Birthday", "Graduation"], "Primary color": ["Red", "Blue", "Green", "Black", "Pink", "Orange"]},
           "category_names": ["Clothing", "T-shirts"]}


def jpg(name, side):
    img = pyvips.Image.new_from_buffer(svg(name, side).encode(), "")
    return img.flatten(background=[255, 255, 255]).jpegsave_buffer(Q=90)


async def main():
    sm = get_sessionmaker(); storage = get_storage()
    now = datetime.now(timezone.utc); today = now.date()
    async with sm() as s:
        # How many shops and groups (seed-demo --shops N --groups M): the layout runs use
        # 1, 4 and 15 shops with 6 groups; the default is the nine shops in two groups.
        count = max(1, int(os.environ.get("DEMO_SHOPS") or len(SHOPS)))
        group_count = int(os.environ.get("DEMO_GROUPS") or (2 if count > 1 else 0))
        names = [SHOPS[i] if i < len(SHOPS) else f"Demo Shop {i + 1}" for i in range(count)]
        t = Tenant(email="demo-shots@example.test", password_hash="!", status=TenantStatus.active, max_shops=max(12, count),
                   time_zone="America/Chicago", cost_settings={"product_cost": "9.20"})
        s.add(t); await s.flush()
        shops = []
        for i, name in enumerate(names):
            c = EtsyConnection(tenant_id=t.id, status=ConnectionStatus.active, etsy_user_id=999001100 + i, shop_id=999001100 + i,
                               shop_name=name, position=i, sales_synced_at=now - timedelta(hours=3),
                               scopes=["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"])
            s.add(c); shops.append(c)
        await s.flush()
        # Shop groups for the multi-shop screens (by default two: 4 and 5 shops). With more
        # groups the shops are dealt round, so some shops are in two groups.
        if group_count == 2 and count == len(SHOPS):
            layout = [("Group A", shops[:4]), ("Group B", shops[4:])]
        else:
            layout = [(f"Group {chr(65 + g)}", [sh for k, sh in enumerate(shops) if k % group_count == g or (g == 0 and k % 5 == 4)])
                      for g in range(group_count if count > 1 else 0)]
        for pos, (gname, members) in enumerate(layout):
            g = ShopGroup(tenant_id=t.id, name=gname, position=pos)
            s.add(g); await s.flush()
            for c in members:
                s.add(ShopGroupMember(group_id=g.id, connection_id=c.id))
        profs = {}
        for shop, kinds in ((shops[0], ("Standard Tee", "Crewneck Sweatshirt")), *(((shops[1], ("Standard Tee",)),) if len(shops) > 1 else ())):
            for name in kinds:
                p = ListingProfile(tenant_id=t.id, connection_id=shop.id, name=name, reference_listing_id=7001, content_template="apparel",
                                   confirmed=True, updated_at=now, images_updated_at=now, cached_payload=PAYLOAD, fixed_image_ids=[902],
                                   listing_style="search", title_prefix="")
                if name == "Standard Tee":  # How it's made answered; the sweatshirt shows "not set"
                    p.how_made_production, p.how_made_tools = "altered", ["computerized"]
                s.add(p); profs[(shop.shop_name, name)] = p
        plan = await profile_links(s, t, profs[("Northwind Tees", "Standard Tee")], shops, now)
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
            targets = [str(x.id) for x in shops[:5] if x is not shops[1]] if i in (2, 3) and len(shops) >= 5 else None
            c = GeneratedContent(tenant_id=t.id, batch_id=b.id, asset_id=covers[0].id, approved=i < 4, listing_profile_id=prof.id,
                                 target_shops=targets,
                                 title=title, tags=tags, description=opening + "\n\n" + REF.split("\n\n", 1)[1], attributes=attrs,
                                 model_used="claude-sonnet-5", input_tokens=4200, output_tokens=520)
            s.add(c); await s.flush()
            if i == 0:
                s.add(ListingPublication(tenant_id=t.id, content_id=c.id, connection_id=shops[0].id, etsy_listing_id=7100, state="draft",
                                         profile_id=prof.id, title=title, sku=sku, scheduled_for=now + timedelta(days=1, hours=5)))
            if i == 1:
                s.add(ListingPublication(tenant_id=t.id, content_id=c.id, connection_id=shops[0].id, etsy_listing_id=7101, state="active",
                                         profile_id=prof.id, title=title, sku=sku, published_at=now - timedelta(hours=20)))
        # A batch written by hand, nothing typed yet (the layout test presses "Approve all"
        # here: one summary line, the cards marked, "Generate the empty ones").
        hand = UploadBatch(tenant_id=t.id, status=UploadBatchStatus.ready, file_count=3, connection_id=shops[0].id, name="Hand-written")
        s.add(hand); await s.flush()
        for sku, art in (("HW2001", "mountain"), ("HW2002", "heart"), ("HW2003", "cup")):
            key = f"demo-shots/{hand.id}/{sku}-front.jpg"
            storage.put(key, jpg(art, "front"), "image/jpeg")
            a = Asset(batch_id=hand.id, tenant_id=t.id, original_filename=f"{sku}/front.jpg", storage_key=key, processed_key=key,
                      status=AssetStatus.processed, rank=1, group_key=sku, parsed_sku=sku, mime_type="image/jpeg", width=1000, height=1250)
            s.add(a)
            prof = profs[("Northwind Tees", "Standard Tee")]
            s.add(ListingGroupSetting(tenant_id=t.id, batch_id=hand.id, group_key=sku, connection_id=shops[0].id, profile_id=prof.id, manual=True))
            await s.flush()
            s.add(GeneratedContent(tenant_id=t.id, batch_id=hand.id, asset_id=a.id, approved=False, listing_profile_id=prof.id,
                                   title="", tags=[], description=REF.split("\n\n", 1)[1], source="manual"))
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
        for k, shop in enumerate(shops[1:], start=1):  # every shop has figures on Overview
            for back in range(30):
                if random.random() < 0.5:
                    s.add(SalesDaily(connection_id=shop.id, listing_id=8000 + k, day=today - timedelta(days=back), tenant_id=t.id,
                                     units=1, orders=1, revenue_minor=2200 + 100 * k, currency="USD"))
            shop.listing_counts, shop.listing_counts_at = {"active": 20 + 7 * k, "draft": k % 3, "inactive": 1, "expired": 0, "sold_out": 0}, now
        shops[0].listing_counts, shops[0].listing_counts_at = {"active": 12, "draft": 1, "inactive": 0, "expired": 0, "sold_out": 0}, now
        await s.commit()
        await mockup_templates(s, storage, t)
        redis = Redis.from_url(get_settings().redis_url)
        await redis.set(f"link-plan:{plan[0]}", json.dumps(plan[1]), ex=6 * 3600)
        print(b.id, await SessionStore(redis).create(t.id))


#: Synthetic shop names (no real seller's): Group A is the first four, Group B the other five.
SHOPS = ("Northwind Tees", "Juniper Print Co.", "Harbor Thread", "Maple & Ink",
         "Copperline Goods", "Lantern Lane", "Pinecrest Apparel", "Saltmarsh Studio", "Willow Row Tees")

#: The main shop's own settings (what the Standard Tee profile's reference listing uses).
SRC = {"shipping_profile": "Standard US shipping", "return_policy": "returns, exchanges, 30 days",
       "readiness_state": "Made to order, 2-4 days"}


async def profile_links(s, t, prof, shops, now):
    """The Standard Tee profile in the other shops, as if "Use in" had read them:
    Juniper matched everything; Harbor Thread and Maple & Ink have no shipping
    profile of that name (a copy is preselected), the same return terms, and two
    processing profiles with the same days (the seller chooses); Copperline Goods
    and Lantern Lane match nothing (copies); the other Group B shops are not set up."""
    await s.flush()
    shipping = lambda sid, title: {"id": sid, "label": title}
    plan = {}

    def matched(sid):
        return {
            "shipping_profile": {"linked": sid * 10 + 1, "how": "same name", "label": SRC["shipping_profile"], "source_label": SRC["shipping_profile"],
                                 "options": [shipping(sid * 10 + 1, SRC["shipping_profile"]), shipping(sid * 10 + 2, "Free shipping over $35")]},
            "return_policy": {"linked": sid * 10 + 3, "how": "same terms", "label": SRC["return_policy"], "source_label": SRC["return_policy"],
                              "options": [shipping(sid * 10 + 3, SRC["return_policy"]), shipping(sid * 10 + 4, "no returns, no exchanges")]},
            "readiness_state": {"linked": sid * 10 + 5, "how": "same terms", "label": SRC["readiness_state"], "source_label": SRC["readiness_state"],
                                "options": [shipping(sid * 10 + 5, SRC["readiness_state"]), shipping(sid * 10 + 6, "Ready to ship, 1-2 days")]},
        }

    copy_shipping = {"profile": {"title": SRC["shipping_profile"]}, "destinations": [{}], "upgrades": [{}]}
    copy_returns = {"accepts_returns": True, "accepts_exchanges": True, "return_deadline": 30}
    copy_ready = {"readiness_state": "made_to_order", "min_processing_time": 2, "max_processing_time": 4, "processing_time_unit": "days"}
    for k, shop in enumerate(shops[1:6], start=1):
        sid = 600 + k
        entries = matched(sid)
        link = ProfileShopLink(tenant_id=t.id, profile_id=prof.id, connection_id=shop.id, checked_at=now)
        if k == 1:
            link.status, link.shipping_profile_id, link.return_policy_id, link.readiness_state_id = "ready", sid * 10 + 1, sid * 10 + 3, sid * 10 + 5
        elif k in (2, 3):
            entries["readiness_state"] = {"reason": "several in this shop match; choose one", "creatable": False, "requests": 0,
                                          "source_label": SRC["readiness_state"],
                                          "options": [shipping(sid * 10 + 5, "Made to order, 2-4 days (tees)"), shipping(sid * 10 + 7, "Made to order, 2-4 days (hoodies)")]}
            # The shipping profile was copied (every setup copies what a shop lacks); two
            # processing profiles tie, which waits for the seller's pick under "change".
            link.shipping_profile_id, link.return_policy_id = sid * 10 + 1, sid * 10 + 3
            link.status, link.notes = "incomplete", {"readiness_state": "several in this shop match; choose one"}
        else:
            entries = {
                "shipping_profile": {"reason": "no match in this shop", "creatable": True, "requests": 3, "create": copy_shipping,
                                     "source_label": SRC["shipping_profile"], "options": [shipping(sid * 10 + 2, "Free shipping over $35")]},
                "return_policy": {"reason": "no match in this shop", "creatable": True, "requests": 1, "create": copy_returns,
                                  "source_label": SRC["return_policy"], "options": [shipping(sid * 10 + 4, "no returns, no exchanges")]},
                "readiness_state": {"reason": "no match in this shop", "creatable": True, "requests": 1, "create": copy_ready,
                                    "source_label": SRC["readiness_state"], "options": [shipping(sid * 10 + 6, "Ready to ship, 1-2 days")]},
            }
            link.status = "incomplete"
            link.notes = {r: "no match in this shop" for r in entries}
        s.add(link)
        plan[str(shop.id)] = entries
    await s.flush()
    return str(prof.id), plan


async def mockup_templates(s, storage, t):
    """Two mockup templates of drawn blank tees (tests/mockup_samples.py: our own drawings),
    prepared as the worker does: one in three colours (the first confirmed, the others
    waiting for the check) and one photo of two tees side by side (two print areas)."""
    import io
    import uuid
    from PIL import Image
    from app.workers import mockups as work
    from tests import mockup_samples as samples

    now = datetime.now(timezone.utc)

    def two_tees():
        canvas = Image.new("RGB", (1400, 760), (232, 233, 236))
        for i, colour in enumerate(((200, 60, 60), (50, 90, 200))):
            tee = Image.open(io.BytesIO(samples.blank(colour, size=(640, 740), background=(232, 233, 236)))).convert("RGB")
            canvas.paste(tee, (40 + i * 700, 10))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    def six_tees():
        """Three white tees above three black ones (a several-garment photo)."""
        canvas = Image.new("RGB", (1500, 1100), (225, 226, 229))
        for row, colour in enumerate(((238, 238, 236), (32, 32, 35))):
            for col in range(3):
                tee = Image.open(io.BytesIO(samples.blank(colour, size=(480, 540), background=(225, 226, 229)))).convert("RGB")
                canvas.paste(tee, (10 + col * 495, 10 + row * 545))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    groups = [
        ("Comfort Colors 1717", [("White", samples.blank((238, 238, 236), size=(1200, 1400))),
                                 ("Black", samples.blank((30, 30, 32), size=(1200, 1400))),
                                 ("Brick", samples.blank((150, 60, 50), size=(1200, 1400)))]),
        ("Matching tees", [("Red and blue", two_tees())]),
        ("Six tees", [("Three white, three black", six_tees())]),
        # Ten colours, every photo confirmed: a design printed on all ten, to time "Apply this
        # change to… this design on all photos" until the last mockup is made again.
        ("Ten colours", [(name, samples.blank(rgb, size=(900, 1050))) for name, rgb in (
            ("White", (238, 238, 236)), ("Black", (30, 30, 32)), ("Brick", (150, 60, 50)), ("Navy", (35, 45, 80)),
            ("Sage", (150, 170, 140)), ("Sand", (215, 200, 170)), ("Pink", (235, 180, 190)), ("Grey", (130, 130, 135)),
            ("Mustard", (210, 165, 50)), ("Teal", (40, 120, 120)))]),
    ]
    first = None
    for position, (name, photos) in enumerate(groups, start=1):
        template = MockupTemplate(tenant_id=t.id, name=name, position=position)
        s.add(template)
        await s.flush()
        for i, (photo_name, data) in enumerate(photos, start=1):
            pid = uuid.uuid4()
            key = f"{t.id}/templates/{pid}/source.png"
            storage.put(key, data, "image/png")
            s.add(MockupPhoto(id=pid, tenant_id=t.id, template_id=template.id, name=photo_name, position=i, status="preparing",
                              source_key=key, rights_confirmed_at=now))
            await s.commit()
            done = await work.prepare(s, storage, pid)
            if first is None or name == "Ten colours":
                first = first or done
                done.confirmed_at = now
                await s.commit()

asyncio.run(main())
