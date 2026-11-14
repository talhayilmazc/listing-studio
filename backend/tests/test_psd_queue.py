"""A PSD conversion never holds up listing writing (feature/psd-templates, step 5).

The conversion runs in a child process at the lowest CPU priority, started from a thread (workers/psd.py), so the
worker's event loop keeps running jobs. Measured here: a batch written alone, then the same while a large PSD
(4000 x 5000, like the sellers' samples) converts. The numbers are printed for docs/psd-findings.md.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from PIL import Image

from app.pipeline.psd_sandbox import run_child
from app.workers import writing
from tests.psd_builder import Pixel, Smart, build
from tests.support import fake_response
from tests.test_publish_api import ctx  # noqa: F401  (fixture)
from tests.test_regenerate import VISION, _content_reply, llm  # noqa: F401
from tests.test_writing_job import _job_ctx, _two_groups, _written


async def _write(ctx, llm) -> float:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    for _ in range(2):
        llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    resp = await ctx["client"].post(f"/api/batches/{batch}/write", json={"profile_id": str(profile)})
    assert resp.status_code == 200, resp.text
    started = time.perf_counter()
    assert await writing.write_batch_listings(_job_ctx(ctx), str(batch)) == "done"
    took = time.perf_counter() - started
    assert await _written(ctx, batch) == 2
    return took


async def test_a_conversion_does_not_hold_up_writing(ctx, llm, tmp_path: Path) -> None:  # noqa: F811
    psd = str(tmp_path / "large.psd")
    photo = Image.radial_gradient("L").resize((4000, 5000)).convert("RGBA")
    build(psd, (4000, 5000), [Pixel(photo, name="Photo"),
                              Smart(Image.new("RGBA", (1300, 1600), (200, 40, 40, 255)), [(1300, 1300), (2700, 1300),
                                                                                         (2700, 3000), (1300, 3000)])])
    out = tmp_path / "out"
    out.mkdir()

    alone = await _write(ctx, llm)

    async def convert() -> float:
        started = time.perf_counter()
        await asyncio.to_thread(run_child, "app.pipeline.psd_convert", [psd, str(out)], memory_mb=1200, seconds=170)
        return time.perf_counter() - started

    conversion = asyncio.create_task(convert())
    await asyncio.sleep(0.3)  # the child is parsing
    beside = await _write(ctx, llm)
    still_converting = not conversion.done()
    converted = await conversion
    print(f"\nwriting alone {alone:.2f}s · beside a conversion {beside:.2f}s · conversion {converted:.1f}s")
    assert still_converting  # the batch was written while the PSD was still converting
    assert beside < alone * 2 + 0.5, (alone, beside)
