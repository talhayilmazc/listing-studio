"""deploy/backup.sh: uploads are not archived by default (they are working
copies); only a failed or unreadable database dump is a failure. Docker is a
stub here: nothing real is dumped."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FAKE_DOCKER = """#!/usr/bin/env bash
case "$*" in
  *pg_dump*) printf 'PGDMP fake dump' ;;
  *pg_restore*) cat > /dev/null; exit "${FAKE_RESTORE_RC:-0}" ;;
  *"du -sm"*) printf '100\\t/data/storage\\n' ;;
  *tar*) printf 'archive' | gzip ;;
  *) exit 0 ;;
esac
"""


def _run(tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    if shutil.which("bash") is None:
        pytest.skip("no bash here")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin", "OPS_ENV": str(tmp_path / "none.env"),
        "BACKUP_DIR": str(tmp_path / "backups"), "HOME": str(tmp_path), **env,
    }
    return subprocess.run(["bash", str(ROOT / "deploy" / "backup.sh")], capture_output=True, text=True, env=environment)


def test_by_default_the_database_is_dumped_and_uploads_are_not_archived(tmp_path) -> None:
    old = tmp_path / "backups" / "storage"
    old.mkdir(parents=True)
    (old / "storage-20261001T000000Z.tar.gz").write_bytes(b"x" * 1000)
    run = _run(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "not archived (STORAGE_BACKUP=off)" in run.stdout and "backup ok" in run.stdout
    dumps = list((tmp_path / "backups" / "db").glob("db-*.dump"))
    assert len(dumps) == 1 and dumps[0].read_bytes() == b"PGDMP fake dump"
    # The archive from when it was on is removed: it was the biggest thing on the disk.
    assert list(old.iterdir()) == [] and "removed:" in run.stdout


def test_an_unreadable_dump_fails_the_backup(tmp_path) -> None:
    run = _run(tmp_path, FAKE_RESTORE_RC="1")
    assert run.returncode == 1 and "backup FAILED" in run.stderr
    assert list((tmp_path / "backups" / "db").glob("db-*.dump")) == []


def test_with_archives_on_a_skipped_archive_exits_2_and_the_dump_is_kept(tmp_path) -> None:
    run = _run(tmp_path, STORAGE_BACKUP="on", BACKUP_FREE_MARGIN_MB=str(10**9))
    assert run.returncode == 2 and "storage archive skipped" in run.stdout
    assert len(list((tmp_path / "backups" / "db").glob("db-*.dump"))) == 1


def test_update_goes_on_when_only_the_storage_archive_was_skipped() -> None:
    text = (ROOT / "deploy" / "update.sh").read_text()
    assert 'as_root bash deploy/backup.sh || backup_rc=$?' in text
    assert '[ "$backup_rc" -eq 2 ]' in text and 'elif [ "$backup_rc" -ne 0 ]; then' in text
    # The dump itself is still checked after it.
    assert text.index("backup_rc=$?") < text.index("no database dump was written")
    assert os.access(ROOT / "deploy" / "backup.sh", os.R_OK)
