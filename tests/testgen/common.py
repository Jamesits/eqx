"""Analytic curves, impulse responses and values shared by the test data writers."""

from __future__ import annotations

import cmath
import math
from pathlib import Path

from eqx import dsp
from eqx.model import standard_grid

ROOT = Path(__file__).resolve().parent.parent.parent / "testdata"

# All-zero computer ID; the encrypted .swhp files open with it.
COMPUTER_ID = "g" + "0" * 40
SWPROJ_PASSWORD = "eqx"
ZERO_IV = bytes(16)
ANGLES = ("degrees_0", "degrees_30", "degrees_90")
SIDES = ("Left", "Right")

# Outputs of conversions only.
CSV_DIR = "autoeq/csv"
FIR_DIR = "fir/wav"


# ---------------------------------------------------------------------------
# analytic curves
# ---------------------------------------------------------------------------
# A section is (numerator, denominator): polynomial coefficients in s,
# lowest power first.  Products of sections are minimum phase.
def _poly(c, s):
    return sum(a * s**i for i, a in enumerate(c))


def _dpoly(c, s):
    return sum(i * a * s ** (i - 1) for i, a in enumerate(c) if i)


def hp2(f0, q):
    w = 2 * math.pi * f0
    return [0, 0, 1], [w * w, w / q, 1]


def lp2(f0, q):
    w = 2 * math.pi * f0
    return [w * w], [w * w, w / q, 1]


def hp1(f0):
    w = 2 * math.pi * f0
    return [0, 1], [w, 1]


def peak(f0, gain_db, q):
    w, a = 2 * math.pi * f0, 10 ** (gain_db / 40)
    return [w * w, w * a / q, 1], [w * w, w / (a * q), 1]


def high_cut(f0, gain_db):
    """First order: 0 dB at low frequencies, ``gain_db`` at high frequencies."""
    w, g = 2 * math.pi * f0, 10 ** (gain_db / 20)
    return [w, g], [w, 1]


def response(sections, f):
    """(dB, phase degrees, group delay seconds) of the sections at ``f`` Hz."""
    s = 2j * math.pi * f
    h, gd = 1 + 0j, 0.0
    for num, den in sections:
        h *= _poly(num, s) / _poly(den, s)
        # d(arg H)/dw = Re(H'(s)/H(s)); group delay is its negative.
        gd -= (_dpoly(num, s) / _poly(num, s) - _dpoly(den, s) / _poly(den, s)).real
    return 20 * math.log10(abs(h)), math.degrees(cmath.phase(h)), gd


def rounded_points(sections, grid=None) -> list[tuple[float, float, float]]:
    """(frequency, dB, group delay) of the sections on ``grid`` (default: the standard grid),
    rounded as SoundID stores them."""
    out = []
    for f in grid or standard_grid():
        db, _phase, gd = response(sections, f)
        out.append((f, round(db, 6), round(gd, 9)))
    return out


# ---------------------------------------------------------------------------
# speaker filters and impulse responses
# ---------------------------------------------------------------------------
# Bells (frequency, gain dB, Q) of each channel's correction.
SPEAKER = {
    "Left": [(60, 6, 0.7), (1000, -3, 1.4), (8000, 4, 2)],
    "Right": [(80, 5, 0.9), (1500, -2, 2), (9000, 3, 1.5)],
}


def bells(side: str, rate: float = dsp.PEQ_SAMPLE_RATE) -> list:
    return [dsp.bell(f, g, q, rate) for f, g, q in SPEAKER[side]]


def subwoofer(rate: float) -> list:
    """The subwoofer of the multichannel measurements: a low pass."""
    return [dsp.pass_filter(False, 100, 0.7071, rate)]


def normalized(bq) -> list[float]:
    """b0, b1, b2, a0, a1, a2 with a0 = 1."""
    return [
        bq.b0 / bq.a0,
        bq.b1 / bq.a0,
        bq.b2 / bq.a0,
        1.0,
        bq.a1 / bq.a0,
        bq.a2 / bq.a0,
    ]


def filtered(x: list[float], biquads) -> list[float]:
    """``x`` through the biquads in series."""
    for bq in biquads:
        y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
        for v in x:
            out = (
                bq.b0 * v + bq.b1 * x1 + bq.b2 * x2 - bq.a1 * y1 - bq.a2 * y2
            ) / bq.a0
            y.append(out)
            x1, x2, y1, y2 = v, x1, out, y1
        x = y
    return x


def impulse_response(length: int, delay: int, gain_db: float, biquads) -> list[float]:
    """An impulse of ``gain_db`` at sample ``delay``, through the biquads."""
    x = [0.0] * length
    x[delay] = 10 ** (gain_db / 20)
    return filtered(x, biquads)
