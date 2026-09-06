"""prepare_thumbnail tests: transparent PNG, flat background, already-square."""

import io

from PIL import Image

from app.pipeline.images import prepare_thumbnail


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_transparent_png_trimmed_padded_squared() -> None:
    img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (100, 100), (200, 30, 30, 255)), (150, 150))
    out = prepare_thumbnail(_png(img), padding_pct=8, size=2000)

    im = Image.open(io.BytesIO(out.data))
    assert im.size == (2000, 2000)
    assert im.format == "JPEG"
    cx = im.getpixel((1000, 1000))  # centre = the red design
    assert cx[0] > 160 and cx[1] < 90 and cx[2] < 90
    assert im.getpixel((20, 20))[0] > 230  # padding corner is white


def test_flat_background_trimmed() -> None:
    img = Image.new("RGB", (400, 400), (255, 255, 255))
    img.paste(Image.new("RGB", (100, 100), (30, 60, 200)), (150, 150))
    out = prepare_thumbnail(_png(img), size=2000)

    im = Image.open(io.BytesIO(out.data))
    assert im.size == (2000, 2000)
    cx = im.getpixel((1000, 1000))  # centre = the blue design
    assert cx[2] > 150 and cx[0] < 90
    assert im.getpixel((20, 20))[0] > 230  # padding corner is white


def test_portrait_mockup_cropped_no_border() -> None:
    # A non-square photographic mockup is centre-cropped to a square: NO white bars.
    img = Image.new("RGB", (120, 200), (200, 30, 30))
    out = prepare_thumbnail(_png(img), size=2000)  # crop is the default

    im = Image.open(io.BytesIO(out.data))
    assert im.size == (2000, 2000)  # square
    for xy in [(10, 10), (1990, 10), (1000, 1000), (10, 1990), (1990, 1990)]:
        r, g, b = im.getpixel(xy)
        assert r > 160 and g < 90 and b < 90, xy  # design colour everywhere, no background


def test_pad_mode_adds_border_on_portrait() -> None:
    # The opt-in pad mode keeps the framed look: white bars on a portrait's sides.
    img = Image.new("RGB", (120, 200), (200, 30, 30))
    out = prepare_thumbnail(_png(img), padding_pct=8, size=2000, mode="pad")

    im = Image.open(io.BytesIO(out.data))
    assert im.size == (2000, 2000)
    assert im.getpixel((1000, 1000))[0] > 160  # red design centred
    assert im.getpixel((20, 1000))[0] > 230  # left-edge padding is white


# --- Preview derivatives (UI tiles; never touches what is uploaded to Etsy) ---


def test_preview_crops_around_off_centre_design() -> None:
    """A 4:5 tile centres on the artwork, not on the frame."""
    from app.pipeline.images import resize_preview

    # Portrait "mockup": design sits high, where a centre crop would miss it.
    img = Image.new("RGB", (600, 1000), (255, 255, 255))
    img.paste(Image.new("RGB", (200, 200), (200, 30, 30)), (200, 100))
    out = resize_preview(_png(img), 224, "4:5")

    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG"
    assert abs(im.width / im.height - 4 / 5) < 0.01  # exactly 4:5
    # The design survives whole and centred, rather than being sliced off.
    r, g, b = im.getpixel((im.width // 2, im.height // 2))
    assert r > 160 and g < 90 and b < 90


def test_preview_centre_crop_would_have_missed_it() -> None:
    """Guard the point of the change: a frame-centred crop loses this design."""
    from app.pipeline.images import resize_preview

    img = Image.new("RGB", (600, 1000), (255, 255, 255))
    img.paste(Image.new("RGB", (160, 160), (30, 60, 200)), (220, 60))

    # What a naive frame-centred 4:5 crop would have produced: white, no design.
    naive = img.crop((100, 375, 500, 875))
    assert naive.getpixel((200, 250))[0] > 200  # red high => white, design absent

    im = Image.open(io.BytesIO(resize_preview(_png(img), 224, "4:5")))
    r, g, b = im.getpixel((im.width // 2, im.height // 2))
    assert r < 90 and b > 150  # content-aware crop keeps the blue design


def test_preview_transparent_design_flattens_to_white() -> None:
    """Transparent PNGs must not render on black."""
    from app.pipeline.images import resize_preview

    img = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (300, 300), (200, 30, 30, 255)), (250, 250))
    im = Image.open(io.BytesIO(resize_preview(_png(img), 224, "4:5")))

    assert im.getpixel((112, 140))[0] > 160  # design
    assert im.getpixel((5, 5))[0] > 230  # surround is white, not black


def test_preview_without_aspect_keeps_proportions() -> None:
    from app.pipeline.images import resize_preview

    img = Image.new("RGB", (800, 500), (120, 120, 120))
    im = Image.open(io.BytesIO(resize_preview(_png(img), 224)))
    assert im.size == (224, 140)  # 8:5 preserved


def test_preview_16_10_hero_ratio() -> None:
    from app.pipeline.images import resize_preview

    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    img.paste(Image.new("RGB", (400, 400), (30, 160, 60)), (300, 300))
    im = Image.open(io.BytesIO(resize_preview(_png(img), 448, "16:10")))
    assert im.size == (448, 280)


def test_wide_crop_of_tall_mockup_keeps_the_design() -> None:
    """A 16:10 window over a tall mockup anchors to the top of the content.

    Mirrors a worn-garment photo: the detected content is the whole figure, whose
    midpoint is at the waist, while the print sits high on the chest. Centring
    would cut the artwork in half.
    """
    from app.pipeline.images import resize_preview

    img = Image.new("RGB", (800, 1600), (255, 255, 255))
    img.paste(Image.new("RGB", (600, 1500), (230, 220, 210)), (100, 50))  # the figure
    img.paste(Image.new("RGB", (300, 200), (200, 30, 30)), (250, 300))  # the print

    im = Image.open(io.BytesIO(resize_preview(_png(img), 448, "16:10")))
    assert abs(im.width / im.height - 16 / 10) < 0.01

    # The print is present and unclipped: sample its full height in the output.
    scale = im.width / 800  # crop spans the full width here
    top, bottom = round((300 - 50) * scale), round((500 - 50) * scale)
    for y in (top + 2, (top + bottom) // 2, bottom - 2):
        r, g, b = im.getpixel((im.width // 2, y))
        assert r > 160 and g < 90 and b < 90, y
