"""Object storage abstraction.

Production uses S3-compatible storage (Cloudflare R2); tests and local runs use
:class:`LocalStorage` on the filesystem. The R2 backend lands with the pieces
that need it -- this step only requires a place to put originals and processed
derivatives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class LocalStorage:
    """Filesystem-backed storage rooted at ``base_dir``. Keys map to paths."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    def _path(self, key: str) -> Path:
        return self._base / key

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
