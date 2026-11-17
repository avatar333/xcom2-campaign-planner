"""Shared test helpers (tiny synthetic image bytes for asset/upload tests)."""
from __future__ import annotations

import struct
import zlib


def make_png_bytes(width: int = 4, height: int = 4) -> bytes:
    """A minimal, valid single-color PNG, built by hand (no Pillow needed to
    *create* the fixture, only to validate it in the app under test)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw_rows = b"".join(b"\x00" + bytes([200, 30, 30] * width) for _ in range(height))
    idat = zlib.compress(raw_rows)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_text_file_bytes() -> bytes:
    return b"this is definitely not an image, just plain text bytes" * 10
