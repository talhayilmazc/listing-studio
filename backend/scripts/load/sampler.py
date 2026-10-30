"""Every 5 s, each app process's memory and CPU, from /proc, into Redis (``load:proc``), for the driver."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import redis

TICK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


def _role(cmd: str) -> str | None:
    if "scripts.load.sampler" in cmd:
        return None
    if "arq" in cmd:
        return "worker"
    if "uvicorn" in cmd or "multiprocessing" in cmd:
        return "api"
    return None


def main() -> None:
    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    while True:
        sample: dict = {"at": time.time(), "api_rss": 0, "worker_rss": 0, "api_cpu": 0.0, "worker_cpu": 0.0}
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
                role = _role(cmd)
                if role is None:
                    continue
                fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                sample[f"{role}_cpu"] += (int(fields[11]) + int(fields[12])) / TICK
                sample[f"{role}_rss"] += int(fields[21]) * PAGE
            except OSError:
                continue
        try:
            client.rpush("load:proc", json.dumps(sample))
        except redis.RedisError:
            pass
        time.sleep(5)


if __name__ == "__main__":
    main()
