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
OS_MB = 350  # at least this much stays for the OS, Docker itself and the file cache

#: The most each used in the load test, 100 sellers at once (load-output/report-*.txt): a ceiling
#: under this would kill a container in normal work. Postgres's figure includes its file cache,
#: which the kernel reclaims at the ceiling, so its ceiling may sit under it.
MEASURED_MB = {"api": 1597, "worker": 2136, "frontend": 99, "redis": 11}


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


def test_together_they_leave_room_for_the_os() -> None:
    total = sum(_mb(s["mem_limit"]) for s in _services().values())
    assert total <= VM_MB - OS_MB, f"the limits add up to {total} MB of a {VM_MB} MB VM"


@pytest.mark.parametrize("name", sorted(MEASURED_MB))
def test_no_ceiling_is_under_what_the_load_test_used(name: str) -> None:
    assert _mb(_services()[name]["mem_limit"]) > MEASURED_MB[name]
