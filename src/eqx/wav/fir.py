"""FIR filter WAV (``*.wav``): one impulse response per channel.

Reader: PCM 16/24/32 bit, float 32/64 bit, ``WAVE_FORMAT_EXTENSIBLE``, any
channel count.  Writer: float 32 bit, PCM 16/24/32 bit.

Smaart's IR mode reads a WAV as 32-bit integers through JUCE: a float file
comes in as its raw bits, so it needs PCM.  Smaart shows the first channel.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .. import dsp, impulse
from ..fileformat import Format, Inspector, file_section
from ..model import standard_grid
from ..report import Section, Table

PCM, IEEE_FLOAT, EXTENSIBLE = 1, 3, 0xFFFE
# Written encodings: name -> bits.  Smaart's IR mode reads integer PCM only.
FLOAT32 = "float32"
ENCODINGS = {FLOAT32: 32, "pcm16": 16, "pcm24": 24, "pcm32": 32}
CHANNEL_NAMES = ("Left", "Right")
# Gain range and table of the inspection.
BAND_HZ = (20.0, 20000.0)


@dataclass
class Fir:
    sample_rate: float
    channels: list[list[float]]             # one impulse response per channel
    encoding: str = "float 32 bit"

    @property
    def taps(self) -> int:
        return len(self.channels[0]) if self.channels else 0


def channel_name(index: int, count: int) -> str:
    if count == 1:
        return "Mono"
    return CHANNEL_NAMES[index] if index < len(CHANNEL_NAMES) else f"Channel {index + 1}"


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
def read(data: bytes, name: str = "WAV") -> Fir:
    """Every channel of a PCM or float WAV; PCM is scaled to +-1."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{name} is not a WAV file")
    fmt = samples = None
    pos = 12
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if tag == b"fmt ":
            fmt = body
        elif tag == b"data":
            samples = body
        pos += 8 + size + (size & 1)        # chunks are word aligned
    if fmt is None or samples is None or len(fmt) < 16:
        raise ValueError(f"{name}: no fmt or data chunk")
    code, count, rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt)
    if code == EXTENSIBLE and len(fmt) >= 26:
        code = struct.unpack_from("<H", fmt, 24)[0]     # first bytes of the subformat GUID
    if count < 1 or rate < 1:
        raise ValueError(f"{name}: {count} channels at {rate} Hz")
    if code == IEEE_FLOAT and bits in (32, 64):
        frames = len(samples) // (bits // 8 * count)
        values = struct.unpack_from(f"<{frames * count}{'f' if bits == 32 else 'd'}", samples)
        encoding = f"float {bits} bit"
    elif code == PCM and bits in (16, 24, 32):
        width = bits // 8
        frames = len(samples) // (width * count)
        scale = 2.0 ** (bits - 1)
        values = [int.from_bytes(samples[i:i + width], "little", signed=True) / scale
                  for i in range(0, frames * count * width, width)]
        encoding = f"PCM {bits} bit"
    else:
        raise ValueError(f"{name}: unsupported WAV encoding {code}, {bits} bits")
    return Fir(float(rate), [list(values[c::count]) for c in range(count)], encoding)


def load(path) -> Fir:
    path = Path(path)
    return read(path.read_bytes(), path.name)


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def write(fir: Fir, encoding: str = FLOAT32) -> bytes:
    """WAV of ``encoding`` (``ENCODINGS``); every channel has the same length.
    PCM rejects samples beyond full scale."""
    count = len(fir.channels)
    if not count or len({len(c) for c in fir.channels}) != 1 or not fir.taps:
        raise ValueError("a FIR needs channels of one non-zero length")
    if encoding not in ENCODINGS:
        raise ValueError(f"encoding must be one of: {', '.join(ENCODINGS)}")
    rate = round(fir.sample_rate)
    frames = [v for frame in zip(*fir.channels) for v in frame]
    if encoding == FLOAT32:
        code, bits = IEEE_FLOAT, 32
        data = struct.pack(f"<{len(frames)}f", *frames)
    else:
        code, bits = PCM, ENCODINGS[encoding]
        peak = max(abs(v) for v in frames)
        if peak > 1.0:
            raise ValueError(f"samples reach {20 * math.log10(peak):+.2f} dB re full scale; "
                             f"{encoding} would clip")
        top = 2 ** (bits - 1) - 1
        width = bits // 8
        data = b"".join(round(v * top).to_bytes(width, "little", signed=True) for v in frames)
    size = bits // 8
    fmt = struct.pack("<HHIIHH", code, count, rate, rate * size * count, size * count, bits)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)


# --------------------------------------------------------------------------
# response
# --------------------------------------------------------------------------
def grid(sample_rate: float) -> list[float]:
    """The standard grid below Nyquist."""
    return [f for f in standard_grid() if f < sample_rate / 2]


def response(fir: Fir, channel: int, frequencies=None) -> list[tuple[float, float]]:
    """(frequency, gain dB) of one channel; default on ``grid``."""
    frequencies = grid(fir.sample_rate) if frequencies is None else list(frequencies)
    return list(zip(frequencies, dsp.fir_gain_db(fir.channels[channel], fir.sample_rate,
                                                 frequencies)))


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class FirInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        fir = read(data, path.name)
        count = len(fir.channels)
        sections = [file_section(path, data), Section("filter", [
            ("sample rate Hz", fir.sample_rate), ("channels", count), ("taps", fir.taps),
            ("length ms", 1000 * fir.taps / fir.sample_rate), ("encoding", fir.encoding),
        ])]
        for c, ir in enumerate(fir.channels):
            peak = impulse.peak_index(ir) if ir else 0
            points = response(fir, c)
            band = [g for f, g in points if BAND_HZ[0] <= f <= BAND_HZ[1]] or [math.nan]
            sections.append(Section(f"channel {channel_name(c, count)}", [
                ("peak sample", peak), ("peak ms", 1000 * peak / fir.sample_rate),
                ("peak value", ir[peak] if ir else math.nan),
                ("gain min dB", min(band)), ("gain max dB", max(band)),
            ], Table(["frequency Hz", "gain dB"], points)))
        return sections


def _sniff(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WAVE"


FORMAT = Format("fir", (".wav",), "FIR filter WAV (one impulse response per channel)",
                FirInspector, _sniff)
