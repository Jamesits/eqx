"""Reader for SoundID ``LVND`` binary exports (Wayne Jones AUDIO ``*_Left.bin``, ``*_Right.bin``).

One file per channel, not encrypted.  Header: ``LVND``, two zero ``uint32``,
``uint32`` length of the messages (little endian).  Zero padding follows the
messages.

A message is ``0x02``, an ASCII digit (message index mod 10), the fields, ``0x03``.
A field is a ``uint32`` in five bytes of seven bits each, least significant
first, with the top bit set.  The last field is the XOR of the others.

| Message | Fields                                                   |
|---------|----------------------------------------------------------|
| biquad  | ``0x000B0065``, address, 0, 2048, 5, float32 b0 b1 b2 a1 a2 |
| gain    | ``0x00060069``, address, float32 dB, 256, 0              |
| delay   | ``0x0006003C``, address, int32 samples at 192 kHz, 512, 0 |
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from ..correction import Export, ExportInspector
from ..dsp import Biquad
from ..fileformat import Format
from ..model import Correction

MAGIC = b"LVND"
HEADER = struct.Struct("<4sIII")
STX, ETX = 0x02, 0x03
BIQUAD, GAIN, DELAY = 0x000B0065, 0x00060069, 0x0006003C
FIELDS = {BIQUAD: 11, GAIN: 6, DELAY: 6}
# SoundID writes these files only at this rate.
SAMPLE_RATE = 192000.0


def decode_field(group: bytes) -> int:
    if len(group) != 5 or any(b < 0x80 for b in group):
        raise ValueError(f"bad field bytes {group.hex(' ')}")
    return sum((b & 0x7F) << (7 * i) for i, b in enumerate(group)) & 0xFFFFFFFF


def encode_field(value: int) -> bytes:
    return bytes(((value >> (7 * i)) & 0x7F) | 0x80 for i in range(5))


def f32(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", value))[0]


def messages(data: bytes) -> list[list[int]]:
    """The fields of every message, checksums verified and removed."""
    if len(data) < HEADER.size or data[:4] != MAGIC:
        raise ValueError("not an LVND file")
    _magic, _zero1, _zero2, length = HEADER.unpack_from(data)
    body = data[HEADER.size:HEADER.size + length]
    if len(body) != length:
        raise ValueError("LVND file is truncated")
    out, i = [], 0
    while i < len(body):
        end = body.find(ETX, i)
        if body[i] != STX or end < 0 or (end - i - 2) % 5:
            raise ValueError(f"bad LVND message at byte {HEADER.size + i}")
        fields = [decode_field(body[k:k + 5]) for k in range(i + 2, end, 5)]
        check = 0
        for v in fields[:-1]:
            check ^= v
        if not fields or check != fields[-1]:
            raise ValueError(f"LVND message {len(out)}: bad checksum")
        out.append(fields[:-1])
        i = end + 1
    return out


def read(data: bytes, channel: str = "") -> Export:
    correction = Correction(channel, sample_rate=SAMPLE_RATE)
    for n, fields in enumerate(messages(data)):
        kind = fields[0]
        if FIELDS.get(kind) != len(fields) + 1:
            raise ValueError(f"LVND message {n}: unknown type {kind:08x} "
                             f"with {len(fields) + 1} fields")
        if kind == BIQUAD:
            b0, b1, b2, a1, a2 = (f32(v) for v in fields[5:10])
            correction.biquads.append(Biquad(b0, b1, b2, 1.0, a1, a2))
        elif kind == GAIN:
            correction.gain_db = f32(fields[2])
        else:
            samples = struct.unpack("<i", struct.pack("<I", fields[2]))[0]
            correction.delay_ms = samples / (SAMPLE_RATE / 1000)
    return Export([correction], [("sample rate", SAMPLE_RATE)])


def channel_from_name(path: Path) -> str:
    match = re.search(r"_(Left|Right)$", Path(path).stem, re.I)
    return match.group(1).capitalize() if match else ""


def load(path) -> Export:
    path = Path(path)
    return read(path.read_bytes(), channel_from_name(path))


class LvndInspector(ExportInspector):
    load = load


FORMAT = Format("soundid-export-lvnd", (".bin",),
                "SoundID export: LVND binary (Wayne Jones AUDIO)", LvndInspector,
                lambda data: data[:4] == MAGIC)
