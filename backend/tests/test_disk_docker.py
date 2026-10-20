"""Docker's real disk use in Admin -> Usage, and "days until full" with the deploy's reclaim.

* deploy/docker-usage.sh counts images (with their containerd snapshots), containers, build
  cache and logs the way ``docker system df`` does, never the volumes; measuring Docker's root
  directory with ``du -x`` (before) saw 43 MB while the images under /var/lib/containerd took
  3.3 GB and the build cache 6 GB;
* the build cache is space a deploy gives back (deploy/update.sh clears it below 12 GB free):
  the forecast counts it as free and its own growth as passing, and says so; the plain figure
  is given too;
* the 12 GB in the app and in deploy/reclaim.sh are the same number.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import disk

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GB = 10**9


def _bash(script: str) -> str:
    if shutil.which("bash") is None or not (DEPLOY / "docker-usage.sh").exists():
        pytest.skip("bash or deploy/ is not here")
    done = subprocess.run(["bash", "-c", f". {DEPLOY / 'docker-usage.sh'}; {script}"], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_docker_is_measured_the_way_docker_system_df_counts_it() -> None:
    df = "Images\\t3.31GB\\nContainers\\t52.4MB\\nLocal Volumes\\t9.8GB\\nBuild Cache\\t6.012GB\\n"
    parts = json.loads(_bash(f'printf "{df}" | docker_df_json'))
    assert parts == {"images": 3_310_000_000, "containers": 52_400_000, "build_cache": 6_012_000_000}  # no volumes
    total = int(_bash(f"docker_total '{json.dumps(parts, separators=(',', ':'))}' 1048576"))
    assert total == 3_310_000_000 + 52_400_000 + 6_012_000_000 + 1_048_576
    assert json.loads(_bash(f"docker_parts_json '{json.dumps(parts, separators=(',', ':'))}' 7"))["logs"] == 7
    for text, want in (("0B", 0), ("12.5kB", 12_500), ("512MB", 512_000_000), ("1.2TB", 1_200_000_000_000)):
        assert int(_bash(f"docker_size_bytes {text}")) == want


def test_disk_check_no_longer_measures_dockers_root_directory() -> None:
    script = (DEPLOY / "disk-check.sh").read_text(encoding="utf-8") if (DEPLOY / "disk-check.sh").exists() else None
    if script is None:
        pytest.skip("deploy/ is not here")
    assert "docker system df" in script and "docker_df_json" in script
    assert "du -sxb \"$root\"" not in script  # the 43 MB measurement


def test_the_forecast_counts_the_build_cache_as_space_a_deploy_gives_back() -> None:
    readings = [
        {"day": "2026-10-13", "used": 20 * GB, "free": 10 * GB, "storage": 4 * GB, "build_cache": 1 * GB},
        {"day": "2026-10-20", "used": 27 * GB, "free": 3 * GB, "storage": 5 * GB, "build_cache": 6 * GB},
    ]
    f = disk.forecast(readings, 3 * GB, build_cache=6 * GB)
    # 7 GB more used in 7 days, 5 GB of it build cache: 2/7 GB a day stays.
    assert f.per_day == pytest.approx(2 * GB / 7)
    assert f.days_left == pytest.approx((3 + 6) * GB / (2 * GB / 7))  # 31.5 days, not 3
    assert f.days_left_without_reclaim == pytest.approx(3 * GB / (7 * GB / 7))
    assert f.reclaimable == 6 * GB
    # Readings from before the cache was measured: the plain rate.
    old = [{k: v for k, v in r.items() if k != "build_cache"} for r in readings]
    assert disk.forecast(old, 3 * GB).days_left == pytest.approx(3.0)


def test_the_reclaim_threshold_is_the_deploys() -> None:
    reclaim = DEPLOY / "reclaim.sh"
    if not reclaim.exists():
        pytest.skip("deploy/ is not here")
    m = re.search(r"DEPLOY_RECLAIM_BELOW_GB:-(\d+)", reclaim.read_text(encoding="utf-8"))
    assert m and int(m.group(1)) == disk.RECLAIM_BELOW_GB == 12
