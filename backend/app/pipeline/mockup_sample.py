"""The sample design the Templates page previews a template with.

Drawn here from fixed shapes (no seller's design, no outside image): a coloured
one for light garments and a white one for dark garments, where an underbase
matters most.
"""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw

SIZE = (900, 900)


@lru_cache(maxsize=2)
def sample(light_ink: bool) -> bytes:
    w, h = SIZE
    img = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    main = (250, 250, 248, 255) if light_ink else (214, 74, 42, 255)
    accent = (250, 214, 92, 255) if light_ink else (38, 92, 166, 255)
    draw.ellipse([0.22 * w, 0.06 * h, 0.78 * w, 0.62 * h], fill=main)
    draw.polygon([(0.04 * w, 0.96 * h), (0.38 * w, 0.42 * h), (0.70 * w, 0.96 * h)], fill=accent)
    draw.polygon([(0.30 * w, 0.96 * h), (0.64 * w, 0.48 * h), (0.96 * w, 0.96 * h)], fill=main)
    draw.rectangle([0.08 * w, 0.975 * h, 0.92 * w, 1.0 * h - 1], fill=accent)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
