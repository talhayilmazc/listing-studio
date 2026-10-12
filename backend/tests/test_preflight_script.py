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
