"""A seller is told before sending when a listing names a well-known brand. We warn; the seller
decides: nothing is blocked that was not blocked before."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.compliance import trademarks as tm
from app.compliance.trademarks import Blocklist, all_marks, brand_warnings, bundled_blocklist, compile_blocklist, read_entries
from app.core.config import Settings
from app.db.models import GeneratedContent, Tenant
from app.ops.trademark_scan import run as trademark_scan
from app.pipeline.content import GeneratedListing, check_listing
from tests.auth_support import make_tenant
from tests.test_end_to_end import _world

LIST = Path(tm.__file__).parent / "trademarks.txt"
NO_FILTER = Blocklist()
TAGS = ["soft tee"]


def _terms(text: str) -> list[str]:
    return [s.term for s in bundled_blocklist().spot(text)]


def warning(brand: str) -> str:
    return f"{brand} is a trademark. Etsy often removes listings that use it without permission, and repeated removals can suspend a shop."


# --- the forms that were missed ------------------------------------------------------------------


@pytest.mark.parametrize(("text", "brand"), [
    ("DisneyWorld Trip Tee", "Disney"),
    ("disneyworld trip tee", "Disney"),
    ("DISNEYWORLD", "Disney"),
    ("DisneyPrincess crew", "Disney"),
    ("disneyprincess crew", "Disney"),
    ("#disney", "Disney"),
    ("#DisneyPrincess", "Disney"),
    ("#disneyprincess", "Disney"),
    ("disney-princess", "Disney"),
    ("disney_princess", "Disney"),
    ("ILoveDisney", "Disney"),
    ("ilovedisney", "Disney"),
    ("dis ney", "Disney"),
    ("DIS-NEY", "Disney"),
    ("d i s n e y", "Disney"),
    ("mick ey ears", "Mickey"),
    ("MICK EY", "Mickey"),
    ("mick_ey", "Mickey"),
    ("mickeymouse", "Mickey"),
    ("d1sney", "Disney"),
    ("dlsney", "Disney"),
    ("D1SN3Y", "Disney"),
    ("m1ckey", "Mickey"),
    ("m4rvel", "Marvel"),
    ("marve1", "Marvel"),
    ("p0kemon", "Pokemon"),
    ("$tarbucks", "Starbucks"),
    ("n1ke", "Nike"),
    ("nikes", "Nike"),
    ("disneyland", "Disneyland"),
    ("DisneyLand", "Disneyland"),
    ("dis ney land", "Disneyland"),
    ("Spiderman", "Spider-Man"),
    ("spider man", "Spider-Man"),
    ("StarWarsFan", "Star Wars"),
    ("N.F.L. fan", "NFL"),
    ("BatmanFan", "Batman"),
    ("Pokémon", "Pokemon"),
])
def test_each_form_is_caught(text: str, brand: str) -> None:
    assert brand in _terms(text), f"{text!r} found {_terms(text)}"


def test_disneyland_is_one_name_not_two() -> None:
    assert _terms("disneyland") == ["Disneyland"]
    assert _terms("DisneyLand") == ["Disneyland"]
    assert sorted(_terms("Disney and Disneyland")) == ["Disney", "Disneyland"]


@pytest.mark.parametrize("text", [
    "princess", "Princess Birthday Shirt", "marvelous", "marvellous", "Marvelous Mom", "MARVELLOUS", "marve1ous", "marvelled",
    "dis", "disc", "Disc Golf Shirt", "discount", "dismay", "mickle", "Mickle Family", "mick", "ey",
    "scavengers", "Scavenger Hunt", "lion kingdom", "Lion Kingdom Tee", "Blue Y Neck", "blue y", "snoop y", "Mini Kenya", "nikon",
    "nile", "like", "hike", "pixels", "pixie", "bat", "man", "wonder", "woman", "super", "Super Mom", "Star Dad", "mario",
    "barbeque", "barber", "grin", "gucc", "chanelle", "channel", "Channel Tee", "nhi", "mib", "nfi", "nb", "n b a", "sanity",
    "epic", "stitch", "lilo", "pooh", "Sesame Seed", "armour", "cocoa", "starbuck", "minion",
    "soft cotton tee for a family trip", "12345", "1l1", "0", "a4 paper", "$5 off",
])
def test_ordinary_words_are_not_brands(text: str) -> None:
    assert _terms(text) == [], f"{text!r} found {_terms(text)}"


def test_a_short_name_is_only_ever_whole() -> None:
    for form in ("nb4", "nbax", "xnba", "nh1", "m1b", "nf1"):
        assert _terms(form) == [], form
    assert _terms("NBA fan") == ["NBA"] and _terms("MyNBAFan") == ["NBA"]


# --- filter on and off ---------------------------------------------------------------------------


def _check(title: str, tags: list[str] = TAGS, description: str = "A soft cotton tee.", *, blocklist: Blocklist, hand_written: bool = True):  # noqa: ANN202
    return check_listing(GeneratedListing(title=title, tags=tags, description=description), None, hand_written=hand_written,
                         trademarks=blocklist, for_seller=True)


FILTER_ON = bundled_blocklist


@pytest.mark.parametrize(("title", "tags", "stopped"), [
    ("Disney Family Shirt", TAGS, "Remove: Disney (title)"),
    ("Mickey Mouse Ears Tee", TAGS, "Remove: Mickey (title)"),
    ("Disney Princess Sweatshirt", TAGS, "Remove: Disney (title)"),
    ("Marvel Avengers Hoodie", TAGS, "Remove: Marvel, Avengers (title)"),
    ("Family Tee", ["disney shirt", "mickey ears"], "Remove: disney shirt, mickey ears (tags)"),
])
def test_with_the_filter_on_it_stays_blocked_as_before(title: str, tags: list[str], stopped: str) -> None:
    check = _check(title, tags, blocklist=FILTER_ON())
    assert stopped in check.errors
    assert not check.valid and check.brands == []  # said once: "Remove" already says it


@pytest.mark.parametrize(("title", "tags", "brands"), [
    ("Disney Family Shirt", TAGS, ["Disney"]),
    ("Mickey Mouse Ears Tee", TAGS, ["Mickey"]),
    ("Disney Princess Sweatshirt", TAGS, ["Disney"]),
    ("Marvel Avengers Hoodie", TAGS, ["Marvel", "Avengers"]),
    ("Family Tee", ["disney shirt", "mickey ears"], ["Disney", "Mickey"]),
    ("DisneyWorld Trip Tee", TAGS, ["Disney"]),
])
def test_with_the_filter_off_it_warns_and_stops_nothing(title: str, tags: list[str], brands: list[str]) -> None:
    check = _check(title, tags, blocklist=NO_FILTER)
    assert check.valid and check.errors == []
    assert check.brands == [warning(b) for b in brands]


@pytest.mark.parametrize("text", ["DisneyWorld Trip Tee", "D1sney Trip Tee", "Mick Ey Tee", "#DisneyPrincess Tee"])
def test_a_form_that_passes_today_is_warned_about_with_the_filter_on_not_newly_blocked(text: str) -> None:
    check = _check(text, blocklist=FILTER_ON())
    assert check.valid, check.errors  # "nothing is blocked that is not blocked today"
    assert len(check.brands) == 1 and "is a trademark" in check.brands[0]


def test_a_brand_stopped_in_one_place_and_another_only_spotted_says_each_once() -> None:
    check = _check("Disney DisneyWorld and M4rvel", blocklist=FILTER_ON())
    assert "Remove: Disney (title)" in check.errors
    assert check.brands == [warning("Marvel")]


def test_a_clean_listing_says_nothing() -> None:
    assert _check("Retro Mountain Tee", blocklist=NO_FILTER).brands == []
    assert _check("Retro Mountain Tee", blocklist=FILTER_ON()).brands == []


def test_the_description_counts() -> None:
    assert _check("Retro Tee", description="Made for your DisneyWorld trip.", blocklist=NO_FILTER).brands == [warning("Disney")]


def test_a_filter_turned_off_for_the_whole_app_still_warns(test_settings: Settings) -> None:
    test_settings.trademark_filter = False
    assert brand_warnings("Disney Tee", [], "", NO_FILTER) == [warning("Disney")]


# --- hand-written and AI: one check --------------------------------------------------------------


@pytest.mark.parametrize("title", ["Disney Family Shirt", "DisneyWorld Trip Tee", "Retro Mountain Tee"])
@pytest.mark.parametrize("blocklist", [NO_FILTER, compile_blocklist(["Disney"])])
def test_hand_written_and_ai_listings_get_exactly_the_same_check(title: str, blocklist: Blocklist) -> None:
    by_hand = _check(title, blocklist=blocklist, hand_written=True)
    by_app = _check(title, blocklist=blocklist, hand_written=False)
    assert (by_hand.errors, by_hand.warnings, by_hand.brands) == (by_app.errors, by_app.warnings, by_app.brands)


# --- the list: one dated file, a note on every entry, one module ---------------------------------


def test_the_list_is_dated_and_every_entry_says_where_it_comes_from() -> None:
    assert tm.list_date() == "2026-10-31"
    entries = read_entries(LIST)
    assert len(entries) > 50
    for term, note in entries:
        assert note, f"{term} has no source note"
    assert len({t.casefold() for t, _ in entries}) == len(entries)


def test_a_plain_list_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "list.txt"
    path.write_text("# a comment\nAcme\nAcme Tools | Acme Inc\n", encoding="utf-8")
    assert read_entries(path) == [("Acme", ""), ("Acme Tools", "Acme Inc")]
    assert tm.read_terms(path) == ["Acme", "Acme Tools"]


def test_the_module_needs_no_database_and_no_request_so_our_tools_can_use_it() -> None:
    source = Path(tm.__file__).read_text(encoding="utf-8")
    top = "\n".join(line for line in source.splitlines() if line.startswith(("from ", "import ")))
    assert "app.db" not in top and "app.api" not in top and "fastapi" not in top
    assert "app.core.config" in top


def test_nothing_else_keeps_a_list_of_brands() -> None:
    app = Path(tm.__file__).parents[1]
    for path in app.rglob("*.py"):
        assert not re.search(r"""["'](disney|mickey|marvel)["']""", path.read_text(encoding="utf-8"), re.I), path


# --- the screens' data: card, workspace row, send summary; per-shop text ------------------------------


async def _brand_world(tmp_path, test_settings: Settings, monkeypatch, *, filter_on: bool, shops: int = 2) -> dict:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=shops, listings=2)
    async with w["sm"]() as s:
        (await s.get(Tenant, w["tenant"])).trademark_filter_seller = filter_on
        first = await s.get(GeneratedContent, w["contents"][0])
        first.title = "DisneyWorld " + (first.title or "")[:60]
        await s.commit()
    return w


@pytest.mark.parametrize("filter_on", [True, False])
async def test_the_card_the_row_and_the_send_preview_all_say_it_and_it_can_still_be_sent(tmp_path, test_settings, monkeypatch, filter_on: bool) -> None:
    w = await _brand_world(tmp_path, test_settings, monkeypatch, filter_on=filter_on)
    c = w["client"]
    contents = (await c.get(f"/api/batches/{w['batch']}/content")).json()
    by_id = {x["id"]: x for x in contents}
    assert by_id[str(w["contents"][0])]["validation"]["brand_warnings"] == [warning("Disney")]
    assert by_id[str(w["contents"][1])]["validation"]["brand_warnings"] == []
    rows = (await c.get(f"/api/batches/{w['batch']}/shops/{w['shops'][1]}/rows")).json()["rows"]
    row = next(r for r in rows if r["content_id"] == str(w["contents"][0]))
    assert row["brand_warnings"] == [warning("Disney")] and row["errors"] == []
    preview = (await c.post(f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()
    assert preview["brand_warnings"] == [warning("Disney")] and preview["disabled"] is None
    sent = await c.post(f"/api/batches/{w['batch']}/send", json={"when": "drafts"})
    assert sent.status_code == 200 and len(sent.json()["jobs"]) == 2
    await w["engine"].dispose()


async def test_with_the_filter_off_a_whole_brand_warns_and_goes_with_the_filter_on_it_is_stopped(tmp_path, test_settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    c = w["client"]
    async with w["sm"]() as s:
        (await s.get(GeneratedContent, w["contents"][0])).title = "Disney Family Trip Tee"
        await s.commit()
    on = (await c.get(f"/api/batches/{w['batch']}/content")).json()[0]["validation"]
    assert on["valid"] is False and "Remove: Disney (title)" in on["errors"] and on["brand_warnings"] == []
    async with w["sm"]() as s:
        (await s.get(Tenant, w["tenant"])).trademark_filter_seller = False
        await s.commit()
    off = (await c.get(f"/api/batches/{w['batch']}/content")).json()[0]["validation"]
    assert off["valid"] is True and off["brand_warnings"] == [warning("Disney")]
    await w["engine"].dispose()


async def test_a_shops_own_text_is_checked_for_that_shop_only(tmp_path, test_settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=1)
    c = w["client"]
    second = w["shops"][1]
    res = await c.put(f"/api/batches/{w['batch']}/shops/{second}/texts", json={"texts": {str(w["contents"][0]): {"title": "M4rvel Fan Tee Shirt"}}})
    assert res.status_code == 200, res.text
    assert res.json()[0]["brand_warnings"] == [warning("Marvel")]
    there = (await c.get(f"/api/batches/{w['batch']}/shops/{second}/rows")).json()["rows"][0]
    here = (await c.get(f"/api/batches/{w['batch']}/shops/{w['shops'][0]}/rows")).json()["rows"][0]
    assert there["brand_warnings"] == [warning("Marvel")] and here["brand_warnings"] == []
    await w["engine"].dispose()


async def test_saving_a_card_answers_with_the_warning_at_once(tmp_path, test_settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    res = await w["client"].patch(f"/api/content/{w['contents'][0]}", json={"title": "DisneyPrincess Birthday Family Tee"})
    assert res.status_code == 200, res.text
    assert res.json()["validation"]["brand_warnings"] == [warning("Disney")]
    await w["engine"].dispose()


# --- the command ---------------------------------------------------------------------------------


async def test_the_command_lists_brands_where_and_what_the_seller_sees_only_for_that_seller(tmp_path, test_settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=3)
    c = w["client"]
    sm = w["sm"]
    other = await make_tenant(sm, "other-seller@example.com")
    async with sm() as s:  # Another account's listing with a brand of its own: never in this seller's report.
        from app.db.models import Asset, AssetStatus, UploadBatch, UploadBatchStatus

        theirs = UploadBatch(tenant_id=other, status=UploadBatchStatus.ready, file_count=1)
        s.add(theirs)
        await s.flush()
        asset = Asset(batch_id=theirs.id, tenant_id=other, original_filename="a.png", storage_key="k", status=AssetStatus.processed, rank=1)
        s.add(asset)
        await s.flush()
        s.add(GeneratedContent(tenant_id=other, batch_id=theirs.id, asset_id=asset.id, title="Gucci Secret Tee", tags=[], description="d"))
        await s.commit()
    async with sm() as s:
        tenant = await s.get(Tenant, w["tenant"])
        first = await s.get(GeneratedContent, w["contents"][0])
        first.title, first.tags = "Disney Family Trip Tee", ["mickey ears", "soft tee"]
        second = await s.get(GeneratedContent, w["contents"][1])
        second.title = "DisneyPrincess Birthday Tee"
        email = tenant.email
        await s.commit()
    res = await c.put(f"/api/batches/{w['batch']}/shops/{w['shops'][1]}/texts", json={"texts": {str(w["contents"][2]): {"title": "M4rvel Fan Tee"}}})
    assert res.status_code == 200, res.text
    before = len(w["etsy"].requests)
    report = await trademark_scan(sm, email)
    assert len(w["etsy"].requests) == before  # no Etsy call
    assert "filter is ON" in report
    assert "Disney in the title" in report and "Mickey in the tags" in report
    assert 'blocked: "Remove: Disney (title), mickey ears (tag)"' in report
    assert 'Disney in the title (written "disney")' in report or "Disney in the title" in report
    assert "filter off: warned, can send: Disney, Mickey" in report
    assert "filter on: warned, can send: Disney" in report  # DisneyPrincess passes today, and is warned about
    assert "its text for Shop 02" in report and "M4rvel" in report and "Marvel in the title" in report
    assert "With a listed name: 3" in report
    assert "Gucci" not in report and "other-seller" not in report
    assert await trademark_scan(sm, "nobody@example.com") == "no account with the email 'nobody@example.com'"
    async with sm() as s:
        unchanged = (await s.execute(select(GeneratedContent).where(GeneratedContent.id == w["contents"][1]))).scalar_one()
    assert unchanged.title == "DisneyPrincess Birthday Tee"
    isolated = await trademark_scan(sm, "other-seller@example.com")
    assert "Gucci in the title" in isolated and "Disney" not in isolated and "With a listed name: 1" in isolated
    await w["engine"].dispose()


def test_the_command_is_on_the_cli_and_opens_a_read_only_transaction() -> None:
    root = Path(tm.__file__).parents[1]
    assert '"trademark-scan"' in (root / "cli.py").read_text(encoding="utf-8")
    assert 'SET TRANSACTION READ ONLY' in (root / "ops/trademark_scan.py").read_text(encoding="utf-8")


def test_all_marks_is_the_whole_list_whatever_the_filter_says(test_settings: Settings) -> None:
    test_settings.trademark_filter = False
    assert all_marks().find("Disney") == ["Disney"]
