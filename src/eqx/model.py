"""Format-independent data shared by readers, writers and converters."""

from __future__ import annotations

import math
from dataclasses import dataclass

EPOCH = "1970-01-01T00:00:00.000000Z"


@dataclass
class Measurement:
    """One speaker frequency response."""

    channel: str                 # "Left" or "Right"
    index: int                   # channel index: 0 left, 1 right
    frequencies: list[float]
    response: list[float]        # dB SPL
    group_delay: list[float]     # seconds
    timestamp: str = EPOCH
    sample_rate: int = 48000
    name: str = ""
    source_file: str = ""
    source_format: str = ""


@dataclass
class MicProfile:
    """One microphone calibration table: the microphone's own response."""

    name: str                               # microphone serial
    angle: str                              # e.g. "degrees_0"
    points: list[tuple[float, float]]       # sorted (frequency Hz, gain dB)

    @classmethod
    def from_points(cls, name: str, angle: str, points) -> "MicProfile":
        """Sort ``points`` and drop non-finite ones."""
        points = sorted(
            (frequency, response) for frequency, response in points
            if math.isfinite(frequency) and math.isfinite(response)
        )
        if len(points) < 2:
            raise ValueError(f"{name}:{angle} contains too few calibration points")
        return cls(name, angle, points)
