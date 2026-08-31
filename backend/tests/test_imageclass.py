"""Reference-image size-chart classification: local-first, vision fallback."""

import io

from PIL import Image, ImageDraw

from app.pipeline.imageclass import (
    AnthropicImageKindClassifier,
    classify_reference_images,
    local_size_chart_kind,
)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _size_chart_image() -> bytes:
    # A near-white ground with black table rules and "text" dashes.
    img = Image.new("RGB", (240, 240), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y in range(20, 220, 30):
        d.line([(20, y), (220, y)], fill=(0, 0, 0), width=2)
    for x in range(20, 240, 60):
        d.line([(x, 20), (x, 200)], fill=(0, 0, 0), width=2)
    for y in range(30, 210, 30):
        for x in range(30, 210, 20):
            d.rectangle([x, y, x + 8, y + 4], fill=(0, 0, 0))
    return _png(img)


def _artwork_image() -> bytes:
    return _png(Image.new("RGB", (200, 200), (220, 20, 20)))  # solid saturated colour


# --- local heuristic --------------------------------------------------------
def test_local_heuristic_flags_a_size_chart() -> None:
    assert local_size_chart_kind(_size_chart_image()) == "size_chart"


def test_local_heuristic_flags_colourful_artwork() -> None:
    assert local_size_chart_kind(_artwork_image()) == "artwork"


def test_local_heuristic_returns_ambiguous_on_undecodable_bytes() -> None:
    assert local_size_chart_kind(b"not an image") == "ambiguous"


# --- classify_reference_images ---------------------------------------------
class _Fetcher:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def fetch(self, url: str):  # noqa: ANN201
        self.seen.append(url)
        return b"bytes", "image/png"


def _heuristic_by_url(url_map):
    def _h(_data: bytes) -> str:
        # The fetcher returns constant bytes, so decide by call order via a queue.
        return url_map.pop(0)

    return _h


def _images():
    return [
        {"listing_image_id": 1, "rank": 1, "url": "u/hero.jpg"},  # primary
        {"listing_image_id": 2, "rank": 2, "url": "u/chart.jpg"},  # heuristic: size_chart
        {"listing_image_id": 3, "rank": 3, "url": "u/amb.jpg"},  # heuristic: ambiguous -> vision
    ]


async def test_classifies_locally_and_falls_back_only_when_ambiguous() -> None:
    fetcher = _Fetcher()
    vision_calls: list[bytes] = []

    async def _vision(data: bytes, media_type: str) -> str:
        vision_calls.append(data)
        return "size_chart"

    images = _images()
    charts = await classify_reference_images(
        images,
        fetch_bytes=fetcher.fetch,
        heuristic=_heuristic_by_url(["size_chart", "ambiguous"]),  # for imgs 2 then 3
        vision=_vision,
    )

    assert charts == [2, 3]
    assert images[0]["kind"] == "artwork"  # primary never classified/fetched
    assert "u/hero.jpg" not in fetcher.seen
    assert set(fetcher.seen) == {"u/chart.jpg", "u/amb.jpg"}
    assert len(vision_calls) == 1  # only the ambiguous image hit the provider


async def test_reuses_prior_classification_without_fetching() -> None:
    fetcher = _Fetcher()
    charts = await classify_reference_images(
        _images(),
        fetch_bytes=fetcher.fetch,
        heuristic=lambda _d: "artwork",
        prior_kinds={2: "size_chart", 3: "artwork"},
    )
    assert charts == [2]
    assert fetcher.seen == []  # nothing fetched or re-classified


async def test_no_vision_defaults_ambiguous_to_artwork() -> None:
    fetcher = _Fetcher()
    images = [
        {"listing_image_id": 1, "rank": 1, "url": "u/hero.jpg"},
        {"listing_image_id": 2, "rank": 2, "url": "u/x.jpg"},
    ]
    charts = await classify_reference_images(
        images, fetch_bytes=fetcher.fetch, heuristic=lambda _d: "ambiguous"
    )
    assert charts == []  # no vision fallback -> conservative artwork
    assert images[1]["kind"] == "artwork"


async def test_anthropic_fallback_sends_bytes_not_url() -> None:
    class _FakeLLM:
        async def complete_json(self, **kwargs):
            blocks = kwargs["content_blocks"]
            img = next(b for b in blocks if b.get("type") == "image")
            assert img["source"]["type"] == "base64"  # bytes, not a URL to Etsy

            class R:
                data = {"kind": "size_chart"}

            return R()

    result = await AnthropicImageKindClassifier(_FakeLLM()).classify(b"bytes", "image/png")
    assert result == "size_chart"
