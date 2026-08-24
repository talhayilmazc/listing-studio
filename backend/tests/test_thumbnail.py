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


def test_already_square_solid_is_padded_and_resized() -> None:
    img = Image.new("RGB", (200, 200), (40, 160, 80))
    out = prepare_thumbnail(_png(img), padding_pct=8, size=2000)

    im = Image.open(io.BytesIO(out.data))
    assert im.size == (2000, 2000)
    cx = im.getpixel((1000, 1000))
    assert cx[1] > 120 and cx[0] < 90  # green centre
    assert im.getpixel((10, 10))[0] > 230  # padding corner is white
