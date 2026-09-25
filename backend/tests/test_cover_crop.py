"""The cover thumbnail adjuster: the seller's square becomes the listing's main photo."""

from __future__ import annotations

import io

from PIL import Image

from app.db.models import Asset, GeneratedContent
from app.pipeline.images import cover_image, crop_cover
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_publish_api import _add_content, ctx  # noqa: F401  (fixture)


def _halves(w: int, h: int) -> bytes:
    """Left half red, right half blue."""
    img = Image.new("RGB", (w, h), "red")
    img.paste(Image.new("RGB", (w // 2, h), "blue"), (w // 2, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _colour(data: bytes) -> tuple[int, int, int]:
    return Image.open(io.BytesIO(data)).convert("RGB").resize((1, 1)).getpixel((0, 0))


def test_the_chosen_square_is_the_photo() -> None:
    out = crop_cover(_halves(400, 200), {"x": 200, "y": 0, "size": 200, "width": 400, "height": 200}, size=300)
    assert (out.width, out.height, out.format) == (300, 300, "JPEG")
    r, g, b = _colour(out.data)
    assert b > 200 and r < 40  # only the blue half


def test_the_crop_lands_in_the_same_place_at_another_resolution() -> None:
    # Chosen on a 400x200 derivative, applied to 800x400 bytes.
    out = crop_cover(_halves(800, 400), {"x": 0, "y": 0, "size": 200, "width": 400, "height": 200}, size=100)
    r, _, b = _colour(out.data)
    assert r > 200 and b < 40  # only the red half


def test_a_square_past_the_edge_is_kept_inside_the_image() -> None:
    out = crop_cover(_halves(400, 200), {"x": 390, "y": 50, "size": 200, "width": 400, "height": 200}, size=50)
    r, _, b = _colour(out.data)
    assert b > 200 and r < 40  # moved back to x=200: still a full square of blue


def test_without_a_crop_the_automatic_square_is_used() -> None:
    out = cover_image(_halves(400, 200), None, size=120)
    assert (out.width, out.height) == (120, 120)


# --- the API ---------------------------------------------------------------------------
async def _asset(ctx):  # noqa: F811
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    async with ctx["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        asset = await s.get(Asset, content.asset_id)
        asset.width, asset.height, asset.processed_key = 1600, 2000, "p/1.jpg"
        await s.commit()
        return asset.id, content.batch_id


async def test_a_crop_is_saved_per_image_and_survives_a_reload(ctx) -> None:  # noqa: F811
    asset_id, batch_id = await _asset(ctx)
    resp = await ctx["client"].put(f"/api/assets/{asset_id}/cover-crop", json={"x": 100, "y": 300, "size": 1200})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"x": 100, "y": 300, "size": 1200, "width": 1600, "height": 2000}
    [a] = (await ctx["client"].get(f"/api/batches/{batch_id}")).json()["assets"]
    assert a["cover_crop"]["size"] == 1200

    assert (await ctx["client"].delete(f"/api/assets/{asset_id}/cover-crop")).status_code == 204
    [a] = (await ctx["client"].get(f"/api/batches/{batch_id}")).json()["assets"]
    assert a["cover_crop"] is None


async def test_a_square_outside_the_image_or_zoomed_too_far_is_refused(ctx) -> None:  # noqa: F811
    asset_id, _ = await _asset(ctx)
    for body in (
        {"x": 500, "y": 0, "size": 1200},  # past the right edge
        {"x": 0, "y": 0, "size": 1700},  # larger than the short side
        {"x": 0, "y": 0, "size": 100},  # more than 5x zoom
    ):
        assert (await ctx["client"].put(f"/api/assets/{asset_id}/cover-crop", json=body)).status_code == 422, body


async def test_only_the_owner_can_crop(ctx) -> None:  # noqa: F811
    asset_id, _ = await _asset(ctx)
    other = await make_tenant(ctx["sm"], "other@example.com")
    ctx["client"].cookies.clear()
    authenticate(ctx["client"], await open_session(ctx["redis"], other))
    resp = await ctx["client"].put(f"/api/assets/{asset_id}/cover-crop", json={"x": 0, "y": 0, "size": 1200})
    assert resp.status_code == 404
