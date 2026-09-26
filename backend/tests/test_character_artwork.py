"""Recognisable characters in the artwork (after the v7 model comparison).

The artwork itself may infringe however the title is worded, so a generic
description must not make it pass: with the trademark filter on it blocks, like
a trademark in the text; with it off, the seller sees a warning before publishing.
"""

from __future__ import annotations

from sqlalchemy import select

from app.compliance.scanner import CHARACTER_ARTWORK, scan
from app.compliance.trademarks import Blocklist, compile_blocklist
from app.db.models import ComplianceSeverity, GeneratedContent, Tenant
from app.pipeline.vision import _to_analysis
from tests.test_publish_api import _add_content, ctx  # noqa: F401  (fixture)

TITLE = "Cute Christmas Sweatshirt"


def test_vision_names_the_characters_it_sees() -> None:
    a = _to_analysis({"themes": ["christmas"], "embedded_text": "", "style": "", "colors": [],
                      "target_audience": "", "product_type_hints": [], "characters": ["Minnie Mouse", " "]})
    assert a.characters == ["Minnie Mouse"]


def test_with_the_filter_on_character_artwork_blocks() -> None:
    [f] = scan(TITLE, [], "d", compile_blocklist(["Disney"]), characters=["Minnie Mouse"])
    assert (f.rule, f.severity) == (CHARACTER_ARTWORK, ComplianceSeverity.blocking)
    assert "Minnie Mouse" in f.detail and "however the title is worded" in f.detail


def test_with_the_filter_off_it_is_a_warning() -> None:
    [f] = scan(TITLE, [], "d", Blocklist(), characters=["Winnie the Pooh"])
    assert f.severity is ComplianceSeverity.warning


def test_an_original_design_gets_no_finding() -> None:
    assert scan(TITLE, [], "d", compile_blocklist(["Disney"]), characters=[]) == []


async def _character_listing(ctx):  # noqa: F811
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True)
    async with ctx["sm"]() as s:
        c = await s.get(GeneratedContent, content_id)
        c.attributes = {"vision": {"characters": ["Minnie Mouse"]}}
        await s.commit()
    # Generation scans a new listing; approving scans it again, as here.
    await ctx["client"].post(f"/api/content/{content_id}/approve", json={"approved": True})
    return content_id, c.batch_id


async def test_a_generic_title_does_not_get_character_artwork_past_the_filter(ctx) -> None:  # noqa: F811
    content_id, batch = await _character_listing(ctx)
    resp = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert resp.status_code == 409 and "Minnie Mouse" in resp.json()["detail"]
    [row] = (await ctx["client"].get(f"/api/batches/{batch}/content")).json()
    assert [(f["rule"], f["severity"]) for f in row["findings"]] == [("character_artwork", "blocking")]


async def test_a_seller_who_chose_the_risk_sees_the_warning_and_can_publish(ctx) -> None:  # noqa: F811
    content_id, batch = await _character_listing(ctx)
    async with ctx["sm"]() as s:
        (await s.get(Tenant, ctx["tenant_id"])).trademark_filter = False
        await s.commit()
    body = (await ctx["client"].post(f"/api/content/{content_id}/publish")).json()
    assert len(body["jobs"]) == 1
    [row] = (await ctx["client"].get(f"/api/batches/{batch}/content")).json()
    assert [(f["rule"], f["severity"]) for f in row["findings"]] == [("character_artwork", "warning")]


def test_a_character_named_only_as_a_theme_still_counts() -> None:
    from app.compliance.trademarks import characters_seen

    # Seen in the real run: characters empty, the character in the themes.
    assert characters_seen([], ["christmas", "minnie mouse", "holiday coffee"]) == ["minnie mouse"]
    assert characters_seen(["Winnie the Pooh"], ["christmas", "winnie the pooh"]) == ["Winnie the Pooh"]
    assert characters_seen([], ["christmas", "nurse"]) == []
