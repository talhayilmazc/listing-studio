"""Reading an uploaded ZIP of designs (docs/duzeltmeler-v6.md §F).

Nothing is extracted to disk. Each entry is read into memory, checked, and
handed to the same admission as a single uploaded file, so the rules that decide
what an image *is* (its bytes, never its name) are the ones already in force.

The archive is untrusted input, so:

* **Zip slip:** a path that is absolute, has a drive letter, or climbs with
  ``..`` is refused and counted; since nothing is written to disk it could not
  escape anyway, but it never becomes a listing group either.
* **Size:** the archive itself, the number of files, each file and the total
  unpacked bytes are all capped. The total is counted from bytes actually read,
  not from the sizes the archive declares, and a file is read no further than
  one byte past its limit, so a ZIP bomb stops at the cap.
* **Nesting:** an archive inside the archive is counted and skipped, never opened.
* Encrypted entries and symbolic links are skipped.

The folder structure is kept: a file's folder is its listing group, as with a
folder upload, and files at the root are one group.
"""

from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.pipeline.uploads import UnsupportedUpload, UploadTooLarge, sniff_format

# Operating-system clutter a ZIP picks up; ignored without being counted.
_CLUTTER_DIRS = {"__MACOSX"}
_CLUTTER_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
_ARCHIVE_EXTS = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz")
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


@dataclass
class ArchiveFile:
    filename: str  # the file's own name, as a folder upload reports it
    group_key: str | None  # its folder inside the archive; None at the root
    data: bytes


@dataclass
class ArchiveContents:
    files: list[ArchiveFile] = field(default_factory=list)
    unsupported: int = 0  # not an image we accept (by content), or encrypted
    unsafe: int = 0  # a path that would leave the archive, or a link
    nested: int = 0  # an archive inside the archive, not opened
    too_large: list[str] = field(default_factory=list)  # over the per-file limit


def _name(info: zipfile.ZipInfo) -> str:
    """The entry's name, recovering UTF-8 names stored without the UTF-8 flag
    (common for archives made on Windows), so Turkish folder names survive."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def safe_parts(name: str) -> list[str] | None:
    """The path's components, or None when it is not safely inside the archive."""
    if "\x00" in name:
        return None
    name = name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


def _is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def read_archive(
    data: bytes,
    *,
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
) -> ArchiveContents:
    """The archive's image files with their groups, and what was skipped.

    Raises :class:`UnsupportedUpload` when this is not a ZIP, and
    :class:`UploadTooLarge` when it holds too many files or unpacks to too much.
    """
    if not data.startswith(_ZIP_MAGIC):
        raise UnsupportedUpload("not a ZIP archive")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError) as exc:
        raise UnsupportedUpload("the ZIP archive could not be read") from exc

    out = ArchiveContents()
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > max_files:
            raise UploadTooLarge(
                f"a ZIP may hold at most {max_files} files; split it into several ZIPs"
            )
        declared = sum(i.file_size for i in infos)
        if declared > max_total_bytes:
            raise UploadTooLarge(
                f"a ZIP may unpack to at most {max_total_bytes // (1024 * 1024)} MB"
            )

        total = 0
        for info in sorted(infos, key=lambda i: _name(i).lower()):
            name = _name(info)
            parts = safe_parts(name)
            if parts is None or _is_link(info):
                out.unsafe += 1
                continue
            base = parts[-1]
            if parts[0] in _CLUTTER_DIRS or base.lower() in _CLUTTER_FILES or base.startswith("._"):
                continue
            if base.lower().endswith(_ARCHIVE_EXTS):
                out.nested += 1
                continue
            if info.flag_bits & 0x1:  # encrypted
                out.unsupported += 1
                continue
            try:
                with archive.open(info) as handle:
                    body = handle.read(max_file_bytes + 1)
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError):
                out.unsupported += 1  # damaged, or a compression method we lack
                continue
            total += len(body)
            if total > max_total_bytes:
                raise UploadTooLarge(
                    f"a ZIP may unpack to at most {max_total_bytes // (1024 * 1024)} MB"
                )
            if body.startswith(_ZIP_MAGIC):
                out.nested += 1  # an archive under another name
                continue
            if len(body) > max_file_bytes:
                out.too_large.append(base)
                continue
            if sniff_format(body) is None:
                out.unsupported += 1
                continue
            group = str(PurePosixPath(*parts[:-1])) if len(parts) > 1 else None
            out.files.append(ArchiveFile(filename=base, group_key=group, data=body))
    return out
