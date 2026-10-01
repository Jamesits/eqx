"""Apple Core Audio Format (``*.caf``) linear PCM.

A file is ``caff``, version 1, then chunks: 4-byte type, 8-byte big-endian
size.  ``desc`` holds the stream format, ``data`` a 4-byte edit count and the
interleaved samples; a ``data`` size of -1 runs to the end of the file.

Reader: integer 8/16/24/32 bit, float 32/64 bit, either byte order, any
channel count.  Writer: mono big-endian float 32 bit, as Core Audio writes it.
"""

from __future__ import annotations

import struct

FLOAT, LITTLE_ENDIAN = 1, 2                 # desc format flags


def read(data: bytes, name: str = "CAF") -> tuple[float, list[list[float]]]:
    """Sample rate and the samples of each channel; integers are scaled to +-1."""
    if data[:4] != b"caff" or len(data) < 8:
        raise ValueError(f"{name} is not a CAF file")
    desc = samples = None
    pos = 8
    while pos + 12 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack_from(">q", data, pos + 4)[0]
        pos += 12
        end = len(data) if size < 0 else pos + size
        if tag == b"desc":
            desc = data[pos:end]
        elif tag == b"data":
            samples = data[pos + 4:end]
        pos = end
    if desc is None or samples is None or len(desc) < 32:
        raise ValueError(f"{name}: no desc or data chunk")
    rate, fmt, flags, packet, frames, channels, bits = struct.unpack(">d4s5I", desc[:32])
    if fmt != b"lpcm" or channels < 1 or frames != 1 or packet != channels * bits // 8:
        raise ValueError(f"{name}: unsupported format {fmt!r}, {channels} channels, "
                         f"{bits} bit, {packet} bytes per packet")
    order = "<" if flags & LITTLE_ENDIAN else ">"
    width = bits // 8
    count = len(samples) // packet * channels
    if flags & FLOAT and bits in (32, 64):
        values = list(struct.unpack(f"{order}{count}{'f' if bits == 32 else 'd'}",
                                    samples[:count * width]))
    elif not flags & FLOAT and bits in (8, 16, 24, 32):
        values = [int.from_bytes(samples[i:i + width], "little" if order == "<" else "big",
                                 signed=True) / (1 << (bits - 1))
                  for i in range(0, count * width, width)]
    else:
        kind = "float" if flags & FLOAT else "integer"
        raise ValueError(f"{name}: unsupported {bits}-bit {kind}")
    return rate, [values[c::channels] for c in range(channels)]


def write(sample_rate: float, samples: list[float]) -> bytes:
    """Mono big-endian float 32-bit CAF."""
    desc = struct.pack(">d4s5I", sample_rate, b"lpcm", FLOAT, 4, 1, 1, 32)
    body = struct.pack(">I", 0) + struct.pack(f">{len(samples)}f", *samples)
    return (b"caff" + struct.pack(">HH", 1, 0)
            + b"desc" + struct.pack(">q", len(desc)) + desc
            + b"data" + struct.pack(">q", len(body)) + body)
