"""What an MP4 or MOV file says about itself, read from its own boxes (feature/listing-video).

Duration, frame size, whether it has a sound track and its video codec, from the file's ``moov`` box: a few
kilobytes are read wherever the box sits (a phone puts it at the end), never the video itself, so a 100 MB file
costs nothing. No decoder and no other program: the file is a seller's upload and is only parsed, never run.
Anything that is not such a file raises :class:`NotAVideo`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
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
