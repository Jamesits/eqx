"""Biquad filters (Audio EQ Cookbook design and magnitude response); FFT; FIR design."""

from __future__ import annotations

import bisect
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


_TWIDDLES: dict[int, list[complex]] = {}


def fft(x) -> list[complex]:
    """Discrete Fourier transform, radix 2; ``len(x)`` must be a power of two."""
    n = len(x)
    if n & (n - 1) or n == 0:
        raise ValueError(f"FFT length {n} is not a power of two")
    return _fft(list(x))


def _fft(x: list) -> list:
    n = len(x)
    if n == 1:
        return x
    even, odd = _fft(x[0::2]), _fft(x[1::2])
    tw = _TWIDDLES.get(n)
    if tw is None:
        tw = _TWIDDLES[n] = [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)]
    odd = [w * o for w, o in zip(tw, odd)]
    return [e + o for e, o in zip(even, odd)] + [e - o for e, o in zip(even, odd)]


def ifft(x) -> list[complex]:
    """Inverse of ``fft``."""
    n = len(x)
    return [v.conjugate() / n for v in fft([complex(v).conjugate() for v in x])]


# --------------------------------------------------------------------------
# FIR design
# --------------------------------------------------------------------------
PHASES = ("minimum", "linear")

# SoundID Reference's filter lengths; they scale with the sample rate.  The
# linear-phase half length is 2000 samples at 44.1 kHz, rounded down: 2176 at
# 48 kHz, 4353 at 96 kHz.
MINIMUM_TAPS_48K = 4096
LINEAR_HALF_44K1 = 2000
# Design grid size; SoundID uses it at every sample rate.
GRID_SIZE = 32768
# Width of the linear-phase taper in half lengths.  Fitted to SoundID: its
# taper is not zero at the ends.
LINEAR_TAPER_WIDTH = 1.181


def fir_taps(sample_rate: float, phase: str) -> int:
    """SoundID's filter length at ``sample_rate``; linear phase is odd."""
    if phase == "minimum":
        return round(MINIMUM_TAPS_48K * sample_rate / 48000)
    if phase == "linear":
        return 2 * math.floor(LINEAR_HALF_44K1 * sample_rate / 44100) + 1
    raise ValueError(f"phase must be one of: {', '.join(PHASES)}")


def hermite(xs: list[float], ys: list[float], queries) -> list[float]:
    """Cubic Hermite curve through (xs, ys) at ``queries``; clamped to the end values.

    SoundID's curve interpolation: the slope at a point is the secant of its
    two neighbours, one-sided at the ends.
    """
    n = len(xs)
    if n < 2:
        raise ValueError("a curve needs two points")
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    if min(h) <= 0:
        raise ValueError("curve points must be strictly increasing")
    m = ([(ys[1] - ys[0]) / h[0]]
         + [(ys[i + 1] - ys[i - 1]) / (xs[i + 1] - xs[i - 1]) for i in range(1, n - 1)]
         + [(ys[-1] - ys[-2]) / h[-1]])
    out = []
    for x in queries:
        if x <= xs[0]:
            out.append(ys[0])
            continue
        if x >= xs[-1]:
            out.append(ys[-1])
            continue
        i = bisect.bisect_right(xs, x) - 1
        t = (x - xs[i]) / h[i]
        out.append((2 * t ** 3 - 3 * t ** 2 + 1) * ys[i] + (t ** 3 - 2 * t ** 2 + t) * h[i] * m[i]
                   + (3 * t ** 2 - 2 * t ** 3) * ys[i + 1] + (t ** 3 - t ** 2) * h[i] * m[i + 1])
    return out


def minimum_phase(magnitude: list[float]) -> list[float]:
    """Minimum-phase impulse response (real cepstrum) of ``n // 2 + 1`` linear gains.

    ``n`` is a power of two; the result has ``n`` samples.
    """
    n = 2 * (len(magnitude) - 1)
    log = [math.log(max(g, 1e-15)) for g in magnitude]
    cepstrum = [c.real for c in ifft(log + log[-2:0:-1])]
    folded = ([cepstrum[0]] + [2 * c for c in cepstrum[1:n // 2]] + [cepstrum[n // 2]]
              + [0.0] * (n // 2 - 1))
    return [v.real for v in ifft([cmath.exp(c) for c in fft(folded)])]


def zero_phase(magnitude: list[float]) -> list[float]:
    """Zero-phase impulse response of ``n // 2 + 1`` linear gains; sample 0 is the center."""
    return [v.real for v in ifft(list(magnitude) + list(magnitude[-2:0:-1]))]


def _blackman(x: float) -> float:
    return 0.42 + 0.5 * math.cos(math.pi * x) + 0.08 * math.cos(2 * math.pi * x)


def design_fir(frequencies: list[float], gains_db: list[float], sample_rate: float,
               phase: str = "minimum", taps: int | None = None,
               edge_db: float | None = None) -> list[float]:
    """FIR filter with the gain ``gains_db`` at ``frequencies`` (Hz, increasing).

    The curve is interpolated with ``hermite`` over log frequency, flat
    beyond its ends.  ``edge_db`` sets DC and Nyquist.  Minimum phase peaks at sample 0,
    linear phase at the center; both are tapered like SoundID's filters.
    """
    taps = taps or fir_taps(sample_rate, phase)
    if phase not in PHASES:
        raise ValueError(f"phase must be one of: {', '.join(PHASES)}")
    if phase == "linear" and taps % 2 == 0:
        raise ValueError("a linear-phase filter needs an odd number of taps")
    if taps < 3:
        raise ValueError("a filter needs at least 3 taps")
    n = max(GRID_SIZE, 1 << (2 * taps - 1).bit_length())
    step = sample_rate / n
    logs = [math.log(f) for f in frequencies]
    db = hermite(logs, list(gains_db), [math.log(k * step) for k in range(1, n // 2)])
    edge = gains_db[0] if edge_db is None else edge_db
    top = gains_db[-1] if edge_db is None else edge_db
    magnitude = [10 ** (g / 20) for g in [edge, *db, top]]
    # The tapers are fitted to SoundID's: a half Blackman over the second
    # half (minimum phase), a Hamming wider than the filter (linear phase).
    if phase == "minimum":
        ir = minimum_phase(magnitude)[:taps]
        start = taps // 2
        return [v * (_blackman((i - start) / (taps - start)) if i >= start else 1.0)
                for i, v in enumerate(ir)]
    half = taps // 2
    ir = zero_phase(magnitude)
    width = LINEAR_TAPER_WIDTH * half
    return [ir[i % n] * (0.54 + 0.46 * math.cos(math.pi * i / width))
            for i in range(-half, half + 1)]


def fir_gain_db(ir: list[float], sample_rate: float, frequencies) -> list[float]:
    """Gain of an impulse response at ``frequencies``, dB (interpolated FFT bins)."""
    n = 1 << max(16, (len(ir) - 1).bit_length() + 2)
    spectrum = fft(list(ir) + [0.0] * (n - len(ir)))
    power = [abs(v) ** 2 for v in spectrum[:n // 2 + 1]]
    out = []
    for f in frequencies:
        x = min(max(f * n / sample_rate, 0.0), n / 2)
        k = min(int(x), n // 2 - 1)
        p = power[k] + (x - k) * (power[k + 1] - power[k])
        out.append(10 * math.log10(max(p, 1e-30)))
    return out
