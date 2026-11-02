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


# --- SSH: what sshd really does (2 November 2026) ---------------------------------------------------


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    tool = tmp_path / name
    tool.write_text("#!/bin/bash\n" + body + "\n")
    tool.chmod(0o755)
    return tool


def _ops_run(tmp_path: Path, env_text: str = "APP_ENV=production\n", ops_text: str | None = None, **extra: str) -> subprocess.CompletedProcess:
    """Preflight with its own sshd, rclone and ops.env."""
    if shutil.which("bash") is None or not SCRIPT.exists():
        pytest.skip("no bash, or deploy/ is not here (the dev container mounts only backend/)")
    env_file = tmp_path / "production.env"
    env_file.write_text(env_text)
    env_file.chmod(0o600)
    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  api:\n    environment:\n      STORAGE_DIR: /data/storage\n")
    ops = tmp_path / "ops.env"
    if ops_text is not None:
        ops.write_text(ops_text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "ENV_FILE": str(env_file), "COMPOSE_FILE": str(compose),
        "OPS_ENV": str(ops), "CREDS_DIR": str(tmp_path / "creds"), "BACKUP_DIR": str(tmp_path),
        "SSHD_CONFIG": str(tmp_path / "sshd_config"), "SSHD_CONFIG_DIR": str(tmp_path / "sshd.d"), **extra,
    }
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=environment, timeout=60)


GOOD_SSHD = "echo passwordauthentication no; echo kbdinteractiveauthentication no; echo permitrootlogin no"


def test_ssh_is_checked_by_what_sshd_does_and_the_winning_file_is_named(tmp_path) -> None:
    # The server as it was: our file exists and says no, cloud-init's is read first and says yes.
    ssh_dir = tmp_path / "sshd.d"
    ssh_dir.mkdir()
    (ssh_dir / "50-cloud-init.conf").write_text("PasswordAuthentication yes\n")
    (ssh_dir / "60-listyro.conf").write_text("PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n")
    sshd = _stub(tmp_path, "sshd", "echo passwordauthentication yes; echo kbdinteractiveauthentication no; echo permitrootlogin no")
    out = _ops_run(tmp_path, SSHD_BIN=str(sshd)).stdout
    assert f"warn  ssh: passwordauthentication is yes, not no: {ssh_dir}/50-cloud-init.conf sets it" in out, out
    assert "ssh hardening in place" not in out
    assert "60-listyro.conf sets it" not in out
    assert "ssh: no passwords" not in out


def test_ssh_passes_only_when_all_three_are_no(tmp_path) -> None:
    sshd = _stub(tmp_path, "sshd", GOOD_SSHD)
    assert "ok    ssh: no passwords, no keyboard-interactive, no root login (sshd -T)" in _ops_run(tmp_path, SSHD_BIN=str(sshd)).stdout
    for wrong, line in (("kbdinteractiveauthentication yes", "kbdinteractiveauthentication is yes"), ("permitrootlogin prohibit-password", "permitrootlogin is prohibit-password")):
        key = wrong.split()[0]
        body = GOOD_SSHD.replace(f"{key} no", wrong)
        out = _ops_run(tmp_path, SSHD_BIN=str(_stub(tmp_path, "sshd", body))).stdout
        assert f"warn  ssh: {line}, not no: no file sets it" in out, out
        assert "ssh: no passwords" not in out


def test_ssh_that_cannot_be_read_says_so_and_never_passes(tmp_path) -> None:
    sshd = _stub(tmp_path, "sshd", "echo 'sshd: no hostkeys available -- exiting.' >&2; exit 1")
    out = _ops_run(tmp_path, SSHD_BIN=str(sshd)).stdout
    assert "the ssh settings could not be checked; run: sudo bash deploy/preflight.sh" in out, out
    assert "ssh: no passwords" not in out
    missing = _ops_run(tmp_path, SSHD_BIN=str(tmp_path / "no-such-sshd")).stdout
    assert "warn  sshd not found" in missing and "ssh: no passwords" not in missing


# --- rclone ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("version,warned", [("rclone v1.60.1-DEV", True), ("rclone v1.64.2", True), ("rclone v1.65.0", False), ("rclone v1.71.2", False), ("rclone v2.0.0", False)])
def test_rclone_older_than_165_warns_when_there_is_a_remote(tmp_path, version: str, warned: bool) -> None:
    (tmp_path / "bin").mkdir()
    _stub(tmp_path / "bin", "rclone", f"echo '{version}'; echo '- os/version: ubuntu 24.04'")
    out = _ops_run(tmp_path, ops_text="BACKUP_REMOTE=offsite:\n").stdout
    assert ("older than 1.65" in out) is warned, out
    assert ("ok    rclone " in out) is (not warned), out


def test_rclone_is_not_asked_about_without_a_remote_and_a_missing_one_is_said(tmp_path) -> None:
    (tmp_path / "bin").mkdir()
    _stub(tmp_path / "bin", "rclone", "echo 'rclone v1.60.1'")
    assert "older than 1.65" not in _ops_run(tmp_path, ops_text="HEALTHCHECK_URL=https://example.test/x\n").stdout
    (tmp_path / "bin" / "rclone").unlink()
    _stub(tmp_path / "bin", "rclone", "exit 127")
    out = _ops_run(tmp_path, ops_text="BACKUP_REMOTE=offsite:\n").stdout
    assert "BACKUP_REMOTE is set but rclone did not answer" in out, out


# --- The alert address in two files ----------------------------------------------------------------


def test_two_alert_addresses_warn_and_neither_is_printed(tmp_path) -> None:
    out = _ops_run(tmp_path, "APP_ENV=production\nALERT_WEBHOOK_URL=https://ntfy.example/new-secret\n", "ALERT_WEBHOOK_URL=https://ntfy.example/old-secret\n").stdout
    assert "warn  ALERT_WEBHOOK_URL differs between" in out, out
    assert "new-secret" not in out and "old-secret" not in out


def test_the_same_alert_address_passes(tmp_path) -> None:
    same = "ALERT_WEBHOOK_URL=https://ntfy.example/topic\n"
    out = _ops_run(tmp_path, "APP_ENV=production\n" + same, same).stdout
    assert "ok    ALERT_WEBHOOK_URL is the same in" in out and "differs" not in out, out
    # One missing is the louder warning that already exists, not this one.
    assert "differs" not in _ops_run(tmp_path, "APP_ENV=production\n", same).stdout


# --- provision.sh ----------------------------------------------------------------------------------


def _provision(tmp_path: Path, call: str, path: str | None = None) -> subprocess.CompletedProcess:
    script = ROOT / "deploy" / "provision.sh"
    if shutil.which("bash") is None or not script.exists():
        pytest.skip("no bash, or deploy/ is not here")
    environment = {"PATH": path or "/usr/bin:/bin", "HOME": str(tmp_path)}
    return subprocess.run(["bash", "-c", f'. "{script}"; {call}'], capture_output=True, text=True, env=environment, timeout=60)


def test_provision_turns_passwords_off_in_cloud_inits_file_and_for_every_reboot(tmp_path) -> None:
    ssh_dir = tmp_path / "sshd.d"
    cloud_dir = tmp_path / "cloud.d"
    ssh_dir.mkdir()
    cloud_dir.mkdir()
    (ssh_dir / "50-cloud-init.conf").write_text("# by cloud-init\n  passwordauthentication yes\nX11Forwarding no\n")
    out = _provision(tmp_path, f'close_cloud_passwords "{ssh_dir}" "{cloud_dir}"')
    assert out.returncode == 0, out.stderr
    assert (ssh_dir / "50-cloud-init.conf").read_text() == "# by cloud-init\nPasswordAuthentication no\nX11Forwarding no\n"
    assert "ssh_pwauth: false" in (cloud_dir / "99-listyro-ssh.cfg").read_text().splitlines()


def test_provision_writes_nothing_where_there_is_no_cloud_init(tmp_path) -> None:
    ssh_dir = tmp_path / "sshd.d"
    ssh_dir.mkdir()
    out = _provision(tmp_path, f'close_cloud_passwords "{ssh_dir}" "{tmp_path / "no-cloud"}"')
    assert out.returncode == 0, out.stderr
    assert list(ssh_dir.iterdir()) == [] and not (tmp_path / "no-cloud").exists()


@pytest.mark.parametrize("installer_exit,ok", [(0, True), (3, True), (1, False), (4, False)])
def test_provision_installs_rclone_from_rclone_org_and_up_to_date_is_not_a_failure(tmp_path, installer_exit: int, ok: bool) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Writes rclone's installer as a script that only exits; records where it was fetched from.
    _stub(bin_dir, "curl", f'echo "$@" >> "{tmp_path}/curl.log"; while [ $# -gt 0 ]; do [ "$1" = -o ] && {{ echo "exit {installer_exit}" > "$2"; }}; shift; done')
    _stub(bin_dir, "dpkg", "exit 0")  # apt's rclone is installed
    _stub(bin_dir, "apt-get", f'echo "$@" >> "{tmp_path}/apt.log"')
    out = _provision(tmp_path, 'install_rclone && echo INSTALLED', path=f"{bin_dir}:/usr/bin:/bin")
    assert ("INSTALLED" in out.stdout) is ok, out.stdout + out.stderr
    assert "https://rclone.org/install.sh" in (tmp_path / "curl.log").read_text()
    assert "remove -yq rclone" in (tmp_path / "apt.log").read_text()


def test_provision_no_longer_takes_rclone_from_apt_and_checks_what_sshd_does() -> None:
    script = ROOT / "deploy" / "provision.sh"
    if not script.exists():
        pytest.skip("deploy/ is not here")
    text = script.read_text(encoding="utf-8")
    install = next(line for line in text.splitlines() if line.startswith("apt-get install -yq ca-certificates"))
    assert "rclone" not in install and "unzip" in install  # rclone's installer needs unzip
    assert "install_rclone\n" in text.replace("\r\n", "\n")
    assert "close_cloud_passwords /etc/ssh/sshd_config.d /etc/cloud/cloud.cfg.d" in text
    assert "sshd -T | grep -qx 'passwordauthentication no'" in text


def test_the_checklist_says_what_was_done() -> None:
    checklist = ROOT / "docs" / "ops-checklist.md"
    if not checklist.exists():
        pytest.skip("docs/ is not here")
    text = checklist.read_text(encoding="utf-8")
    for needed in ("rclone cryptcheck /var/backups/listyro offsite:", "rclone rcat r2:listyro-backups/", "rclone lsf r2:listyro-backups", "rclone deletefile r2:listyro-backups/",
                   "`rclone lsd r2:` is **refused, by design**", "https://rclone.org/install.sh", "**both must change together**", "sudo sshd -T", "99-listyro-ssh.cfg"):
        assert needed in text, needed
    assert "sudo apt-get install -y rclone" not in text

