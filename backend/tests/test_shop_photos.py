"""A listing's photos in one shop (customer request, 2026-11-03; api/shop_photos.py, pipeline/shop_listing.py::arrange).

The draft for shop B sends B's order and cover while shop A keeps its own; photos left out; Etsy's limit; Keep as it
is; the bulk cover actions and their preview; a listing already live keeps its photos; another account gets 404.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
from httpx import AsyncClient
from sqlalchemy import select

from app.api import shop_photos
from app.core.config import Settings
from app.db.models import Asset, AssetStatus, GeneratedContent, ListingPublication
from app.pipeline import shop_listing
from app.pipeline.storage import LocalStorage
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session
from tests.test_end_to_end import FakeEtsy, _Httpx, _run_drafts, _send_drafts, _world
from tests.test_publish_volume import _png

#: A category with a multi-value Holiday, a single Occasion, a number with units and a plain choice.
TAXONOMY_PROPS = [
    {"property_id": 46803063641, "property_name": "Holiday", "is_multivalued": True, "max_values_allowed": 2, "scales": [],
     "possible_values": [{"value_id": 1, "name": "Christmas"}, {"value_id": 2, "name": "Easter"}, {"value_id": 3, "name": "Halloween"}]},
    {"property_id": 46803063659, "property_name": "Occasion", "is_multivalued": False, "scales": [],
     "possible_values": [{"value_id": 11, "name": "Birthday"}, {"value_id": 12, "name": "Wedding"}]},
    {"property_id": 300, "property_name": "Width", "possible_values": [],
     "scales": [{"scale_id": 5, "display_name": "Inches"}, {"scale_id": 6, "display_name": "Centimeters"}]},
    {"property_id": 301, "property_name": "Sleeve length", "possible_values": [{"value_id": 21, "name": "Short sleeve"}, {"value_id": 22, "name": "Long sleeve"}]},
    {"property_id": 302, "property_name": "Care", "possible_values": [], "scales": []},  # free text: not offered
]


class ShopEtsy(FakeEtsy):
    """The end-to-end fake, which also knows the category's attributes, keeps what each draft was given, and names
    each uploaded photo."""

    def __init__(self) -> None:
        super().__init__()
        self.photos: dict[int, list[str]] = {}
        self.properties: dict[int, dict[int, dict]] = {}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path.removeprefix("/v3/application")
        if re.fullmatch(r"/seller-taxonomy/nodes/\d+/properties", path):
            self.requests.append(f"{method} /seller-taxonomy/nodes/{{id}}/properties")
            return httpx.Response(200, json={"results": TAXONOMY_PROPS})
        if m := re.fullmatch(r"/shops/\d+/listings/(\d+)/properties/(\d+)", path):
            self.requests.append(f"{method} /shops/{{id}}/listings/{{id}}/properties/{{id}}")
            form = parse_qs(request.content.decode())
            # The client sends a list as one comma-joined field, as Etsy's form API takes it.
            split = lambda key: [x for v in form.get(key, []) for x in v.split(",") if x]  # noqa: E731
            kept = {"property_id": int(m.group(2)), "value_ids": [int(v) for v in split("value_ids")],
                    "values": split("values"), "scale_id": int(form["scale_id"][0]) if "scale_id" in form else None}
            self.properties.setdefault(int(m.group(1)), {})[int(m.group(2))] = kept
            return httpx.Response(200, json=kept)
        if m := re.fullmatch(r"/shops/\d+/listings/(\d+)/properties", path):
            return httpx.Response(200, json={"results": list(self.properties.get(int(m.group(1)), {}).values())})
        if (m := re.fullmatch(r"/shops/\d+/listings/(\d+)/images", path)) and method == "POST":
            name = re.search(rb'filename="([^"]+)"', request.content)
            self.photos.setdefault(int(m.group(1)), []).append(name.group(1).decode() if name else "?")
        return await super().handle(request)


async def world(tmp_path, test_settings: Settings, monkeypatch, *, photos: int = 3, listings: int = 1, payload: dict | None = None) -> dict:
    """Two shops, ``listings`` listings with ``photos`` photos each, and the category's attributes in the profile."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=listings)
    etsy = ShopEtsy()
    from app.workers import publish as publish_worker

    monkeypatch.setattr(publish_worker, "httpx", _Httpx(etsy))
    w["etsy"] = etsy
    storage = LocalStorage(tmp_path / "storage")
    async with w["sm"]() as s:
        from app.db.models import ListingProfile
        from app.pipeline import listing_attributes

        profile = await s.get(ListingProfile, w["profile"])
        profile.cached_payload = {**profile.cached_payload, "category_properties": listing_attributes.compact(TAXONOMY_PROPS), **(payload or {})}
        w["photos"] = {}
        for content in (await s.execute(select(GeneratedContent))).scalars():
            cover = await s.get(Asset, content.asset_id)
            ids = [str(cover.id)]
            for n in range(2, photos + 1):
                key = f"p/{cover.group_key}-{n}.png"
                storage.put(key, _png(n * 37))
                extra = Asset(batch_id=cover.batch_id, tenant_id=cover.tenant_id, original_filename=f"{cover.group_key}/photo{n}.png",
                              storage_key=key, processed_key=key, status=AssetStatus.processed, rank=n, group_key=cover.group_key)
                s.add(extra)
                await s.flush()
                ids.append(str(extra.id))
            w["photos"][content.id] = ids
        await s.commit()
    return w


async def both_shops(w: dict) -> None:
    res = await w["client"].put(f"/api/batches/{w['batch']}/send-to", json={"send_to": {"connection_ids": [str(s) for s in w["shops"]]}})
    assert res.status_code == 200, res.text


async def drafts_by_shop(w: dict) -> dict[uuid.UUID, int]:
    async with w["sm"]() as s:
        return {p.connection_id: p.etsy_listing_id for p in (await s.execute(select(ListingPublication))).scalars()}


async def other_client(w: dict) -> AsyncClient:
    tid = await make_tenant(w["sm"], "other@example.com")
    client = AsyncClient(transport=w["client"]._transport, base_url="http://test", headers=BROWSER_HEADERS)
    authenticate(client, await open_session(w["ctx"]["redis"], tid))
    return client


def _url(w: dict, shop: uuid.UUID, content: uuid.UUID | None = None) -> str:
    return f"/api/batches/{w['batch']}/shops/{shop}/photos" + (f"/{content}" if content else "")


def test_arrange_is_the_shops_order_then_new_photos_never_one_left_out() -> None:
    class P:
        def __init__(self, i: str) -> None:
            self.id = i

    a, b, c, d = P("a"), P("b"), P("c"), P("d")
    assert shop_listing.arrange([a, b, c], None) == [a, b, c]
    assert shop_listing.arrange([a, b, c], (["c", "a"], ["b"])) == [c, a]
    assert shop_listing.arrange([a, b, c, d], (["c", "a"], ["b"])) == [c, a, d]  # d was added since: at the end
    assert shop_listing.arrange([a, b], (["gone"], ["a", "b"])) == [a, b]  # never none


async def test_shop_b_drafts_with_its_own_order_and_cover_while_a_keeps_its_own(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    one, two = w["shops"]
    content = w["contents"][0]
    cover, p2, p3 = w["photos"][content]
    got = (await w["client"].get(_url(w, two, content))).json()
    assert got["own"] is False and got["order"] == [cover, p2, p3] and got["room"] == 20
    res = await w["client"].put(_url(w, two, content), json={"order": [p3, cover, p2]})
    assert res.status_code == 200 and res.json()["own"] is True and res.json()["order"][0] == p3
    rows = (await w["client"].get(f"/api/batches/{w['batch']}/shops/{two}/rows")).json()["rows"]
    assert rows[0]["photos_own"] is True
    shops = (await w["client"].get(f"/api/batches/{w['batch']}/shops")).json()["listings"][0]["photos"]
    assert shops == {str(two): [p3, cover, p2]}  # the cards show B's order when B is picked; A has none of its own
    await both_shops(w)
    await _run_drafts(w, await _send_drafts(w))
    drafts = await drafts_by_shop(w)
    assert w["etsy"].photos[drafts[one]] == [f"{cover}-thumb.jpg", f"{p2}.jpg", f"{p3}.jpg"]
    assert w["etsy"].photos[drafts[two]] == [f"{p3}-thumb.jpg", f"{cover}.jpg", f"{p2}.jpg"]
    await w["engine"].dispose()


async def test_photos_left_out_in_one_shop_are_not_sent_there(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    one, two = w["shops"]
    content = w["contents"][0]
    cover, p2, p3 = w["photos"][content]
    res = await w["client"].put(_url(w, two, content), json={"order": [p2], "out": [cover, p3]})
    assert res.json()["out"] == [cover, p3]
    await both_shops(w)
    await _run_drafts(w, await _send_drafts(w))
    drafts = await drafts_by_shop(w)
    assert w["etsy"].photos[drafts[two]] == [f"{p2}-thumb.jpg"]
    assert len(w["etsy"].photos[drafts[one]]) == 3
    await w["engine"].dispose()


async def test_etsys_limit_at_least_one_and_at_most_what_fits(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    two, content = w["shops"][1], w["contents"][0]
    cover, p2, p3 = w["photos"][content]
    none = await w["client"].put(_url(w, two, content), json={"order": [], "out": [cover, p2, p3]})
    assert none.status_code == 422 and "at least one photo" in none.text
    twice = await w["client"].put(_url(w, two, content), json={"order": [cover, cover]})
    assert twice.status_code == 422
    monkeypatch.setattr(shop_photos.chart_order, "MAX_IMAGES", 2)
    over = await w["client"].put(_url(w, two, content), json={"order": [p3, p2, cover]})
    assert over.status_code == 422 and "leave 1 more out" in over.text
    assert (await w["client"].put(_url(w, two, content), json={"order": [p3, p2], "out": [cover]})).status_code == 200
    stranger = await w["client"].put(_url(w, two, content), json={"order": [str(uuid.uuid4())]})
    assert stranger.status_code == 404
    await w["engine"].dispose()


async def test_keep_as_it_is_and_the_listings_own_order_follow_the_listing(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    two, content = w["shops"][1], w["contents"][0]
    cover, p2, p3 = w["photos"][content]
    await w["client"].put(_url(w, two, content), json={"order": [p3, cover, p2]})
    kept = (await w["client"].put(_url(w, two, content), json={"keep": True})).json()
    assert kept["own"] is False and kept["order"] == [cover, p2, p3]
    same = (await w["client"].put(_url(w, two, content), json={"order": [cover, p2, p3]})).json()
    assert same["own"] is False  # the listing's own order is no override
    async with w["sm"]() as s:
        row = await s.get(GeneratedContent, content)
        assert shop_listing.photos_in(row, two) is None
    await w["engine"].dispose()


async def test_bulk_cover_is_previewed_then_applied_for_that_shop_only(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch, listings=2)
    one, two = w["shops"]
    ids = [str(c) for c in w["contents"]]
    preview = (await w["client"].post(_url(w, two) + "/bulk", json={"content_ids": ids, "action": "cover", "number": 3})).json()
    assert preview["applied"] is False and preview["changed"] == 2 and "Nothing is saved" in preview["sentence"]
    assert all(r["after"][0] == r["before"][2] for r in preview["rows"])
    async with w["sm"]() as s:
        assert all(shop_listing.photos_in(c, two) is None for c in (await s.execute(select(GeneratedContent))).scalars())
    too_far = (await w["client"].post(_url(w, two) + "/bulk", json={"content_ids": ids, "action": "cover", "number": 9})).json()
    assert too_far["changed"] == 0 and all(r["reason"] == "it has 3 photos" for r in too_far["rows"])
    done = (await w["client"].post(_url(w, two) + "/bulk", json={"content_ids": ids, "action": "cover", "number": 3, "apply": True})).json()
    assert done["applied"] is True and "Other shops are unchanged" in done["sentence"]
    last = (await w["client"].post(_url(w, two) + "/bulk", json={"content_ids": ids, "action": "cover_last", "apply": True})).json()
    for content in w["contents"]:
        cover, p2, p3 = w["photos"][content]
        mine = (await w["client"].get(_url(w, two, content))).json()
        # Photo 3 became the cover, then moved to the end: the listing's own order again, so it follows the listing.
        assert mine["order"] == [cover, p2, p3] and mine["own"] is False
        assert (await w["client"].get(_url(w, one, content))).json()["own"] is False
    assert last["changed"] == 2
    await w["engine"].dispose()


async def test_a_listing_live_in_a_shop_keeps_its_photos_there(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    one, content = w["shops"][0], w["contents"][0]
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        for pub in (await s.execute(select(ListingPublication))).scalars():
            pub.state, pub.published_at = "active", datetime.now(timezone.utc)
        await s.commit()
    sent = len(w["etsy"].requests)
    cover, p2, p3 = w["photos"][content]
    res = (await w["client"].put(_url(w, one, content), json={"order": [p3, p2, cover]})).json()
    assert res["note"].startswith("Already on Etsy in Shop 01: this order is for new drafts only")
    bulk = (await w["client"].post(_url(w, one) + "/bulk", json={"content_ids": [str(content)], "action": "cover_last"})).json()
    assert bulk["rows"][0]["note"].startswith("Already on Etsy")
    assert len(w["etsy"].requests) == sent  # nothing went to Etsy
    await w["engine"].dispose()


async def test_another_account_gets_404(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    two, content = w["shops"][1], w["contents"][0]
    other = await other_client(w)
    assert (await other.get(_url(w, two, content))).status_code == 404
    assert (await other.put(_url(w, two, content), json={"keep": True})).status_code == 404
    assert (await other.post(_url(w, two) + "/bulk", json={"content_ids": [str(content)], "action": "cover_last", "apply": True})).status_code == 404
    async with w["sm"]() as s:
        assert shop_listing.photos_in(await s.get(GeneratedContent, content), two) is None
    await w["engine"].dispose()

