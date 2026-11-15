"""How long 20 designs x 5 templates take to make (feature/mockups-editor B7; docs/mockups.md). Not part of the
normal run: ``MOCKUP_SPEED=1 docker compose run --rm tests -s tests/test_mockup_speed.py``. Synthetic photos and
designs only. Prints the times and the process's memory peak; asserts nothing about speed (machines differ).

It uses only what existed before the editor work, so the same file measures the code before and after.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, _new_template, _photo, mw, world  # noqa: F401  (fixtures)

pytestmark = pytest.mark.skipif(not os.environ.get("MOCKUP_SPEED"), reason="timing only: MOCKUP_SPEED=1")

DESIGNS, TEMPLATES = 20, 5


def _vm(name: str) -> int:
    for line in open("/proc/self/status"):
        if line.startswith(name):
            return int(line.split()[1]) // 1024
    return 0


async def _timed_run(mw, batch, template_ids) -> tuple[float, object]:  # noqa: ANN001, F811
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": template_ids})
    assert res.status_code == 202, res.text
    started = time.perf_counter()
    async with mw["sm"]() as s:
        done = await work.run(s, mw["storage"], uuid.UUID(res.json()["id"]))
    return time.perf_counter() - started, done


async def test_twenty_designs_on_five_templates(mw) -> None:  # noqa: F811
    colours = [(238, 238, 236), (26, 26, 28), (30, 40, 80), (200, 60, 60), (120, 140, 90)]
    templates = []
    for n, colour in enumerate(colours):
        t = await _new_template(mw, f"Tee {n}")
        await _photo(mw, t["id"], f"Colour {n}", colour, size=(1700, 2000))
        templates.append(t["id"])
    batch = await _new_batch(mw)
    for n in range(DESIGNS):
        res = await mw["a"].post(f"/api/batches/{batch}/designs",
                                 files={"file": (f"BR{1000 + n}.png", samples.design("transparent", size=(3000, 3600)), "image/png")})
        assert res.status_code == 201, res.text
    open("/proc/self/clear_refs", "w").write("5")
    base = _vm("VmRSS:")
    first, done = await _timed_run(mw, batch, templates)
    peak = _vm("VmHWM:") - base
    assert done.status == "succeeded" and done.done == DESIGNS * TEMPLATES, (done.status, done.done, done.error)
    second, done2 = await _timed_run(mw, batch, templates)
    print(f"\n{DESIGNS} designs x {TEMPLATES} templates: first run {first:.1f} s ({first / (DESIGNS * TEMPLATES) * 1000:.0f} ms a mockup), "
          f"the same run again {second:.1f} s, reused {getattr(done2, 'reused', 'n/a')}; memory peak +{peak} MB over {base} MB")
    # One design moved on one photo, then "Re-run only the changed ones" (B5; after the editor work only).
    if hasattr(done2, "reused"):
        from sqlalchemy import select

        from app.db.models import MockupOutput

        async with mw["sm"]() as s:
            one = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == done2.id))).scalars().first()
        await mw["a"].put("/api/mockup-adjustments", json={"design_id": str(one.design_id), "photo_id": str(one.photo_id),
                                                            "area": 0, "data": {"dx": 0.05}})
        res = await mw["a"].post(f"/api/mockup-runs/{done2.id}/rerun-changed")
        assert res.status_code == 202, res.text
        pairs = [(uuid.UUID(r["design_id"]), uuid.UUID(r["photo_id"])) for r in res.json()["refreshing"]]
        started = time.perf_counter()
        async with mw["sm"]() as s:
            await work.refresh_turn(s, mw["storage"], done2.id, pairs, mw["redis"])
        took = time.perf_counter() - started
        print(f"one design moved, Re-run only the changed ones: {took:.1f} s for {len(pairs)} mockups asked, 1 made again")
