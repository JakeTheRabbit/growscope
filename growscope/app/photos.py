"""Photo storage with capture-time extraction. Pure-python EXIF - no image
library, no native deps. Falls back to upload time when EXIF is missing."""
from __future__ import annotations

import logging
import re
import struct
from datetime import datetime
from pathlib import Path

from . import db
from .config import MEDIA_DIR

_LOG = logging.getLogger("photos")

PHOTOS_DIR = MEDIA_DIR / "photos"


def exif_datetime(data: bytes) -> datetime | None:
    """DateTimeOriginal (0x9003) or DateTime (0x0132) from a JPEG, or None."""
    try:
        if data[:2] != b"\xff\xd8":
            return None
        # Walk JPEG segments looking for APP1/Exif
        pos = 2
        tiff = None
        while pos + 4 < len(data) and pos < 65536 * 2:
            if data[pos] != 0xFF:
                break
            marker, size = data[pos + 1], struct.unpack(">H", data[pos + 2:pos + 4])[0]
            if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\x00\x00":
                tiff = data[pos + 10:pos + 2 + size]
                break
            pos += 2 + size
        if not tiff or len(tiff) < 8:
            return None
        endian = "<" if tiff[:2] == b"II" else ">"

        def u16(off: int) -> int:
            return struct.unpack(endian + "H", tiff[off:off + 2])[0]

        def u32(off: int) -> int:
            return struct.unpack(endian + "I", tiff[off:off + 4])[0]

        def read_ifd(off: int) -> dict[int, tuple[int, int, int]]:
            entries = {}
            count = u16(off)
            for i in range(count):
                base = off + 2 + i * 12
                if base + 12 > len(tiff):
                    break
                entries[u16(base)] = (u16(base + 2), u32(base + 4), u32(base + 8))
            return entries

        def ascii_at(entry: tuple[int, int, int]) -> str:
            _, count, value = entry
            if count <= 4:
                return ""
            return tiff[value:value + count].split(b"\x00")[0].decode("ascii", "ignore")

        ifd0 = read_ifd(u32(4))
        candidates = []
        if 0x8769 in ifd0:  # ExifIFD pointer
            exif_ifd = read_ifd(ifd0[0x8769][2])
            if 0x9003 in exif_ifd:
                candidates.append(ascii_at(exif_ifd[0x9003]))
        if 0x0132 in ifd0:
            candidates.append(ascii_at(ifd0[0x0132]))
        for raw in candidates:
            m = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", raw)
            if m:
                return datetime(*(int(g) for g in m.groups()))
        return None
    except (struct.error, IndexError, ValueError):
        return None


def save(grow: dict, filename: str, data: bytes, source: str = "upload") -> dict:
    taken = exif_datetime(data) or datetime.now()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "photo.jpg"
    grow_dir = PHOTOS_DIR / grow["slug"]
    grow_dir.mkdir(parents=True, exist_ok=True)
    name = f"{taken.strftime('%Y%m%d_%H%M%S')}_{safe}"
    rel = f"photos/{grow['slug']}/{name}"
    if db.photo_exists(grow["id"], rel):
        return {"duplicate": True, "path": rel}
    (grow_dir / name).write_bytes(data)
    row = db.add_photo(grow["id"], taken.isoformat(timespec="seconds"), rel, source)
    _LOG.info("photo saved for %s: %s (%d bytes)", grow["name"], rel, len(data))
    return row


def remove(photo_id: int) -> bool:
    row = db.delete_photo(photo_id)
    if not row:
        return False
    target = MEDIA_DIR / row["path"]
    try:
        target.unlink(missing_ok=True)
    except OSError:
        _LOG.warning("could not delete file %s", target)
    return True
