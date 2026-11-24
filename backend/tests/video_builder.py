"""Synthetic MP4 / MOV files for the listing-video tests: the boxes a real file has, and no pictures.

``mp4(seconds=8)`` is a few hundred bytes that say "8 seconds, 1080 x 1920, H.264" exactly as a phone's file
does; nothing in it can be played. Nobody's video: made here, byte by byte.
"""

from __future__ import annotations

import struct


def box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def _mvhd(seconds: float, timescale: int, version: int) -> bytes:
    duration = int(round(seconds * timescale))
    if version == 1:
        head = struct.pack(">B3xQQIQ", 1, 0, 0, timescale, duration)
    else:
        head = struct.pack(">B3xIIII", 0, 0, 0, timescale, duration)
    return box(b"mvhd", head + b"\x00" * 80)


def _trak(handler: bytes, codec: bytes, width: int, height: int) -> bytes:
    tkhd = box(b"tkhd", b"\x00" * 76 + struct.pack(">II", width << 16, height << 16))
    hdlr = box(b"hdlr", struct.pack(">I4s4s", 0, b"mhlr", handler) + b"\x00" * 12 + b"\x00")
    stsd = box(b"stsd", struct.pack(">II", 0, 1) + struct.pack(">I4s", 16, codec) + b"\x00" * 8)
    mdia = box(b"mdia", hdlr + box(b"minf", box(b"stbl", stsd)))
    return box(b"trak", tkhd + mdia)


def mp4(seconds: float = 8.0, *, width: int = 1080, height: int = 1920, audio: bool = False, moov_last: bool = False,
        version: int = 0, brand: bytes = b"isom", codec: bytes = b"avc1", pad: int = 256) -> bytes:
    ftyp = box(b"ftyp", brand + struct.pack(">I", 0) + brand)
    traks = _trak(b"vide", codec, width, height) + (_trak(b"soun", b"mp4a", 0, 0) if audio else b"")
    moov = box(b"moov", _mvhd(seconds, 600, version) + traks)
    mdat = box(b"mdat", b"\x00" * pad)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def mov(seconds: float = 8.0, **more: object) -> bytes:
    return mp4(seconds, brand=b"qt  ", **more)  # type: ignore[arg-type]
