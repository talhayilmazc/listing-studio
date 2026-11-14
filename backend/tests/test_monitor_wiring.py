"""What the monitor reads is really written: the middleware counts, the worker beats, the host script
reports, the commands work (docs/monitoring.md)."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.main import create_app
from app.monitor import signals
from app.workers.settings import WorkerSettings, scheduler_beat

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


@pytest.fixture(autouse=True)
def _clean():
    signals.reset()
    yield
    signals.reset()


async def _counted(redis: FakeAsyncRedis, method: str, path: str, *, raises: bool = False) -> None:
    app = create_app()
    app.dependency_overrides[deps.get_redis] = lambda: redis

    @app.get("/api/_ok")
    async def ok() -> dict:
        return {}

    @app.get("/api/_boom")
    async def boom() -> dict:
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.request(method, path)


async def test_the_middleware_counts_answers_and_server_errors() -> None:
    redis = FakeAsyncRedis()
    await _counted(redis, "GET", "/api/_ok")
    await _counted(redis, "GET", "/api/_boom")
    import time

    now = time.time()
    await signals.flush(redis, "api", now=now)
    assert await signals.errors_over(redis, 10, now) == (2, 1)
    slowest = await signals.slowest(redis, [time.strftime("%Y-%m-%d", time.gmtime(now))])
    assert {row[0] for row in slowest} == {"GET /api/_ok", "GET /api/_boom"}  # the method too: an upload is not a list


async def test_a_scanners_404s_are_not_endpoints() -> None:
    redis = FakeAsyncRedis()
    await _counted(redis, "GET", f"/api/{uuid.uuid4().hex}")
    import time

    await signals.flush(redis, "api")
    assert await signals.slowest(redis, [time.strftime("%Y-%m-%d", time.gmtime())]) == []


async def test_the_scheduler_says_it_is_running() -> None:
    redis = FakeAsyncRedis()
    await scheduler_beat({"redis": redis})
    assert await redis.get(signals.SCHEDULER_BEAT)
    assert any(job.name == "cron:scheduler_beat" for job in WorkerSettings.cron_jobs)


async def test_the_worker_says_it_is_running_without_a_job_slot(monkeypatch) -> None:
    import asyncio

    from app.workers import settings as worker_settings

    redis = FakeAsyncRedis()
    task = asyncio.create_task(worker_settings._worker_beat(redis))
    await asyncio.sleep(0.01)
    task.cancel()
    assert await redis.get(signals.WORKER_BEAT)


def test_the_check_is_scheduled_and_upkeep() -> None:
    from app.etsy.categories import _BY_JOB
    from app.workers import gate

    assert any(job.name == "cron:preflight_scheduled" for job in WorkerSettings.cron_jobs)
    assert "preflight_shop" in gate.UPKEEP and gate.JOB_COST["preflight_shop"] >= 4 and "preflight_shop" in _BY_JOB


def test_every_container_alert_rule_reads_what_the_host_script_writes() -> None:
    from app.monitor import rules

    source = (DEPLOY / "watch.sh").read_text(encoding="utf-8") if DEPLOY.exists() else None
    if source is None:
        pytest.skip("deploy/ is not here")
    assert f"SETEX {rules.CONTAINERS} 900" in source
    assert "deploy/watch.sh" in (DEPLOY / "provision.sh").read_text(encoding="utf-8")
    assert "deploy/watch.sh" in (DEPLOY / "preflight.sh").read_text(encoding="utf-8")


def _bash(script: str) -> str:
    if shutil.which("bash") is None or not (DEPLOY / "watch.sh").exists():
        pytest.skip("bash or deploy/ is not here")
    done = subprocess.run(["bash", "-c", f". {DEPLOY / 'watch.sh'}; {script}"], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_the_host_script_reports_memory_restarts_and_kills_as_the_rules_read_them() -> None:
    lines = ("listyro-worker-1\\tabc\\t1.5GiB / 2.402GiB\\t3\\ttrue\\n"
             "listyro-api-1\\tdef\\t512.5MiB / 1.772GiB\\t0\\tfalse\\n")
    data = json.loads(_bash(f'printf "{lines}" | watch_json 1000'))
    assert data["at"] == 1000
    worker, api = data["containers"]
    assert (worker["name"], worker["id"], worker["mem_mb"], worker["limit_mb"], worker["restarts"], worker["oom"]) == (
        "worker", "abc", 1536, 2460, 3, True)
    assert (api["name"], api["mem_mb"], api["oom"]) == ("api", 512, False)


def test_a_dead_api_is_said_by_the_host_after_three_minutes_hourly_and_when_it_is_back(tmp_path) -> None:
    state = tmp_path / "down"
    assert _bash(f"watch_down 0 1001000 {state}") == ""  # the first bad minute: it may be a restart
    assert _bash(f"watch_down 0 1001179 {state}") == ""
    said = _bash(f"watch_down 0 1001180 {state}")
    assert "API not healthy for 3 minutes" in said and said.startswith("[Listyro]")
    assert _bash(f"watch_down 0 1001240 {state}") == ""  # not every minute
    assert "API not healthy for 63 minutes" in _bash(f"watch_down 0 1004780 {state}")
    assert _bash(f"watch_down 1 1004840 {state}") == "[Listyro] Resolved: the API is healthy again."
    assert not state.exists()
    assert _bash(f"watch_down 1 1004900 {state}") == ""


def test_a_short_stumble_of_the_api_says_nothing(tmp_path) -> None:
    state = tmp_path / "down"
    assert _bash(f"watch_down 0 1001000 {state}") == ""
    assert _bash(f"watch_down 1 1001060 {state}") == ""


def test_the_commands_exist_and_alert_test_says_when_nothing_is_set(test_settings, capsys) -> None:
    from app.ops.health_digest import run_alert_test

    test_settings.alert_webhook_url = ""
    assert run_alert_test() == 1
    assert "not set" in capsys.readouterr().out


async def test_alert_test_sends_one_line(test_settings, monkeypatch, capsys) -> None:
    from app.core import alerts
    from app.ops import health_digest

    sent: list[str] = []

    async def record(text: str) -> bool:
        sent.append(text)
        return True

    test_settings.alert_webhook_url = "http://alerts.test/topic"
    monkeypatch.setattr(alerts, "send", record)
    import asyncio

    await asyncio.to_thread(health_digest.run_alert_test)
    assert len(sent) == 1 and sent[0].startswith("[Listyro] Test alert")


def test_the_loop_needs_production_or_the_setting(test_settings) -> None:
    assert test_settings.app_env != "production" and test_settings.monitor_enabled is False
    main = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8")
    assert 'settings.app_env == "production" or settings.monitor_enabled' in main
