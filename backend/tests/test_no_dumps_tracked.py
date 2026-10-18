"""No database dump or data export is ever tracked in git.

A production pg_dump (tokens, shop and listing data) was once committed and had
to be removed from history. .gitignore keeps new ones out of ``git add``; this
fails if one is tracked anyway (``git add -f``, or a pattern dropped from
.gitignore). The tracked files come from ``git ls-files``, or, where git itself is
not installed (the test container, ``docker compose run --rm tests``), from git's
own index file, which that run mounts read-only.
"""

from __future__ import annotations

import fnmatch
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: The same patterns as .gitignore's "Database dumps and exports" block.
FORBIDDEN = ("*.sql", "*.dump", "*.sql.gz", "*.rdb", "backup_*", "acts-*.json")


def index_paths(data: bytes) -> list[str]:
    """The paths in a git index file (versions 2 and 3: every tracked file)."""
    if data[:4] != b"DIRC":
        raise ValueError("not a git index")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3):
        raise ValueError(f"git index version {version} is not read here")
    paths: list[str] = []
    at = 12
    for _ in range(count):
        flags = struct.unpack(">H", data[at + 60 : at + 62])[0]
        fixed = 62 + (2 if version == 3 and flags & 0x4000 else 0)
        end = data.index(b"\0", at + fixed)
        paths.append(data[at + fixed : end].decode("utf-8"))
        at += (end - at + 8) // 8 * 8  # each entry is NUL-padded to a multiple of 8
    return paths


def _tracked() -> list[str]:
    if not (ROOT / ".git").exists():
        pytest.skip("no git checkout here: run the suite with `docker compose run --rm tests`")
    if shutil.which("git") is None:
        return index_paths((ROOT / ".git" / "index").read_bytes())
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def forbidden(paths: list[str]) -> list[str]:
    return [p for p in paths if any(fnmatch.fnmatch(Path(p).name.lower(), pat) for pat in FORBIDDEN)]


def test_the_patterns_catch_what_they_should_and_nothing_else() -> None:
    assert forbidden(["backup_2026-09-01.sql", "x/db.dump", "a.sql.gz", "docs/acts-1.json", "deploy/backup_db.sh",
                      "backend/dump.rdb"]) == [
        "backup_2026-09-01.sql", "x/db.dump", "a.sql.gz", "docs/acts-1.json", "deploy/backup_db.sh",
        "backend/dump.rdb"]
    assert forbidden(["backend/alembic/versions/0049_drop_ad_spend.py", "facts.json", "sqlite.py"]) == []


def test_no_dump_or_export_is_tracked() -> None:
    found = forbidden(_tracked())
    assert not found, f"database dumps or exports are tracked in git; remove them: {found}"


def test_gitignore_still_lists_every_pattern() -> None:
    lines = {line.strip() for line in (ROOT / ".gitignore").read_text().splitlines()}
    assert set(FORBIDDEN) <= lines
