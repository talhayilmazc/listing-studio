"""The trademark decision is the account's, the same in every shop (v8 §E).

The same Mickey/Disney listing was allowed with a warning in one shop and
blocked in another ("with this shop's title prefix remove the trademark
'Disney'..."), because the path that rebuilds the title with another shop's
prefix checked it against the app's default filter (on) instead of the
account's. With the filter on, it is blocked in every shop; with it off, it
shows the warning in every shop; never one of each. Problems are shown once,
as a short list.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.compliance.check import listing_problem
from app.compliance.scanner import rescan
from app.db.models import ComplianceFinding, EtsyConnection, GeneratedContent, ListingProfile, Tenant
from app.pipeline.targets import resolve_target
from tests.test_multi_shop import world  # noqa: F401  (fixture)

TITLE = ("Comfort Colors®, Mickey Ears Tee, Disney Trip Shirt, Magic Castle Family Vacation Top, "
         "Theme Park Gift, Matching Group Tees, Fun Holiday")


async def _mickey(world, *, filter_on: bool):  # noqa: F811, ANN202
    c0 = world["contents"][0]
    async with world["sm"]() as s:
        alice = await s.get(Tenant, world["alice"])
        alice.trademark_filter = None
        alice.trademark_filter_seller = filter_on
        content = await s.get(GeneratedContent, c0)
        content.title = TITLE
        content.tags = ["mickey mouse tee", *[f"tag{n}" for n in range(12)]]
        content.attributes = {"vision": {"characters": ["Mickey Mouse"]}}
        # The two shops' profiles have different title prefixes (Frost Tees: "Comfort Colors®", Frost Mugs: none).
        assert (await s.get(ListingProfile, world["pa1"])).title_prefix != (await s.get(ListingProfile, world["pa2"])).title_prefix
        await rescan(s, content)
        await s.commit()
    return c0


def _cells(preview: dict, content_id) -> dict[str, tuple]:  # noqa: ANN001
    row = next(r for r in preview["rows"] if r["content_id"] == str(content_id))
    return {c["connection_id"]: (c["state"], c["reason"]) for c in row["cells"]}


@pytest.mark.parametrize("filter_on", [True, False])
async def test_the_same_listing_gets_the_same_result_in_every_shop(world, filter_on: bool) -> None:  # noqa: F811
    c0 = await _mickey(world, filter_on=filter_on)
    preview = (await world["a"].post(f"/api/batches/{world['batch']}/publish/preview", json=None)).json()
    cells = _cells(preview, c0)
    one, two = cells[str(world["a1"])], cells[str(world["a2"])]
    assert one[0] == two[0] == ("unavailable" if filter_on else "available"), cells
    assert one[1] == two[1]  # the same reason, or none, in both shops

    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, c0)
        targets = [await resolve_target(s, content, await s.get(EtsyConnection, shop)) for shop in (world["a1"], world["a2"])]
        findings = list((await s.execute(
            select(ComplianceFinding).where(ComplianceFinding.generated_content_id == c0))).scalars())
        problem = await listing_problem(s, content)
    # Rebuilding the title with the other shop's prefix uses the account's decision too.
    assert [t.ok for t in targets] == ([True, True] if not filter_on else [t.ok for t in targets])
    assert not any("trademark" in (t.reason or "").lower() or "Remove:" in (t.reason or "") for t in targets if not filter_on)
    severities = {(f.rule, f.severity.value) for f in findings}
    if filter_on:
        assert problem is not None and problem.count("Remove:") == 1
        assert problem.startswith("Remove: ") and "mickey mouse tee (tag)" in problem
        assert "Disney" in problem and "Mickey" in problem and "(title)" in problem
        assert ("character_artwork", "blocking") in severities and ("trademark", "blocking") in severities
    else:
        assert problem is None
        assert severities == {("character_artwork", "warning")}  # a warning in every shop, never a block


async def test_the_cross_shop_title_check_reads_the_accounts_filter(world) -> None:  # noqa: F811
    c0 = await _mickey(world, filter_on=False)
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, c0)
        other_shop = await resolve_target(s, content, await s.get(EtsyConnection, world["a2"]))
    # The title was rebuilt with Frost Mugs' (empty) prefix and is not refused for a trademark.
    assert other_shop.profile.id == world["pa2"] and other_shop.ok, other_shop.reason
    assert "Disney" in other_shop.title
