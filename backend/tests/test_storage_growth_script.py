"""deploy/storage-growth.sh: disk-check.sh alerts when uploads grow more than
1 GB in a day, at most once a day."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "storage-growth.sh"
GB = 1024**3
HOUR = 3600
T0 = 1_791_000_000  # a fixed moment


def _check(state: Path, size: int, at: int, limit: str | None = None) -> str:
    if shutil.which("bash") is None:
        pytest.skip("no bash here")
    env = {"PATH": "/usr/bin:/bin"} | ({"STORAGE_GROWTH_ALERT_GB": limit} if limit else {})
    run = subprocess.run(
        ["bash", "-c", f'. "{SCRIPT}"; storage_growth {size} "{state}" {at}'],
        capture_output=True, text=True, env=env, check=True,
    )
    return run.stdout


def test_growth_over_a_gigabyte_in_a_day_alerts_once(tmp_path) -> None:
    state = tmp_path / "size.log"
    assert _check(state, 9 * GB, T0) == ""  # the first reading: nothing to compare with
    assert _check(state, int(9.5 * GB), T0 + 12 * HOUR) == ""  # not a day yet
    alert = _check(state, int(10.4 * GB), T0 + 24 * HOUR)
    assert alert.startswith("storage grew 1.4 GB in the last day (now 10.4 GB; alert above 1 GB a day)")
    assert _check(state, int(10.6 * GB), T0 + 25 * HOUR) == ""  # once a day


def test_slower_growth_or_a_higher_limit_does_not_alert(tmp_path) -> None:
    state = tmp_path / "size.log"
    _check(state, 9 * GB, T0)
    assert _check(state, int(9.8 * GB), T0 + 24 * HOUR) == ""
    other = tmp_path / "other.log"
    _check(other, 9 * GB, T0)
    assert _check(other, int(10.5 * GB), T0 + 24 * HOUR, limit="2") == ""


def test_readings_older_than_three_days_are_dropped(tmp_path) -> None:
    state = tmp_path / "size.log"
    for n in range(6):
        _check(state, 9 * GB, T0 + n * 24 * HOUR)
    assert len(state.read_text().splitlines()) == 4  # now and the three days before
