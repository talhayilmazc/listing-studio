"""Object storage abstraction.

Production uses S3-compatible storage (Cloudflare R2); tests and local runs use
:class:`LocalStorage` on the filesystem. The R2 backend lands with the pieces
that need it -- this step only requires a place to put originals and processed
derivatives.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

#: A cached preview, stored beside its source as ``<key>.v<N>.w<width>[-ratio].jpg``.
_PREVIEW = re.compile(r"\.v\d+\.w\d+")


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def delete_with_derivatives(self, key: str) -> int: ...

    def measure_with_derivatives(self, key: str) -> tuple[int, int]: ...

    def usage(self) -> dict[str, int]: ...


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

    def delete_prefix(self, prefix: str) -> None:
        """Delete everything stored under ``prefix`` (a folder of keys).

        Refuses an empty prefix or one that would leave the storage root, so a
        bad key can never remove more than one batch's files.
        """
        import shutil

        prefix = prefix.strip("/")
        if not prefix or ".." in prefix.split("/"):
            raise ValueError("refusing to delete outside a batch's own folder")
        base = self._base.resolve()
        target = (self._base / prefix).resolve()
        if base not in target.parents:
            raise ValueError("refusing to delete outside the storage root")
        shutil.rmtree(target, ignore_errors=True)

    def _family(self, key: str) -> list[Path]:
        """One stored file and what was derived from it: the previews cached
        beside it as ``<key>.<suffix>`` (api/batches.py). Nothing else in the
        folder; a missing file is an empty list."""
        key = key.strip("/")
        if not key or ".." in key.split("/"):
            raise ValueError("refusing to reach outside the storage root")
        base = self._base.resolve()
        target = (self._base / key).resolve()
        if base not in target.parents:
            raise ValueError("refusing to reach outside the storage root")
        if not target.parent.is_dir():
            return []
        return [
            path
            for path in target.parent.iterdir()
            if path.is_file() and (path.name == target.name or path.name.startswith(target.name + "."))
        ]

    def measure_with_derivatives(self, key: str) -> tuple[int, int]:
        """How many files a delete would remove, and their size in bytes."""
        sizes = [path.stat().st_size for path in self._family(key)]
        return len(sizes), sum(sizes)

    def delete_with_derivatives(self, key: str) -> int:
        """Delete one stored file and the previews made from it. Returns the
        bytes freed; a missing file is not an error."""
        freed = 0
        for path in self._family(key):
            try:
                size = path.stat().st_size
                path.unlink()
                freed += size
            except FileNotFoundError:
                pass  # gone between the listing and the delete: nothing to free
        return freed

    def usage(self) -> dict[str, int]:
        """What is stored, by kind, in bytes and files: ``uploads`` are the
        originals as sellers sent them; ``derivatives`` are everything made from
        them (processed copies, previews, kept thumbnails)."""
        out = {"uploads_bytes": 0, "uploads_files": 0, "derivatives_bytes": 0, "derivatives_files": 0}
        if not self._base.is_dir():
            return out
        for folder, _, names in os.walk(self._base):
            original = os.path.basename(folder) == "original"
            for name in names:
                try:
                    size = os.stat(os.path.join(folder, name)).st_size
                except OSError:
                    continue  # deleted while we were counting
                kind = "uploads" if original and not _PREVIEW.search(name) else "derivatives"
                out[f"{kind}_bytes"] += size
                out[f"{kind}_files"] += 1
        return out
