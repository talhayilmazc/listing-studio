"""What an MP4 or MOV file says about itself, read from its own boxes (feature/listing-video).

Duration, frame size, whether it has a sound track and its video codec, from the file's ``moov`` box: a few
kilobytes are read wherever the box sits (a phone puts it at the end), never the video itself, so a 100 MB file
costs nothing. No decoder and no other program: the file is a seller's upload and is only parsed, never run.
Anything that is not such a file raises :class:`NotAVideo`.

Etsy also takes FLV, AVI and MPEG (pipeline/video_rules.py): those have no such boxes, so ``read_path`` asks the
frame reader (ffmpeg, a separate program, the one that makes the poster) to describe them instead.
"""

from __future__ import annotations

import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

#: A ``moov`` box larger than this is not a listing video's (a 15 second clip's is a few kilobytes).
MAX_MOOV_BYTES = 16 * 1024 * 1024
_CONTAINERS = {b"trak", b"mdia", b"minf", b"stbl"}
_TOP_LEVEL = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot", b"uuid", b"meta", b"moof", b"sidx", b"styp"}


class NotAVideo(ValueError):
    pass


@dataclass
class Probe:
    seconds: float
    width: int | None
    height: int | None
    has_audio: bool
    codec: str | None


def _boxes(data: bytes, start: int = 0, end: int | None = None):  # noqa: ANN202
    end = len(data) if end is None else end
    at = start
    while at + 8 <= end:
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        head = 8
        if size == 1:
            if at + 16 > end:
                return
            size = struct.unpack(">Q", data[at + 8:at + 16])[0]
            head = 16
        elif size == 0:
            size = end - at
        if size < head or at + size > end:
            return
        yield kind, at + head, at + size
        at += size


def _moov(fh: BinaryIO, total: int) -> bytes:
    at = 0
    seen = 0
    while at + 8 <= total:
        fh.seek(at)
        header = fh.read(16)
        if len(header) < 8:
            break
        size, kind = struct.unpack(">I4s", header[:8])
        head = 8
        if size == 1:
            if len(header) < 16:
                break
            size = struct.unpack(">Q", header[8:16])[0]
            head = 16
        elif size == 0:
            size = total - at
        if size < head or (seen == 0 and kind not in _TOP_LEVEL):
            raise NotAVideo("not an MP4 or MOV file")
        seen += 1
        if kind == b"moov":
            if size - head > MAX_MOOV_BYTES:
                raise NotAVideo("the file's index is too large")
            fh.seek(at + head)
            body = fh.read(size - head)
            if len(body) != size - head:
                raise NotAVideo("the file ends before its index does")
            return body
        at += size
        if seen > 10_000:
            break
    raise NotAVideo("the file has no index (moov)")


def _handler(data: bytes, start: int, end: int) -> bytes | None:
    for kind, a, b in _boxes(data, start, end):
        if kind == b"hdlr" and b - a >= 12:
            return data[a + 8:a + 12]
    return None


def _codec(data: bytes, start: int, end: int) -> str | None:
    for kind, a, b in _boxes(data, start, end):
        if kind in _CONTAINERS:
            found = _codec(data, a, b)
            if found:
                return found
        elif kind == b"stsd" and b - a >= 16:
            return data[a + 12:a + 16].decode("latin-1").strip() or None
    return None


def read(fh: BinaryIO, total: int) -> Probe:
    """The video in ``fh`` (``total`` bytes long)."""
    moov = _moov(fh, total)
    seconds = 0.0
    width = height = None
    has_audio = False
    codec = None
    for kind, a, b in _boxes(moov):
        if kind == b"mvhd" and b - a >= 20:
            version = moov[a]
            if version == 1 and b - a >= 32:
                timescale, duration = struct.unpack(">IQ", moov[a + 20:a + 32])
            else:
                timescale, duration = struct.unpack(">II", moov[a + 12:a + 20])
            if timescale:
                seconds = duration / timescale
        elif kind == b"trak":
            handler = None
            size = None
            for inner, c, d in _boxes(moov, a, b):
                if inner == b"tkhd" and d - c >= 8:
                    # The frame size is the box's last two numbers (16.16 fixed point), whatever its version.
                    w, h = struct.unpack(">II", moov[d - 8:d])
                    size = (w >> 16, h >> 16)
                elif inner == b"mdia":
                    handler = _handler(moov, c, d)
                    if handler == b"vide" and codec is None:
                        codec = _codec(moov, c, d)
            if handler == b"vide" and size and size[0] and size[1] and width is None:
                width, height = size
            elif handler == b"soun":
                has_audio = True
    if seconds <= 0 or codec is None:
        raise NotAVideo("the file has no video in it")
    return Probe(seconds=seconds, width=width, height=height, has_audio=has_audio, codec=codec)


_DURATION = re.compile(r"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #[^\n]*?Video: (\w+)[^\n]*?[ ,](\d{2,5})x(\d{2,5})[ ,\n\[]")
_AUDIO = re.compile(r"Stream #[^\n]*?Audio:")
FFMPEG_SECONDS = 20


def read_other(path: Path) -> Probe:
    """A container that is not MP4 or MOV (FLV, AVI, MPEG): what the frame reader says of it. It is asked to
    describe the file and nothing else (``-i`` with no output), at low priority and with a time limit."""
    from app.pipeline import listing_videos

    exe = listing_videos.ffmpeg()
    if exe is None:
        raise NotAVideo("no reader for this kind of file here")
    try:
        done = subprocess.run([exe, "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True, timeout=FFMPEG_SECONDS,
                              stdin=subprocess.DEVNULL, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        raise NotAVideo("the file could not be read") from exc
    said = done.stderr.decode("utf-8", "replace")
    duration, video = _DURATION.search(said), _VIDEO.search(said)
    if duration is None or video is None:
        raise NotAVideo("the file has no video in it")
    hours, minutes, seconds = duration.groups()
    return Probe(seconds=int(hours) * 3600 + int(minutes) * 60 + float(seconds), width=int(video.group(2)),
                 height=int(video.group(3)), has_audio=_AUDIO.search(said) is not None, codec=video.group(1))


def read_path(path: Path) -> Probe:
    """The video at ``path``: from its own boxes when it is MP4, MOV or 3GP; else through the frame reader."""
    try:
        with open(path, "rb") as fh:
            return read(fh, path.stat().st_size)
    except NotAVideo:
        return read_other(path)
