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
