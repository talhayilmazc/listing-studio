"""The worker's memory with three heavy jobs at once, with and without the memory budget (fix/worker-memory-budget).

    docker compose run --rm --entrypoint python tests scripts/heavy_peak.py

LOCAL ONLY, synthetic data only, no database, no network. Run in its own container: the container's cgroup
(``/sys/fs/cgroup/memory.current``, sampled every 50 ms) is what the kernel holds against the worker's limit,
and it counts the PSD conversion's child process too. The real work, as the worker does it:

- a PSD conversion: a synthetic 6000 x 7000 mockup with a smart object, through ``psd_sandbox.run_child``;
- a mockup run: 6 templates x 10 designs of 4000 px (``build_maps`` + ``render_many``);
- an upload's preparation: a 249 MP design PNG (``ImageProcessor.process``).

The mockup analysis models are loaded first, as in a worker that has prepared a template (they stay): that is
the base. "together" starts the three at once; "budget" lets each start only when its reservation fits
(``memory_budget.reserve``, a waiting job looks again every half second).
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CGROUP = "/sys/fs/cgroup/memory.current"


def current_mb() -> float:
    with open(CGROUP) as fh:
        return int(fh.read()) / 1048576


def anon_mb() -> float:
    """Memory the processes hold (not the page cache of files read and written, which the kernel takes back
    before it kills anything)."""
    with open(CGROUP.replace("memory.current", "memory.stat")) as fh:
        for line in fh:
            if line.startswith("anon "):
                return int(line.split()[1]) / 1048576
    return 0.0


class Sampler:
    def __init__(self) -> None:
        self.peak = 0.0
        self.anon = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, current_mb())
            self.anon = max(self.anon, anon_mb())
            time.sleep(0.05)

    def __enter__(self) -> "Sampler":
        self.peak, self.anon = current_mb(), anon_mb()
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


def make_inputs(folder: str) -> dict:
    from PIL import Image, ImageDraw

    from app.pipeline import mockups
    from tests import mockup_samples as samples
    from tests.psd_builder import Pixel, Smart, build

    Image.MAX_IMAGE_PIXELS = None
    psd = os.path.join(folder, "big.psd")
    photo = Image.radial_gradient("L").resize((6000, 7000)).convert("RGBA")
    build(psd, (6000, 7000), [Pixel(photo, name="Photo"),
                              Smart(Image.new("RGBA", (2000, 2400), (200, 40, 40, 255)),
                                    [(2000, 2000), (4000, 2000), (4000, 4400), (2000, 4400)])])
    design = Image.new("RGBA", (15000, 16600), (0, 0, 0, 0))
    draw = ImageDraw.Draw(design)
    for i in range(40):
        draw.ellipse((i * 300, i * 330, i * 300 + 4000, i * 330 + 3000), fill=(i * 6, 120, 200 - i * 4, 255))
    buf = io.BytesIO()
    design.save(buf, "PNG", compress_level=3)
    del design, draw, photo
    blanks = [mockups.prepare_blank(samples.person("hair", size=(1600, 2000))[0], "default", "default", "Tee"),
              mockups.prepare_blank(samples.wrinkled(size=(1700, 2000)), "default", "default", "Tee 2")]
    designs = [samples.design(size=(4000, 4500)) for _ in range(10)]
    return {"psd": psd, "out": folder, "upload": buf.getvalue(), "blanks": blanks, "designs": designs}


def psd_job(inputs: dict) -> None:
    from app.pipeline import psd_sandbox
    from app.workers.psd import MEMORY_MB, SECONDS

    out = tempfile.mkdtemp(dir=inputs["out"])
    psd_sandbox.run_child("app.pipeline.psd_convert", [inputs["psd"], out, "-"], memory_mb=MEMORY_MB, seconds=SECONDS)


def mockup_job(inputs: dict) -> None:
    from app.pipeline import mockups

    designs = [mockups.load_design(d) for d in inputs["designs"]]
    for blank in inputs["blanks"] * 3:
        area = blank.print_area
        maps = mockups.build_maps(blank.image, blank.mask, blank.occluder, [area])
        for d in designs:
            mockups.render_many(maps, [(d, area)], mockups.Settings(area))
        del maps


def upload_job(inputs: dict) -> None:
    from app.pipeline.images import ImageProcessor, ProcessingSpec

    ImageProcessor().process(inputs["upload"], ProcessingSpec())


async def scenario(inputs: dict, gated: bool) -> tuple[float, float, float]:
    from fakeredis import FakeAsyncRedis

    from app.workers import memory_budget as budget

    redis = FakeAsyncRedis()
    jobs = [("convert_psd", budget.PSD_MB, psd_job), ("generate_mockups", budget.MOCKUP_RUN_MB, mockup_job),
            ("process_upload", budget.upload_mb(15000, 16600), upload_job)]

    async def one(name: str, mb: int, work) -> None:  # noqa: ANN001
        while True:
            async with budget.reserve(redis if gated else None, name, mb) as fits:
                if fits:
                    await asyncio.to_thread(work, inputs)
                    return
            await asyncio.sleep(0.5)

    started = time.perf_counter()
    with Sampler() as sampler:
        await asyncio.gather(*(one(*job) for job in jobs))
    return sampler.peak, sampler.anon, time.perf_counter() - started


def main() -> int:
    if not os.path.exists(CGROUP):
        print("no cgroup v2 memory.current here: run it in its own container")
        return 2
    folder = tempfile.mkdtemp(prefix="heavy-")
    inputs = make_inputs(folder)
    import gc

    from app.workers.memory_budget import give_back

    give_back()  # what making the inputs freed: not the worker's
    time.sleep(1)
    base, base_anon = current_mb(), anon_mb()
    print(f"base (models loaded, inputs in memory): {base:,.0f} MB, of it held by the processes {base_anon:,.0f} MB", flush=True)
    if "--alone" in sys.argv:
        for name, work in (("upload 249 MP", upload_job), ("mockup run", mockup_job), ("PSD conversion", psd_job)):
            before = anon_mb()
            with Sampler() as sampler:
                work(inputs)
            gc.collect()
            time.sleep(1)
            kept = anon_mb() - before
            if "--trim" in sys.argv:
                from app.workers.memory_budget import give_back

                give_back()
                time.sleep(1)
            print(f"alone {name}: peak +{sampler.anon - before:,.0f} MB over what was held before it; "
                  f"held after it {kept:+,.0f} MB, after giving back {anon_mb() - before:+,.0f} MB", flush=True)
        return 0
    for gated in (True, False):
        give_back()
        time.sleep(1)
        start_anon = anon_mb()
        peak, anon, took = asyncio.run(scenario(inputs, gated))
        label = "budget  " if gated else "together"
        print(f"{label}: held by the processes {anon:,.0f} MB (+{anon - start_anon:,.0f} over the {start_anon:,.0f} held "
              f"when it started); with page cache {peak:,.0f} MB; {took:.1f} s", flush=True)
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
