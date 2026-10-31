"""Each production container has a memory ceiling, and together they fit the VM (docs/load-test.md).

Without them one container that grows takes the whole server down, not just itself: the kernel kills
whatever it picks. With them, it kills inside that container, which restarts by itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"

VM_MB = 5800
#: Stays out of every limit but cloudflared's: cloudflared itself, the OS, Docker and the file cache.
LEFT_FOR_THE_OS_MB = 700
HEADROOM = 1.15

#: The most each used in the load test, 100 sellers at once (load-output/report-*.txt).
PEAK_MB = {"api": 1668, "worker": 2136, "postgres": 812, "frontend": 104, "redis": 11}
#: What a limit really has to hold, because the kernel can't give it back. The API's RSS counts
#: shared pages once for each of its three processes (1,528 MB private + shared once). Postgres's
#: peak is mostly file cache, which goes first at its limit (334 MB its own + 128 MB shared buffers).
CANNOT_BE_GIVEN_BACK_MB = {**PEAK_MB, "api": 1575, "postgres": 462}


def _services() -> dict:
    if not COMPOSE.exists():
        pytest.skip("the deploy files are not here")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _mb(limit: str) -> int:
    assert limit.endswith("m"), f"write the limit in megabytes: {limit}"
    return int(limit[:-1])


def test_every_container_has_a_ceiling_and_comes_back_after_hitting_it() -> None:
    for name, service in _services().items():
        assert "mem_limit" in service, f"{name} has no memory limit: it could take the VM down"
        assert service.get("restart") == "unless-stopped", f"{name} would stay down after being killed"


def test_the_limits_are_written_in_one_place() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count("mem_limit: *memory-") == len(_services())
    assert "mem_limit: 1" not in text.split("services:", 1)[1]


def test_together_they_leave_room_for_cloudflared_and_the_os() -> None:
    total = sum(_mb(s["mem_limit"]) for name, s in _services().items() if name != "cloudflared")
    assert total <= VM_MB - LEFT_FOR_THE_OS_MB, f"the limits add up to {total} MB of a {VM_MB} MB VM"


@pytest.mark.parametrize("name", sorted(CANNOT_BE_GIVEN_BACK_MB))
def test_every_ceiling_has_headroom_over_what_it_must_hold(name: str) -> None:
    assert _mb(_services()[name]["mem_limit"]) >= CANNOT_BE_GIVEN_BACK_MB[name] * HEADROOM


def test_redis_evicts_before_its_ceiling_kills_it() -> None:
    redis = _services()["redis"]
    assert "--maxmemory 96mb" in str(redis["command"])
    assert 96 < _mb(redis["mem_limit"])


def test_fifteen_percent_over_the_plain_peaks_does_not_fit_this_vm() -> None:
    """Why the table in the compose file has two columns: docs/load-test.md gives 8 GB limits."""
    assert sum(PEAK_MB.values()) * HEADROOM + LEFT_FOR_THE_OS_MB > VM_MB
