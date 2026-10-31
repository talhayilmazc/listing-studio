"""What part 5 left open: nightly jobs apart, remaking mockups in the fair queue, test stacks that
leave nothing behind."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fakeredis import FakeAsyncRedis

from app.workers import fair
from app.workers.settings import WorkerSettings

ROOT = Path(__file__).resolve().parents[2]


def _nightly() -> dict[str, tuple[int, int]]:
    out = {}
    for job in WorkerSettings.cron_jobs:
        if job.hour is not None:
            out[job.name] = (next(iter(job.hour)), next(iter(job.minute)))
    return out


def test_a_run_of_the_suite_cannot_cross_midnight() -> None:
    """Below Python as well: SQLite's CURRENT_TIMESTAMP is on the same clock."""
    import os
    import sqlite3
    from datetime import datetime, timezone

    if "FAKETIME" not in os.environ:
        pytest.skip("run through docker compose run --rm tests")
    day = os.environ["FAKETIME"].lstrip("@")[:10]
    assert datetime.now(timezone.utc).date().isoformat() == day
    assert sqlite3.connect(":memory:").execute("SELECT date(CURRENT_TIMESTAMP)").fetchone()[0] == day


def test_no_two_nightly_jobs_start_in_the_same_minute() -> None:
    jobs = _nightly()
    assert len(jobs) >= 7
    assert len(set(jobs.values())) == len(jobs), jobs


def test_a_nightly_job_never_starts_with_an_every_few_minutes_one() -> None:
    frequent = set()
    for job in WorkerSettings.cron_jobs:
        if job.hour is None and isinstance(job.minute, set):
            frequent |= job.minute
    for name, (_hour, minute) in _nightly().items():
        assert minute not in frequent, f"{name} starts in minute {minute} with a job that runs every hour"


@pytest.mark.parametrize(("module", "function", "seed"), [
    ("app.workers.listing_stats", "read_all_listing_stats", "read_listing_stats"),
])
def test_a_shop_job_of_the_night_does_not_start_with_all_the_others(module: str, function: str, seed: str) -> None:
    source = (ROOT / "backend" / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")
    assert f'"{seed}", str(cid), _defer_by=spread_seconds(n, len(' in source


def test_sales_and_ledger_jobs_are_spread_too() -> None:
    sales = (ROOT / "backend/app/workers/sales.py").read_text(encoding="utf-8")
    ledger = (ROOT / "backend/app/workers/ledger.py").read_text(encoding="utf-8")
    assert "_defer_by=spread_seconds(n, len(due))" in sales
    assert "_defer_by=spread_seconds(n, len(due))" in ledger


async def test_remaking_mockups_waits_its_turn_behind_another_accounts_job() -> None:
    redis = FakeAsyncRedis()
    went: list[str] = []

    async def hand_over(function: str, ref: str, *, _job_id: str) -> None:
        went.append(f"{function}:{ref}")

    from app.api.deps import submit_heavy

    class Enqueuer:
        async def redis(self):  # noqa: ANN201
            return redis

        enqueue = staticmethod(hand_over)

    q = Enqueuer()
    for n, ref in enumerate(("a1", "a2", "a3", "a4")):
        await submit_heavy(q, "account-a", "rerender_mockups", ref, job_id=f"j{n}")
    await submit_heavy(q, "account-b", "generate_mockups", "b1", job_id="b")
    assert went == ["rerender_mockups:a1", "generate_mockups:b1"]  # one per account: A's other three wait
    await fair.after_heavy({"redis": redis, "enqueue": hand_over}, "rerender_mockups", "a1", 2.0)
    assert went[-1] == "rerender_mockups:a2"


def test_the_route_and_the_worker_use_the_fair_queue() -> None:
    route = (ROOT / "backend/app/api/mockup_edits.py").read_text(encoding="utf-8")
    assert 'submit_heavy(enqueuer, run.tenant_id, "rerender_mockups"' in route
    assert 'enqueuer.enqueue("rerender_mockups"' not in route
    worker = (ROOT / "backend/app/workers/mockups.py").read_text(encoding="utf-8")
    assert 'async with heavy(ctx, "rerender_mockups", run_id) as turn:' in worker


def test_the_shop_connection_stack_leaves_no_volume_behind() -> None:
    path = ROOT / "docker-compose.oauth-e2e.yml"
    if not path.exists():
        pytest.skip("the compose files are not here")
    services = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]
    assert "/data" in services["redis"]["tmpfs"]
    assert "/var/lib/postgresql/data" in services["postgres"]["tmpfs"]
    for name, service in services.items():
        for mount in service.get("volumes", []):
            assert str(mount).startswith("./"), f"{name} mounts {mount}: only files of this repository"
    stop = (ROOT / "frontend/e2e/oauth.global.ts").read_text(encoding="utf-8")
    assert '"down", "--volumes"' in stop
