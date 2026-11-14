"""Parse an untrusted PSD in a child process with a time and memory limit (feature/psd-templates).

A PSD is a seller's file and may be malformed or hostile: deep layer trees, huge declared sizes, decompression
bombs, bugs in the parser. Parsing happens in ``python -m app.pipeline.psd_sandbox <path>``, a separate process
with an address-space limit (``RLIMIT_AS``) and a CPU-time limit (``RLIMIT_CPU``), plus a wall-clock timeout
here. Whatever goes wrong there (an exception, running out of memory, being killed) fails only that file:
``ParseFailed`` with a plain reason. The child reads one file and prints JSON; it touches no database, no
network and nothing but the given path.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from typing import Any

from app.pipeline.psd_read import LayerLine, SmartObject, Summary

#: Defaults for inspecting one file; the converter passes its own.
MEMORY_MB = 2048
SECONDS = 180


class ParseFailed(Exception):
    """The file could not be read; the message says why in plain words."""


def _limits(memory_mb: int, seconds: int):  # noqa: ANN202 - a preexec function
    def apply() -> None:
        import resource

        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds + 5))
        os.nice(10)  # never ahead of drafts, publishing or page requests

    return apply


#: The child's exit codes.
OUT_OF_MEMORY = 3
UNREADABLE = 2
#: The application's root (``app`` is importable from here) for the child.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: One thread for numpy's BLAS in the child: under an address-space limit OpenBLAS's worker threads can fail to
#: allocate and the process stalls instead of failing (seen while testing; the wall-clock timeout would catch it,
#: three minutes later).
_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
MEMORY_MESSAGE = "it needs more memory than a template may use"
DAMAGED = "it is not a Photoshop file, or it is damaged"


def run_child(module: str, args: list[str], *, memory_mb: int, seconds: int) -> bytes:
    """Run ``python -m <module> <args>`` under the limits; its stdout, or ``ParseFailed`` with a plain reason."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", module, *args], capture_output=True, timeout=seconds + 10,
            preexec_fn=_limits(memory_mb, seconds), cwd=_ROOT, env={**os.environ, **_ENV}, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ParseFailed(f"reading it took longer than {max(1, seconds // 60)} minutes") from None
    if done.returncode == 0:
        return done.stdout
    reason = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
    if done.returncode == OUT_OF_MEMORY or done.returncode in (-9, -11):  # killed: the memory limit
        raise ParseFailed(MEMORY_MESSAGE)
    if done.returncode == -24:  # SIGXCPU: the CPU-time limit
        raise ParseFailed(f"reading it took longer than {max(1, seconds // 60)} minutes")
    raise ParseFailed(reason[0][:160] if done.returncode == UNREADABLE and reason[0] else DAMAGED)


def inspect_file(path: str, *, memory_mb: int = MEMORY_MB, seconds: int = SECONDS) -> Summary:
    if not os.path.isfile(path):
        raise ParseFailed("the file is not there")
    out = run_child("app.pipeline.psd_sandbox", [path], memory_mb=memory_mb, seconds=seconds)
    return _summary(json.loads(out))


def out_of_memory(exc: BaseException) -> bool:
    """Whether running out of memory is behind ``exc`` (numpy's import, for one, wraps it in an ImportError)."""
    seen = 0
    node: BaseException | None = exc
    while node is not None and seen < 10:
        text = str(node).lower()
        if isinstance(node, MemoryError) or any(w in text for w in ("allocate", "out of memory", "failed to map")):
            return True
        node = node.__cause__ or node.__context__
        seen += 1
    return False


def child_main(work) -> int:  # noqa: ANN001 - a callable that does the child's work
    """The child's body: run ``work``, map what goes wrong to an exit code and one plain line on stderr."""
    try:
        work()
        return 0
    except BaseException as exc:  # noqa: BLE001 - the child reports, the parent decides
        if out_of_memory(exc):
            return OUT_OF_MEMORY
        sys.stderr.write(DAMAGED + "\n")
        return UNREADABLE


def _summary(data: dict[str, Any]) -> Summary:
    layers = [LayerLine(**x) for x in data.pop("layers")]
    sos = []
    for x in data.pop("smart_objects"):
        x["quad"] = [tuple(p) for p in x["quad"]] if x["quad"] else None
        x["embedded_size"] = tuple(x["embedded_size"]) if x["embedded_size"] else None
        sos.append(SmartObject(**x))
    return Summary(**data, layers=layers, smart_objects=sos)


def _main(path: str) -> int:
    def work() -> None:
        from app.pipeline.psd_read import read

        sys.stdout.write(json.dumps(dataclasses.asdict(read(path))))

    return child_main(work)


if __name__ == "__main__":  # pragma: no cover - the child process
    raise SystemExit(_main(sys.argv[1]))
