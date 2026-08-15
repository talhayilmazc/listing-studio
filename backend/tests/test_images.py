"""Image processing tests (resize / format / watermark).

Runs against every available backend: always Pillow, plus pyvips when libvips is
installed (e.g. inside the Docker image).
"""

import io
from typing import Callable

import pytest
from PIL import Image

from app.pipeline.images import (
    ImageProcessingError,
    ImageProcessor,
    PillowBackend,
    ProcessingSpec,
)

_BACKENDS = [PillowBackend()]
try:  # pragma: no cover - depends on libvips availability
    import pyvips  # noqa: F401

    from app.pipeline.images import PyvipsBackend

    _BACKENDS.append(PyvipsBackend())
except Exception:
    pass


@pytest.fixture(params=_BACKENDS, ids=lambda b: b.name)
def processor(request: pytest.FixtureRequest) -> ImageProcessor:
    return ImageProcessor(backend=request.param)


def test_resize_downscales_preserving_aspect(
    processor: ImageProcessor, make_image: Callable[..., bytes]
) -> None:
    data = make_image(3000, 2000)  # 3:2
    result = processor.process(data, ProcessingSpec(max_width=2000, max_height=2000))
    assert result.width <= 2000 and result.height <= 2000
    assert max(result.width, result.height) == 2000
    assert abs((result.width / result.height) - 1.5) < 0.02


def test_does_not_upscale_small_images(
    processor: ImageProcessor, make_image: Callable[..., bytes]
) -> None:
    data = make_image(100, 100)
    result = processor.process(data, ProcessingSpec(max_width=2000, max_height=2000))
    assert (result.width, result.height) == (100, 100)


def test_format_conversion_png_to_jpeg(
    processor: ImageProcessor, make_image: Callable[..., bytes]
) -> None:
    data = make_image(400, 300, fmt="PNG")
    result = processor.process(data, ProcessingSpec(target_format="JPEG"))
    assert result.format == "JPEG"
    assert result.mime_type == "image/jpeg"
    # Bytes really are JPEG.
    assert Image.open(io.BytesIO(result.data)).format == "JPEG"


def test_watermark_changes_output(
    processor: ImageProcessor, make_image: Callable[..., bytes]
) -> None:
    data = make_image(600, 400)
    plain = processor.process(data, ProcessingSpec(target_format="PNG"))
    marked = processor.process(
        data, ProcessingSpec(target_format="PNG", watermark_text="© Shop")
    )
    assert marked.data != plain.data
    # Still a valid, same-sized image.
    reopened = Image.open(io.BytesIO(marked.data))
    assert (reopened.width, reopened.height) == (plain.width, plain.height)


def test_corrupt_input_raises(processor: ImageProcessor) -> None:
    with pytest.raises(ImageProcessingError):
        processor.process(b"this is not an image", ProcessingSpec())


def test_unsupported_format_raises(
    processor: ImageProcessor, make_image: Callable[..., bytes]
) -> None:
    with pytest.raises(ImageProcessingError):
        processor.process(make_image(50, 50), ProcessingSpec(target_format="TIFF"))
