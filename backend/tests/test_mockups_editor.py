"""The mockup editor (feature/mockups-editor; docs/mockups.md).

B1: every template takes the seller's placement, a Photoshop mockup's too: the design is moved, scaled, stretched
and turned inside the smart object's canvas, as if the layer were resized inside the smart object in Photoshop,
then the canvas goes onto the page through its corners as always. "Centre" puts the design's middle on the
area's middle; the automatic fit is unchanged.
"""

from __future__ import annotations

import numpy as np

from app.pipeline import design_edits as E
from app.pipeline import psd_template as T


def _spec(w: int = 400, h: int = 300) -> T.Spec:
    # A smart object 200 x 200 on the page at (100, 50), straight on: canvas 1000 x 1000.
    return T.Spec(width=w, height=h, canvas=(1000, 1000), quad=[(100, 50), (300, 50), (300, 250), (100, 250)])


def _design(w: int = 500, h: int = 250) -> np.ndarray:
    d = np.zeros((h, w, 4), np.uint8)
    d[..., 0], d[..., 3] = 220, 255  # red, opaque
    return d


def _red_box(page: np.ndarray) -> tuple[int, int, int, int]:
    red = (page[..., 0] > 150) & (page[..., 1] < 80) & (page[..., 2] < 80)
    ys, xs = np.nonzero(red)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def test_without_an_adjustment_a_psd_is_placed_as_before() -> None:
    base = np.full((300, 400, 3), 240, np.uint8)
    before = T.render(_spec(), base, _design())
    assert np.array_equal(before, T.render(_spec(), base, _design(), adjust=None))
    # Contain, centred in the 200 x 200 smart object: 200 x 100, from y 100 to 200.
    left, top, right, bottom = _red_box(before)
    assert (abs(left - 100) <= 1, abs(right - 299) <= 1, abs(top - 100) <= 1, abs(bottom - 199) <= 1) == (True,) * 4


def test_a_psd_design_is_scaled_moved_and_turned_inside_the_smart_object() -> None:
    base = np.full((300, 400, 3), 240, np.uint8)
    # Half the size, centred: 100 x 50 around the middle (200, 150).
    left, top, right, bottom = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 0.5}))
    assert abs((left + right) / 2 - 200) <= 1.5 and abs((top + bottom) / 2 - 150) <= 1.5
    assert abs((right - left) - 100) <= 2 and abs((bottom - top) - 50) <= 2
    # Moved right by a tenth of the canvas: 20 page pixels.
    left2, _, right2, _ = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 0.5, "dx": 0.1}))
    assert abs((left2 + right2) / 2 - 220) <= 1.5
    # Turned 90 degrees: tall instead of wide.
    l3, t3, r3, b3 = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 0.5, "angle": 90}))
    assert (b3 - t3) > (r3 - l3) * 1.6
    # What leaves the canvas is cut at the smart object's edge, as in Photoshop.
    l4, t4, r4, b4 = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 2.0}))
    assert l4 >= 99 and r4 <= 300 and t4 >= 49 and b4 <= 250


def test_a_transparent_design_keeps_no_dark_edge_when_moved() -> None:
    """Premultiplied sampling: a white design with soft edges on a white page leaves no grey ring."""
    d = np.zeros((300, 300, 4), np.uint8)
    yy, xx = np.mgrid[0:300, 0:300]
    d[..., :3] = 255
    d[..., 3] = np.clip(255 - np.hypot(xx - 150, yy - 150) * 3, 0, 255).astype(np.uint8)  # a soft white dot
    base = np.full((300, 400, 3), 255, np.uint8)
    page = T.render(_spec(), base, d, adjust={"scale": 0.7, "angle": 17, "dx": 0.05})
    assert page.min() >= 250, page.min()


def test_the_look_applies_to_a_psd_design_too() -> None:
    base = np.full((300, 400, 3), 240, np.uint8)
    faded = T.render(_spec(), base, _design(), adjust={"opacity": 0.5})
    r, g = int(faded[150, 200, 0]), int(faded[150, 200, 1])
    assert 100 < g < 140 and r > 220  # half red over the light grey page: green halves, red stays


def test_centre_puts_the_middle_on_the_area_middle_in_both_alignments() -> None:
    quad = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    for align, size in (("top", 0.9), ("centre", 1.0)):
        a = E.centred(quad, (200, 100), size, {"dx": 0.2, "dy": -0.1, "scale": 0.8, "angle": 12}, align)
        pts = E.unit_points(quad, (200, 100), size, a, align)
        cx, cy = sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4
        assert abs(cx - 0.5) < 1e-6 and abs(cy - 0.5) < 1e-6, (align, cx, cy)
        assert a["scale"] == 0.8 and a["angle"] == 12  # size and turn kept
    # A PSD's automatic fit is already centred: Centre of no adjustment is no adjustment.
    assert E.centred(quad, (200, 100), 1.0, None, "centre") is None


def test_the_editor_is_told_how_a_psd_design_starts() -> None:
    import inspect

    from app.api import mockup_edits
    from app.workers import mockups as work

    source = inspect.getsource(mockup_edits)
    assert 'size=1.0 if p.psd else p.size, align="centre" if p.psd else "top"' in source
    # The run renders a PSD photo with the stored adjustment (it used to be left out).
    assert "placements[0][3])" in inspect.getsource(work.run)


def test_the_editor_previews_a_design_at_most_1200_px_with_its_transparency() -> None:
    """B2/B7: the editor and the tiles draw a small copy; the full file is for rendering only."""
    import io

    from PIL import Image

    from app.api.mockup_edits import PREVIEW_SIDE, design_preview

    big = Image.new("RGBA", (4000, 3000), (0, 0, 0, 0))
    big.paste((200, 30, 30, 255), (1000, 1000, 3000, 2000))
    buf = io.BytesIO()
    big.save(buf, "PNG")
    small = Image.open(io.BytesIO(design_preview(buf.getvalue())))
    assert max(small.size) == PREVIEW_SIDE and small.size == (1200, 900)
    assert small.mode == "RGBA" and small.getpixel((10, 10))[3] == 0 and small.getpixel((600, 450))[3] == 255
    tiny = io.BytesIO()
    Image.new("RGBA", (600, 400), (0, 0, 0, 0)).save(tiny, "PNG")
    assert Image.open(io.BytesIO(design_preview(tiny.getvalue()))).size == (600, 400)  # never enlarged
