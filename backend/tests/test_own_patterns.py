"""v7 §B: a new listing modelled on one of the seller's OWN listings.

Only the seller's own shop is ever the source (ToU §1 and §5); the pattern is
kept, the subject comes from the new design.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import ShopListingCache, Tenant
from app.pipeline.content import AnthropicContentGenerator, example_errors, policy_for
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.templates import load_template
from app.pipeline.vision import VisionAnalysis
from tests.auth_support import authenticate, make_tenant, open_session
from tests.support import FakeMessages, fake_response
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)

EXAMPLE = {"title": "Funny Nurse Shirt, Flu Season Humor Tee, Nurse Gift for Her, Hospital Staff Sweatshirt",
           "tags": ["funny nurse shirt", "nurse gift", "flu season"]}


async def _own_listing(ctx, listing_id=4001, *, hours_old=1.0, feature=True):  # noqa: F811
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        s.add(ShopListingCache(tenant_id=ctx["tenant_id"], connection_id=shop, listing_id=listing_id,
                               payload={"listing_id": listing_id, "state": "active", "title": EXAMPLE["title"],
                                        "tags": EXAMPLE["tags"], "url": f"https://www.etsy.com/listing/{listing_id}"},
                               fetched_at=datetime.now(timezone.utc) - timedelta(hours=hours_old)))
        (await s.get(Tenant, ctx["tenant_id"])).features = {"own_patterns": feature}
        await s.commit()


async def test_only_the_sellers_own_listings_are_offered(ctx) -> None:  # noqa: F811
    await _own_listing(ctx)
    rows = (await ctx["client"].get("/api/shop/pattern-listings?q=nurse")).json()
    assert [r["listing_id"] for r in rows] == [4001]
    assert rows[0]["url"].endswith("/4001")  # every listing shown links back to Etsy

    other = await make_tenant(ctx["sm"], "other@example.com")
    async with ctx["sm"]() as s:
        (await s.get(Tenant, other)).features = {"own_patterns": True}
        await s.commit()
    ctx["client"].cookies.clear()
    authenticate(ctx["client"], await open_session(ctx["redis"], other))
    assert (await ctx["client"].get("/api/shop/pattern-listings")).json() == []


async def test_another_accounts_listing_cannot_be_chosen(ctx) -> None:  # noqa: F811
    await _own_listing(ctx)
    content = await _add_content(ctx["sm"], ctx["tenant_id"])
    from app.db.models import GeneratedContent

    async with ctx["sm"]() as s:
        batch = (await s.get(GeneratedContent, content)).batch_id
    ok = await ctx["client"].put(f"/api/batches/{batch}/groups/pattern", json={"group_key": "", "pattern_listing_id": 4001})
    assert ok.status_code == 200 and ok.json()[0]["pattern_listing_id"] == 4001
    bad = await ctx["client"].put(f"/api/batches/{batch}/groups/pattern", json={"group_key": "", "pattern_listing_id": 9999})
    assert bad.status_code == 404


async def test_the_feature_is_off_unless_an_admin_turned_it_on(ctx) -> None:  # noqa: F811
    await _own_listing(ctx, feature=False)
    assert (await ctx["client"].get("/api/shop/pattern-listings")).status_code == 404


def test_copying_the_example_title_is_refused() -> None:
    assert example_errors(EXAMPLE["title"], EXAMPLE["title"])
    assert example_errors("Christmas Teacher Sweatshirt, Flu Season Humor Tee, Nurse Gift for Her Now", EXAMPLE["title"])
    # Short stock phrases may repeat: that is the pattern.
    assert example_errors("Funny Teacher Shirt, Back to School Humor Tee, Teacher Gift for Her", EXAMPLE["title"]) == []


async def test_the_example_goes_to_the_model_as_a_pattern() -> None:
    title = "Funny Teacher Shirt, Back to School Humor Tee, Teacher Gift for Her, Classroom Staff Sweatshirt, Fall School Tee"
    tags = ["funny teacher shirt", "teacher gift", "back to school", "teacher tee", "school staff", "fall shirt",
            "classroom humor", "teacher humor", "gift for teacher", "school sweatshirt", "teacher life", "school tee", "fall tee"]
    msgs = FakeMessages([fake_response({"title": title, "tags": tags, "description": "d"})])
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=msgs)
    gen = AnthropicContentGenerator(client, load_template("content/apparel"), policy=policy_for("apparel"))
    a = VisionAnalysis(theme="teacher", themes=["teacher"], embedded_text="", style="", colors=[],
                       target_audience="teachers", product_type_hints=[])
    result = await gen.generate(a, None, EXAMPLE)
    assert result.listing.title == title
    text = "\n".join(b["text"] for b in msgs.calls[0]["messages"][0]["content"] if b["type"] == "text")
    assert "Example title: Funny Nurse Shirt" in text and "never from the example" in text
