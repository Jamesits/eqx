"""Biquad filters: Audio EQ Cookbook design and magnitude response."""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass

# Parametric filters without their own sample rate are evaluated at this rate.
# SoundID evaluates its target preset filters at it too.
PEQ_SAMPLE_RATE = 48000.0


@dataclass(frozen=True)
class Biquad:
    """H(z) = (b0 + b1 z^-1 + b2 z^-2) / (a0 + a1 z^-1 + a2 z^-2)."""

    b0: float
    b1: float
    b2: float
    a0: float
    a1: float
    a2: float

    def db(self, frequency: float, sample_rate: float) -> float:
        return 20 * math.log10(abs(self.h(frequency, sample_rate)))

    def h(self, frequency: float, sample_rate: float) -> complex:
        z = cmath.exp(-2j * math.pi * frequency / sample_rate)
        return ((self.b0 + self.b1 * z + self.b2 * z * z)
                / (self.a0 + self.a1 * z + self.a2 * z * z))


def cascade_db(biquads, frequency: float, sample_rate: float) -> float:
    """Gain of the biquads in series at ``frequency`` Hz, dB."""
    h = 1 + 0j
    for bq in biquads:
        h *= bq.h(frequency, sample_rate)
    return 20 * math.log10(abs(h))


def bell(frequency: float, gain_db: float, q: float,
         sample_rate: float = PEQ_SAMPLE_RATE) -> Biquad:
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * frequency / sample_rate
    cos = math.cos(w0)
    alpha = math.sin(w0) / (2 * q)
    return Biquad(1 + alpha * a, -2 * cos, 1 - alpha * a, 1 + alpha / a, -2 * cos, 1 - alpha / a)


def shelf(high: bool, frequency: float, gain_db: float, slope: float,
          sample_rate: float = PEQ_SAMPLE_RATE) -> Biquad:
    """Cookbook shelf; ``slope`` is the shelf slope S, not Q.

    S = 1 is the steepest shelf without overshoot.
    """
    a = 10 ** (gain_db / 40)
    return _shelf(high, frequency, a, math.sqrt((a + 1 / a) * (1 / slope - 1) + 2), sample_rate)


def shelf_q(high: bool, frequency: float, gain_db: float, q: float,
            sample_rate: float = PEQ_SAMPLE_RATE) -> Biquad:
    """Cookbook shelf with its steepness given as Q."""
    return _shelf(high, frequency, 10 ** (gain_db / 40), 1 / q, sample_rate)


def _shelf(high: bool, frequency: float, a: float, alpha_factor: float,
           sample_rate: float) -> Biquad:
    w0 = 2 * math.pi * frequency / sample_rate
    cos = math.cos(w0)
    alpha = math.sin(w0) / 2 * alpha_factor
    s = -1 if high else 1                   # high shelf: cos -> -cos
    root = 2 * math.sqrt(a) * alpha
    return Biquad(a * ((a + 1) - s * (a - 1) * cos + root),
                  s * 2 * a * ((a - 1) - s * (a + 1) * cos),
                  a * ((a + 1) - s * (a - 1) * cos - root),
                  (a + 1) + s * (a - 1) * cos + root,
                  -s * 2 * ((a - 1) + s * (a + 1) * cos),
                  (a + 1) + s * (a - 1) * cos - root)


def pass_filter(high: bool, frequency: float, q: float,
                sample_rate: float = PEQ_SAMPLE_RATE) -> Biquad:
    """Cookbook second-order high pass (``high``) or low pass."""
    w0 = 2 * math.pi * frequency / sample_rate
    cos = math.cos(w0)
    alpha = math.sin(w0) / (2 * q)
    b1 = -(1 + cos) if high else 1 - cos
    return Biquad(abs(b1) / 2, b1, abs(b1) / 2, 1 + alpha, -2 * cos, 1 - alpha)
