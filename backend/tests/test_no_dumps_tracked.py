"""No database dump or data export is ever tracked in git.

A production pg_dump (tokens, shop and listing data) was once committed and had
to be removed from history. .gitignore keeps new ones out of ``git add``; this
fails if one is tracked anyway (``git add -f``, or a pattern dropped from
.gitignore). Skipped where there is no git checkout (the container).
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: The same patterns as .gitignore's "Database dumps and exports" block.
FORBIDDEN = ("*.sql", "*.dump", "*.sql.gz", "backup_*", "acts-*.json")


def _tracked() -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("no git checkout here")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def forbidden(paths: list[str]) -> list[str]:
    return [p for p in paths if any(fnmatch.fnmatch(Path(p).name.lower(), pat) for pat in FORBIDDEN)]


def test_the_patterns_catch_what_they_should_and_nothing_else() -> None:
    assert forbidden(["backup_2026-09-01.sql", "x/db.dump", "a.sql.gz", "docs/acts-1.json", "deploy/backup_db.sh"]) == [
        "backup_2026-09-01.sql", "x/db.dump", "a.sql.gz", "docs/acts-1.json", "deploy/backup_db.sh"]
    assert forbidden(["backend/alembic/versions/0049_drop_ad_spend.py", "facts.json", "sqlite.py"]) == []


def test_no_dump_or_export_is_tracked() -> None:
    found = forbidden(_tracked())
    assert not found, f"database dumps or exports are tracked in git; remove them: {found}"


def test_gitignore_still_lists_every_pattern() -> None:
    lines = {line.strip() for line in (ROOT / ".gitignore").read_text().splitlines()}
    assert set(FORBIDDEN) <= lines
