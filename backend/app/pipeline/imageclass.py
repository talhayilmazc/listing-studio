"""Classify a reference listing's images as size charts vs product artwork.

**Local-first.** A fast Pillow/libvips heuristic decides most images from their
pixels alone — size charts are visually distinctive: a near-white background, low
colour variance, and dense text / table edges. Classification runs on our own
server from the seller's own image bytes; **nothing is sent to a third party** for
the common case. Only genuinely AMBIGUOUS images fall back to the vision model, and
only then is the (seller's own) image content handed to the provider.

Used to auto-mark a profile's size-chart / measurement / care images as fixed
images (B3) so they are copied onto every new draft. Own-shop images only.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.pipeline.llm import LLMClient

SIZE_CHART = "size_chart"
ARTWORK = "artwork"
AMBIGUOUS = "ambiguous"

# Heuristic thresholds (tuned on a 256px working copy). A size chart is mostly a
# near-white ground with low-saturation ink (text + table rules); product artwork
# is either colourful or full-bleed/dark.
_WHITE_MIN = 0.55  # fraction of near-white pixels
_SAT_MAX = 45.0  # mean saturation (0..255)
_EDGE_MIN = 0.04  # fraction of strong edges (text strokes / table rules)
_DARK_LO, _DARK_HI = 0.008, 0.40  # ink coverage window (some text, not full-bleed)
_ART_SAT_MIN = 85.0  # clearly colourful -> artwork
_ART_WHITE_MAX = 0.30  # little/no white ground -> artwork (photo/full-bleed)


def local_size_chart_kind(data: bytes) -> str:
    """Classify image bytes locally: ``size_chart`` | ``artwork`` | ``ambiguous``."""
    from PIL import Image, ImageFilter, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return AMBIGUOUS

    img = img.convert("RGB")
    img.thumbnail((256, 256))
    width, height = img.size
    n = (width * height) or 1

    rgb = img.tobytes()  # flat r,g,b,r,g,b,...
    white = dark = 0
    for i in range(0, len(rgb), 3):
        r, g, b = rgb[i], rgb[i + 1], rgb[i + 2]
        if r >= 230 and g >= 230 and b >= 230:
            white += 1
        elif r <= 90 and g <= 90 and b <= 90:
            dark += 1
    white_frac = white / n
    dark_frac = dark / n

    mean_sat = sum(img.convert("HSV").getchannel("S").tobytes()) / n
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES).tobytes()
    edge_frac = sum(1 for v in edges if v >= 60) / n

    if (
        white_frac >= _WHITE_MIN
        and mean_sat <= _SAT_MAX
        and edge_frac >= _EDGE_MIN
        and _DARK_LO <= dark_frac <= _DARK_HI
    ):
        return SIZE_CHART
    if mean_sat >= _ART_SAT_MIN or white_frac <= _ART_WHITE_MAX:
        return ARTWORK
    return AMBIGUOUS


# --- Vision fallback (ambiguous cases only) ---------------------------------
IMAGE_KIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": [SIZE_CHART, ARTWORK]}},
    "required": ["kind"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You classify ONE Etsy listing image. Answer 'size_chart' if it is a size chart, "
    "measurement table, size guide, or care/washing-instruction graphic — an image "
    "made mostly of text, numbers, tables or diagrams rather than the product design. "
    "Answer 'artwork' if it shows the product itself or its printed design."
)


class VisionImageClassifier(Protocol):
    async def classify(self, data: bytes, media_type: str) -> str: ...


class AnthropicImageKindClassifier:
    """Fallback classifier for ambiguous images; sends image bytes (base64)."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def classify(self, data: bytes, media_type: str = "image/jpeg") -> str:
        encoded = base64.standard_b64encode(data).decode("ascii")
        blocks = [
            {"type": "text", "text": "Classify this listing image."},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": encoded},
            },
        ]
        result = await self._client.complete_json(
            system=_SYSTEM, content_blocks=blocks, schema=IMAGE_KIND_SCHEMA, max_tokens=64
        )
        kind = str(result.data.get("kind", ARTWORK))
        return kind if kind in (SIZE_CHART, ARTWORK) else ARTWORK


# fetch_bytes(url) -> (data, media_type) or None; vision(data, media_type) -> kind.
FetchBytes = Callable[[str], Awaitable[tuple[bytes, str] | None]]
VisionFallback = Callable[[bytes, str], Awaitable[str]]


async def classify_reference_images(
    images: list[dict[str, Any]],
    *,
    fetch_bytes: FetchBytes,
    heuristic: Callable[[bytes], str] = local_size_chart_kind,
    vision: VisionFallback | None = None,
    prior_kinds: dict[int, str] | None = None,
) -> list[int]:
    """Classify each non-primary image in place; return the size-chart image ids.

    The primary image (lowest rank) is the product artwork and is not classified.
    A previously-cached classification for the same image id is reused, so this
    isn't re-run. Each new image is fetched to our server and classified locally;
    only an ``ambiguous`` result falls back to ``vision`` (if provided).
    """
    prior = prior_kinds or {}
    if not images:
        return []
    primary_id = min(images, key=lambda i: (i.get("rank") or 1_000_000)).get("listing_image_id")

    charts: list[int] = []
    for image in images:
        image_id = image.get("listing_image_id")
        if image_id == primary_id:
            image["kind"] = ARTWORK
            continue
        if prior.get(image_id):
            image["kind"] = prior[image_id]
        else:
            fetched = await fetch_bytes(str(image["url"])) if image.get("url") else None
            if fetched is None:
                image["kind"] = ARTWORK
            else:
                data, media_type = fetched
                kind = heuristic(data)
                if kind == AMBIGUOUS:
                    kind = await vision(data, media_type) if vision is not None else ARTWORK
                image["kind"] = kind
        if image["kind"] == SIZE_CHART and image_id is not None:
            charts.append(image_id)
    return charts
