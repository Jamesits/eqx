"""Reader and writer for IK Multimedia ``IKMPAK`` containers.

Little endian: ``IKMPAK``, u32 version, then the entry table::

    v1: u32 count; entries: name NUL, u32 offset, u32 size
    v2: u32 count; entries: name NUL, u64 offset, u64 size
    v3: u64 count, u64 table size; entries: name NUL, u64 offset, u64 size

Offsets are absolute; data is stored uncompressed.
"""

from __future__ import annotations

import struct
from pathlib import Path

from ..fileformat import file_section as _file_section
from ..report import Section, Table

MAGIC = b"IKMPAK"
VERSIONS = (1, 2, 3)
# IK's reader copies a name into a 255-byte buffer, NUL included.
MAX_NAME = 254


def read(data: bytes) -> tuple[int, dict[str, bytes]]:
    """The version and the entries, name -> content."""
    if data[:6] != MAGIC or len(data) < 10:
        raise ValueError("not an IK Multimedia pak (no IKMPAK header)")
    version = struct.unpack_from("<I", data, 6)[0]
    if version not in VERSIONS:
        raise ValueError(f"unsupported pak version {version}")
    pos = 10
    if version == 3:
        count, table = _unpack("<QQ", data, pos)
        pos += 16
        end = pos + table
        if end > len(data):
            raise ValueError("pak entry table is truncated")
    else:
        count = _unpack("<I", data, pos)[0]
        pos += 4
        end = len(data)
    number = "<II" if version == 1 else "<QQ"
    entries: dict[str, bytes] = {}
    for _ in range(count):
        nul = data.find(b"\0", pos, min(end, pos + MAX_NAME + 1))
        if nul <= pos:
            raise ValueError("pak entry name is empty, too long or truncated")
        name = data[pos:nul].decode("utf-8").replace("\\", "/")
        pos = nul + 1
        if pos + struct.calcsize(number) > end:
            raise ValueError("pak entry table is truncated")
        offset, size = struct.unpack_from(number, data, pos)
        pos += struct.calcsize(number)
        if offset + size > len(data):
            raise ValueError(f"pak entry {name!r} lies outside the file")
        entries[name] = data[offset:offset + size]
    if version == 3 and pos != end:
        raise ValueError("pak entry table size does not match its entries")
    return version, entries


def write(entries: dict[str, bytes]) -> bytes:
    """Version 3, entries sorted by name, as ARC X writes them."""
    names = sorted(entries)
    for name in names:
        if not 0 < len(name.encode()) <= MAX_NAME or "\0" in name:
            raise ValueError(f"invalid pak entry name {name!r}")
    table_size = sum(len(n.encode()) + 17 for n in names)
    offset = 26 + table_size
    table, body = b"", b""
    for name in names:
        table += name.encode() + b"\0" + struct.pack("<QQ", offset + len(body), len(entries[name]))
        body += entries[name]
    return MAGIC + struct.pack("<IQQ", 3, len(names), table_size) + table + body


def _unpack(fmt: str, data: bytes, pos: int) -> tuple:
    if pos + struct.calcsize(fmt) > len(data):
        raise ValueError("pak header is truncated")
    return struct.unpack_from(fmt, data, pos)


def file_section(path: Path, data: bytes, version: int, sizes: dict[str, int],
                 *fields) -> Section:
    """The inspector's file section of a pak: ``fields``, the version and the entry sizes."""
    section = _file_section(path, data, *fields, ("pak version", version),
                            ("entries", len(sizes)))
    section.table = Table(["entry", "size"], sorted(sizes.items()))
    return section
