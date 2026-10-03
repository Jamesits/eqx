"""DRC raw impulse response (``*.pcm``): headerless 32-bit float samples.

DRC reads and writes this as ``F`` (``BCInFileType``, ``PSOutFileType``) in
the byte order of the machine; every DRC build in use is little-endian.  The
file holds no sample rate.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from .. import impulse
from ..fileformat import Format, Inspector, file_section, response_section
from ..options import Option
from ..report import Section

SAMPLE_SIZE = 4
DEFAULT_RATE = 48000.0


def read(data: bytes, name: str = "PCM") -> list[float]:
    if not data or len(data) % SAMPLE_SIZE:
        raise ValueError(
            f"{name}: {len(data)} bytes is not a whole number of 32-bit float samples"
        )
    samples = list(struct.unpack(f"<{len(data) // SAMPLE_SIZE}f", data))
    if not all(math.isfinite(v) for v in samples):
        raise ValueError(f"{name} holds non-finite samples")
    return samples


def load(path) -> list[float]:
    path = Path(path)
    return read(path.read_bytes(), path.name)


def write(samples) -> bytes:
    return struct.pack(f"<{len(samples)}f", *samples)


def response(
    samples: list[float], rate: float, frequencies: list[float]
) -> tuple[list[float], list[float]]:
    """(dB, group delay s) at ``frequencies``: 1/48-octave bands; the delay
    before the peak is not part of the group delay."""
    if frequencies[-1] >= rate / 2:
        raise ValueError(
            f"{rate:g} Hz sample rate: no response at {frequencies[-1]:g} Hz"
        )
    bands = impulse.point_bands(samples, rate, frequencies)
    return [impulse.power_db(p) for p, _ in bands], [gd for _, gd in bands]


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class PcmInspector(Inspector):
    options = (Option("--rate", type=float, help="sample rate, Hz (default: 48000)"),)

    def __init__(self, rate: float = DEFAULT_RATE):
        self.rate = rate

    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        samples = read(data, path.name)
        peak = impulse.peak_index(samples)
        grid = impulse.log_grid(self.rate)
        return [
            file_section(path, data),
            response_section(
                "impulse response",
                [
                    ("sample rate Hz", self.rate),
                    ("samples", len(samples)),
                    ("length ms", 1000 * len(samples) / self.rate),
                    ("peak sample", peak),
                    ("peak value", samples[peak]),
                ],
                lambda: (grid, *response(samples, self.rate, grid)),
            ),
        ]


FORMAT = Format(
    "drc",
    (".pcm",),
    "DRC raw impulse response (32-bit float)",
    PcmInspector,
)
