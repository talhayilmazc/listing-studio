"""deploy/preflight.sh refuses a production that would send nothing to Etsy.

ETSY_CALLS_BLOCKED is the development switch (app/etsy/guard.py). Left on in
production the app looks healthy while no draft, publish or sync reaches Etsy,
so preflight fails on it; a warning could scroll past.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "preflight.sh"
MESSAGE = "ETSY_CALLS_BLOCKED is on in production: no drafts, publishes or syncs will reach Etsy"


def _run(tmp_path: Path, env_text: str, compose_text: str = "services:\n  api:\n    environment:\n      STORAGE_DIR: /data/storage\n") -> subprocess.CompletedProcess:
    if shutil.which("bash") is None or not SCRIPT.exists():
        pytest.skip("no bash, or deploy/ is not here (the dev container mounts only backend/)")
    env_file = tmp_path / "production.env"
    env_file.write_text(env_text)
    env_file.chmod(0o600)
    compose = tmp_path / "compose.yml"
    compose.write_text(compose_text)
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ENV_FILE": str(env_file), "COMPOSE_FILE": str(compose),
        "OPS_ENV": str(tmp_path / "none.env"), "CREDS_DIR": str(tmp_path / "creds"), "BACKUP_DIR": str(tmp_path),
    }
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=environment, timeout=60)


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", '"true"'])
def test_the_switch_on_in_production_fails_preflight(tmp_path, value: str) -> None:
    out = _run(tmp_path, f"APP_ENV=production\nETSY_CALLS_BLOCKED={value}\n")
    assert f"  FAIL  {MESSAGE}" in out.stdout, out.stdout + out.stderr
    assert out.returncode == 1 and "Not ready." in out.stdout
    assert f"warn  {MESSAGE}" not in out.stdout  # a failure, not a warning


def test_set_in_the_compose_file_it_fails_too(tmp_path) -> None:
    # Compose's environment outranks .env, so .env saying nothing is not enough.
    compose = 'services:\n  worker:\n    environment:\n      ETSY_CALLS_BLOCKED: "true"\n'
    out = _run(tmp_path, "APP_ENV=production\n", compose)
    assert f"  FAIL  {MESSAGE}" in out.stdout, out.stdout + out.stderr


@pytest.mark.parametrize("env_text", ["APP_ENV=production\n", "APP_ENV=production\nETSY_CALLS_BLOCKED=false\n", "APP_ENV=production\nETSY_CALLS_BLOCKED=\n"])
def test_off_or_unset_it_passes_that_check(tmp_path, env_text: str) -> None:
    out = _run(tmp_path, env_text)
    assert MESSAGE not in out.stdout and "  ok    Etsy calls are not blocked" in out.stdout, out.stdout + out.stderr


def test_the_real_production_files_do_not_turn_it_on() -> None:
    if not SCRIPT.exists():
        pytest.skip("deploy/ is not here")
    assert "ETSY_CALLS_BLOCKED" not in (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    example = (ROOT / "deploy" / "env.production.example").read_text(encoding="utf-8")
    assert "ETSY_CALLS_BLOCKED=true" not in example.replace(" ", "")


# --- Etsy's limits under Commercial Access (approved 19 October 2026) ------------------------------


@pytest.mark.parametrize("env_text", ["APP_ENV=production\n", "APP_ENV=production\nGLOBAL_DAILY_LIMIT=100000\n"])
def test_the_commercial_access_daily_ceiling_passes(tmp_path, env_text: str) -> None:
    out = _run(tmp_path, env_text)
    assert "  ok    GLOBAL_DAILY_LIMIT is Etsy's ceiling (100000)" in out.stdout, out.stdout + out.stderr


def test_a_daily_limit_above_etsys_ceiling_warns_and_does_not_fail(tmp_path) -> None:
    out = _run(tmp_path, "APP_ENV=production\nGLOBAL_DAILY_LIMIT=150000\n")
    assert "warn  GLOBAL_DAILY_LIMIT is 150000, above Etsy's 100000/day" in out.stdout, out.stdout + out.stderr
    assert "FAIL  GLOBAL_DAILY_LIMIT" not in out.stdout
    # The old Personal-access figure is a warning too (the margin belongs in GLOBAL_PAUSE_PERCENT), not a pass.
    old = _run(tmp_path, "APP_ENV=production\nGLOBAL_DAILY_LIMIT=5000\n")
    assert "warn  GLOBAL_DAILY_LIMIT is 5000, not 100000" in old.stdout, old.stdout + old.stderr
    assert "FAIL  GLOBAL_DAILY_LIMIT must be a number" in _run(tmp_path, "APP_ENV=production\nGLOBAL_DAILY_LIMIT=lots\n").stdout


def test_requests_per_second_above_etsys_150_warns(tmp_path) -> None:
    fine = _run(tmp_path, "APP_ENV=production\nETSY_REQUESTS_PER_SECOND=10\n")
    assert "  ok    ETSY_REQUESTS_PER_SECOND is 10 (Etsy allows 150)" in fine.stdout, fine.stdout + fine.stderr
    high = _run(tmp_path, "APP_ENV=production\nETSY_REQUESTS_PER_SECOND=200\n")
    assert "warn  ETSY_REQUESTS_PER_SECOND above Etsy's 150/s" in high.stdout, high.stdout + high.stderr
