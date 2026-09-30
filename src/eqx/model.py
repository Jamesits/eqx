"""Format-independent data shared by readers, writers and converters."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from .dsp import PEQ_SAMPLE_RATE, Biquad, bell, cascade_db, pass_filter, shelf_q

EPOCH = "1970-01-01T00:00:00.000000Z"


@dataclass
class Measurement:
    """One speaker frequency response."""

    channel: str                 # channel name, e.g. "Left"
    index: int                   # channel index in the layout: 0 left, 1 right
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


@dataclass
class Correction:
    """One channel of a correction exported for a device.

    The response is the sum of the gain, the biquads (at ``sample_rate``),
    the parametric filters and the graphic EQ points.
    """

    channel: str                            # "Left", "Right", or the file's own name
    gain_db: float = 0.0
    delay_ms: float = 0.0
    sample_rate: float | None = None        # of ``biquads``
    biquads: list[Biquad] = field(default_factory=list)
    peqs: list[Peq] = field(default_factory=list)
    points: list[tuple[float, float]] = field(default_factory=list)   # graphic EQ, dB

    def response(self, frequencies) -> list[float]:
        """Gain at each frequency, dB.  Graphic points are interpolated in log frequency."""
        if self.biquads and not self.sample_rate:
            raise ValueError(f"{self.channel}: biquads without a sample rate")
        peqs = [p.biquad() for p in self.peqs]
        logs = [math.log(f) for f, _ in self.points]
        gains = [g for _, g in self.points]
        out = []
        for f in frequencies:
            db = self.gain_db
            if self.biquads:
                db += cascade_db(self.biquads, f, self.sample_rate)
            if peqs:
                db += cascade_db(peqs, f, PEQ_SAMPLE_RATE)
            if self.points:
                db += _interp_log(logs, gains, math.log(f))
            out.append(db + 0.0)
        return out


PEQ_KINDS = ("bell", "low-shelf", "high-shelf", "low-pass", "high-pass")


@dataclass(frozen=True)
class Peq:
    """A parametric filter.  Shelves take Q; pass filters ignore the gain."""

    frequency: float                        # Hz
    gain_db: float
    q: float
    kind: str = "bell"                      # one of PEQ_KINDS

    def biquad(self) -> Biquad:
        if self.kind == "bell":
            return bell(self.frequency, self.gain_db, self.q)
        if self.kind in ("low-shelf", "high-shelf"):
            return shelf_q(self.kind == "high-shelf", self.frequency, self.gain_db, self.q)
        if self.kind in ("low-pass", "high-pass"):
            return pass_filter(self.kind == "high-pass", self.frequency, self.q)
        raise ValueError(f"unsupported filter type {self.kind!r}")


def _interp_log(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation, clamped to the end values."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x) - 1
    return ys[i] + (x - xs[i]) / (xs[i + 1] - xs[i]) * (ys[i + 1] - ys[i])
