"""Biquad filters (Audio EQ Cookbook design and magnitude response); FFT; FIR design; bell fit."""

from __future__ import annotations

import bisect
import math
import operator
import statistics
from dataclasses import dataclass

from . import fmath

# Parametric filters without their own sample rate are evaluated at this rate.
# SoundID evaluates its target preset filters at it too.
PEQ_SAMPLE_RATE = 48000.0
# 10 log10(x) = _DB_PER_NEPER * ln(x)
_DB_PER_NEPER = 10 / fmath.log(10)


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
        return 20 * fmath.log10(fmath.cabs(self.h(frequency, sample_rate)))

    def h(self, frequency: float, sample_rate: float) -> complex:
        z = fmath.cexp(-2j * math.pi * frequency / sample_rate)
        return (self.b0 + self.b1 * z + self.b2 * z * z) / (
            self.a0 + self.a1 * z + self.a2 * z * z
        )


def cascade_db(biquads, frequency: float, sample_rate: float) -> float:
    """Gain of the biquads in series at ``frequency`` Hz, dB."""
    h = 1 + 0j
    for bq in biquads:
        h *= bq.h(frequency, sample_rate)
    return 20 * fmath.log10(fmath.cabs(h))


def _cookbook(frequency: float, q: float, sample_rate: float) -> tuple[float, float]:
    """(cos w0, alpha) of the Audio EQ Cookbook."""
    w0 = 2 * math.pi * frequency / sample_rate
    return fmath.cos(w0), fmath.sin(w0) / (2 * q)


def bell(
    frequency: float, gain_db: float, q: float, sample_rate: float = PEQ_SAMPLE_RATE
) -> Biquad:
    a = fmath.pow(10, gain_db / 40)
    cos, alpha = _cookbook(frequency, q, sample_rate)
    return Biquad(
        1 + alpha * a, -2 * cos, 1 - alpha * a, 1 + alpha / a, -2 * cos, 1 - alpha / a
    )


def shelf(
    high: bool,
    frequency: float,
    gain_db: float,
    slope: float,
    sample_rate: float = PEQ_SAMPLE_RATE,
) -> Biquad:
    """Cookbook shelf; ``slope`` is the shelf slope S, not Q.

    S = 1 is the steepest shelf without overshoot.
    """
    a = fmath.pow(10, gain_db / 40)
    return _shelf(
        high, frequency, a, math.sqrt((a + 1 / a) * (1 / slope - 1) + 2), sample_rate
    )


def shelf_q(
    high: bool,
    frequency: float,
    gain_db: float,
    q: float,
    sample_rate: float = PEQ_SAMPLE_RATE,
) -> Biquad:
    """Cookbook shelf with its steepness given as Q."""
    return _shelf(high, frequency, fmath.pow(10, gain_db / 40), 1 / q, sample_rate)


def _shelf(
    high: bool, frequency: float, a: float, alpha_factor: float, sample_rate: float
) -> Biquad:
    w0 = 2 * math.pi * frequency / sample_rate
    cos = fmath.cos(w0)
    alpha = fmath.sin(w0) / 2 * alpha_factor
    s = -1 if high else 1  # high shelf: cos -> -cos
    root = 2 * math.sqrt(a) * alpha
    return Biquad(
        a * ((a + 1) - s * (a - 1) * cos + root),
        s * 2 * a * ((a - 1) - s * (a + 1) * cos),
        a * ((a + 1) - s * (a - 1) * cos - root),
        (a + 1) + s * (a - 1) * cos + root,
        -s * 2 * ((a - 1) + s * (a + 1) * cos),
        (a + 1) + s * (a - 1) * cos - root,
    )


def pass_filter(
    high: bool, frequency: float, q: float, sample_rate: float = PEQ_SAMPLE_RATE
) -> Biquad:
    """Cookbook second-order high pass (``high``) or low pass."""
    cos, alpha = _cookbook(frequency, q, sample_rate)
    b1 = -(1 + cos) if high else 1 - cos
    return Biquad(abs(b1) / 2, b1, abs(b1) / 2, 1 + alpha, -2 * cos, 1 - alpha)


def notch(frequency: float, q: float, sample_rate: float = PEQ_SAMPLE_RATE) -> Biquad:
    cos, alpha = _cookbook(frequency, q, sample_rate)
    return Biquad(1, -2 * cos, 1, 1 + alpha, -2 * cos, 1 - alpha)


def all_pass(
    frequency: float, q: float, sample_rate: float = PEQ_SAMPLE_RATE
) -> Biquad:
    cos, alpha = _cookbook(frequency, q, sample_rate)
    return Biquad(1 - alpha, -2 * cos, 1 + alpha, 1 + alpha, -2 * cos, 1 - alpha)


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
        tw = _TWIDDLES[n] = [fmath.cexp(-2j * math.pi * k / n) for k in range(n // 2)]
    odd = [w * o for w, o in zip(tw, odd)]
    return [e + o for e, o in zip(even, odd)] + [e - o for e, o in zip(even, odd)]


def ifft(x) -> list[complex]:
    """Inverse of ``fft``."""
    n = len(x)
    return [v.conjugate() / n for v in fft([complex(v).conjugate() for v in x])]


# --------------------------------------------------------------------------
# FIR design
# --------------------------------------------------------------------------
PHASES = ("minimum", "linear", "mixed")

# SoundID Reference's filter lengths at 44.1 kHz; they scale with the sample
# rate, rounded down to an even number, plus one: 4093 and 4353 taps at 48 kHz.
# Mixed phase has the linear-phase length.
MINIMUM_TAPS_44K1 = 3760
LINEAR_TAPS_44K1 = 4000
# Mixed phase is a linear-phase filter delayed by this fraction of the half
# length, its start cut off: 826 samples at 48 kHz.
MIXED_LINEARITY = 0.38
# Design grid: at least GRID_SIZE points and at least GRID_FACTOR times the
# filter length.  SoundID uses 32768 points at every rate and length (factor
# 1).  The minimum-phase response is computed on the grid, so a grid shorter
# than twice the filter folds the response's tail onto its start; a zero-phase
# response wraps around the grid.  At 32768 taps a factor of 1 is 0.011 dB off
# the curve below 200 Hz, a factor of 2 is 0.003 dB.  At SoundID's lengths
# the GRID_SIZE floor makes them equal.
GRID_SIZE = 32768
GRID_FACTOR = 2
# SoundID does not delay the Nyquist bin of a linear- or mixed-phase filter.
# With an odd latency (32, 96 and 192 kHz linear phase) its sign flips: a
# notch at Nyquist, -3.5 dB, gone 50 Hz below.  Off by default: the notch has
# no use.  On reproduces SoundID.
NYQUIST_NOTCH = False
# SoundID's Kaiser tapers: minimum phase over the second half, linear phase
# over the whole filter.
MINIMUM_TAPER_ALPHA = 8.0
LINEAR_TAPER_ALPHA = 2 * math.pi


def fir_taps(sample_rate: float, phase: str) -> int:
    """SoundID's filter length at ``sample_rate``; always odd."""
    if phase not in PHASES:
        raise ValueError(f"phase must be one of: {', '.join(PHASES)}")
    base = MINIMUM_TAPS_44K1 if phase == "minimum" else LINEAR_TAPS_44K1
    return (math.floor(base * sample_rate / 44100) & ~1) + 1


def fir_latency(taps: int, phase: str) -> int:
    """The peak sample of a SoundID filter with ``taps`` taps."""
    if phase == "linear":
        return taps // 2
    if phase == "mixed":
        return math.floor(taps // 2 * MIXED_LINEARITY)
    return 0


def hermite(xs: list[float], ys: list[float], queries) -> list[float]:
    """SoundID's curve through (xs, ys) at ``queries``; clamped to the end values.

    Cubic Hermite: the slope at a point is the secant of its two neighbours.
    The first and last segments are straight lines.
    """
    n = len(xs)
    if n < 2:
        raise ValueError("a curve needs two points")
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    if min(h) <= 0:
        raise ValueError("curve points must be strictly increasing")
    m = (
        [0.0]
        + [(ys[i + 1] - ys[i - 1]) / (xs[i + 1] - xs[i - 1]) for i in range(1, n - 1)]
        + [0.0]
    )
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
        if i == 0 or i == n - 2:
            out.append(ys[i] + t * (ys[i + 1] - ys[i]))
            continue
        t2 = t * t
        t3 = t2 * t
        out.append(
            (2 * t3 - 3 * t2 + 1) * ys[i]
            + (t3 - 2 * t2 + t) * h[i] * m[i]
            + (3 * t2 - 2 * t3) * ys[i + 1]
            + (t3 - t2) * h[i] * m[i + 1]
        )
    return out


def minimum_phase(magnitude: list[float]) -> list[float]:
    """Minimum-phase impulse response (real cepstrum) of ``n // 2 + 1`` linear gains.

    ``n`` is a power of two; the result has ``n`` samples.
    """
    n = 2 * (len(magnitude) - 1)
    log = [fmath.log(max(g, 1e-15)) for g in magnitude]
    cepstrum = [c.real for c in ifft(log + log[-2:0:-1])]
    folded = (
        [cepstrum[0]]
        + [2 * c for c in cepstrum[1 : n // 2]]
        + [cepstrum[n // 2]]
        + [0.0] * (n // 2 - 1)
    )
    return [v.real for v in ifft([fmath.cexp(c) for c in fft(folded)])]


def zero_phase(magnitude: list[float]) -> list[float]:
    """Zero-phase impulse response of ``n // 2 + 1`` linear gains; sample 0 is the center."""
    return [v.real for v in ifft(list(magnitude) + list(magnitude[-2:0:-1]))]


def _bessel_i0(x: float) -> float:
    y = x * x / 4
    term, total, k = 1.0, 1.0, 0
    while term > 1e-17 * total:
        k += 1
        term *= y / (k * k)
        total += term
    return total


def _soundid_i0(x: float) -> float:
    """SoundID's linear-phase Kaiser "I0": sum of (x/2)^(2k-1) / (k! (k+1)!), k = 1..9, plus 1.

    Not the Bessel function; the taper it gives is what SoundID plays.
    """
    y = x / 2
    total, power, fact = 1.0, y, 1.0  # fact = k!
    for k in range(1, 10):
        total += power / (fact * fact * (k + 1))
        power *= y * y
        fact *= k + 1
    return total


def _kaiser(x: float, alpha: float, i0) -> float:
    """Kaiser window at ``x`` in [-1, 1]."""
    return i0(alpha * math.sqrt(max(0.0, 1 - x * x))) / i0(alpha)


def design_fir(
    frequencies: list[float],
    gains_db: list[float],
    sample_rate: float,
    phase: str = "minimum",
    taps: int | None = None,
    edge_db: float | None = None,
    nyquist_notch: bool = NYQUIST_NOTCH,
    grid_factor: int = GRID_FACTOR,
) -> list[float]:
    """FIR filter with the gain ``gains_db`` at ``frequencies`` (Hz, increasing).

    The curve is interpolated with ``hermite`` over log frequency, flat
    beyond its ends.  ``edge_db`` sets DC and Nyquist.  Minimum phase peaks at sample 0,
    linear phase at the center, mixed phase at ``fir_latency``; all are
    tapered like SoundID's filters.  ``nyquist_notch``: SoundID's undelayed
    Nyquist bin.  ``grid_factor``: the design grid is at least this many
    times ``taps``.
    """
    taps = taps or fir_taps(sample_rate, phase)
    if phase not in PHASES:
        raise ValueError(f"phase must be one of: {', '.join(PHASES)}")
    if phase == "linear" and taps % 2 == 0:
        raise ValueError("a linear-phase filter needs an odd number of taps")
    if taps < 3:
        raise ValueError("a filter needs at least 3 taps")
    if not isinstance(grid_factor, int) or grid_factor < 1:
        raise ValueError("the grid factor must be a positive integer")
    n = max(GRID_SIZE, 1 << (grid_factor * taps - 1).bit_length())
    step = sample_rate / n
    logs = [fmath.log(f) for f in frequencies]
    db = hermite(logs, list(gains_db), [fmath.log(k * step) for k in range(1, n // 2)])
    edge = gains_db[0] if edge_db is None else edge_db
    top = gains_db[-1] if edge_db is None else edge_db
    magnitude = [fmath.pow(10, g / 20) for g in [edge, *db, top]]
    if phase == "minimum":
        ir = minimum_phase(magnitude)[:taps]
        start = taps / 2
        return [
            v
            * (
                _kaiser((i - start) / start, MINIMUM_TAPER_ALPHA, _bessel_i0)
                if i >= start
                else 1.0
            )
            for i, v in enumerate(ir)
        ]
    latency = fir_latency(taps, phase)
    ir = zero_phase(magnitude)
    # An undelayed Nyquist bin: with an odd latency its sign flips.
    nyquist = 2 * magnitude[-1] / n if nyquist_notch and latency % 2 else 0.0
    # SoundID's taper is one sample wider than the filter's longer side.
    width = taps - latency
    return [
        (ir[i % n] + (nyquist if i % 2 else -nyquist))
        * _kaiser(i / width, LINEAR_TAPER_ALPHA, _soundid_i0)
        for i in range(-latency, taps - latency)
    ]


def design_fir_points(
    points,
    sample_rate: float,
    phase: str = "minimum",
    taps: int | None = None,
    edge_db: float | None = None,
    level_db: float = 0.0,
    nyquist_notch: bool = NYQUIST_NOTCH,
    grid_factor: int = GRID_FACTOR,
) -> list[float]:
    """``design_fir`` of (frequency, gain dB) ``points``, less ``level_db``."""
    return design_fir(
        [f for f, _ in points],
        [g - level_db for _, g in points],
        sample_rate,
        phase,
        taps,
        edge_db,
        nyquist_notch,
        grid_factor,
    )


def padded(irs: list[list[float]]) -> list[list[float]]:
    """The impulse responses, zero padded to the longest."""
    length = max(len(ir) for ir in irs)
    return [ir + [0.0] * (length - len(ir)) for ir in irs]


def fir_gain_db(ir: list[float], sample_rate: float, frequencies) -> list[float]:
    """Gain of an impulse response at ``frequencies``, dB (interpolated FFT bins)."""
    n = 1 << max(16, (len(ir) - 1).bit_length() + 2)
    spectrum = fft(list(ir) + [0.0] * (n - len(ir)))
    power = [v.real * v.real + v.imag * v.imag for v in spectrum[: n // 2 + 1]]
    out = []
    for f in frequencies:
        x = min(max(f * n / sample_rate, 0.0), n / 2)
        k = min(int(x), n // 2 - 1)
        p = power[k] + (x - k) * (power[k + 1] - power[k])
        out.append(10 * fmath.log10(max(p, 1e-30)))
    return out


# --------------------------------------------------------------------------
# parametric EQ fit
# --------------------------------------------------------------------------
@dataclass
class BellFit:
    gain_db: float  # broadband gain
    bells: list[tuple[float, float, float]]  # (frequency Hz, gain dB, Q), by frequency
    rms_db: float  # error over the fitted points
    max_db: float


class _Bells:
    """Gains, dB, of cookbook bells at fixed frequencies."""

    def __init__(self, frequencies, sample_rate: float):
        w = [2 * math.pi * f / sample_rate for f in frequencies]
        self.cos1 = [fmath.cos(x) for x in w]
        self.cos2 = [fmath.cos(2 * x) for x in w]
        self.sample_rate = sample_rate

    def db(self, log_frequency: float, gain_db: float, log_q: float) -> list[float]:
        bq = bell(fmath.exp(log_frequency), gain_db, fmath.exp(log_q), self.sample_rate)
        # |H|^2 of a real biquad: (c0 + c1 cos w + c2 cos 2w) / (same for the poles).
        n0, n1, n2 = (
            bq.b0 * bq.b0 + bq.b1 * bq.b1 + bq.b2 * bq.b2,
            2 * (bq.b0 * bq.b1 + bq.b1 * bq.b2),
            2 * bq.b0 * bq.b2,
        )
        d0, d1, d2 = (
            bq.a0 * bq.a0 + bq.a1 * bq.a1 + bq.a2 * bq.a2,
            2 * (bq.a0 * bq.a1 + bq.a1 * bq.a2),
            2 * bq.a0 * bq.a2,
        )
        # Natural log, scaled: half the cost of log10 in this hot loop.
        log = fmath.log
        return [
            _DB_PER_NEPER * log((n0 + n1 * c1 + n2 * c2) / (d0 + d1 * c1 + d2 * c2))
            for c1, c2 in zip(self.cos1, self.cos2)
        ]


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; ``a`` is changed."""
    n = len(b)
    b = list(b)
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(a[r][i]))
        a[i], a[p], b[i], b[p] = a[p], a[i], b[p], b[i]
        for r in range(i + 1, n):
            f = a[r][i] / a[i][i]
            if f:
                a[r] = [x - f * y for x, y in zip(a[r], a[i])]
                b[r] -= f * b[i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (b[i] - math.fsum(a[i][j] * x[j] for j in range(i + 1, n))) / a[i][i]
    return x


def fit_bells(
    frequencies,
    gains_db,
    count: int,
    *,
    frequency_hz: tuple[float, float] = (20.0, 20000.0),
    gain_db: tuple[float, float] = (-20.0, 20.0),
    q: tuple[float, float] = (0.4, 9.9),
    sample_rate: float = PEQ_SAMPLE_RATE,
    tolerance_db: float = 0.05,
) -> BellFit:
    """A gain and up to ``count`` bells whose sum follows ``gains_db``, least squares.

    Each bell starts at the largest remaining error, then all parameters are
    refined together (Levenberg-Marquardt, bounded).  Bands stop being added
    once the error is below ``tolerance_db`` everywhere.
    """
    frequencies, target = list(frequencies), list(gains_db)
    curves = _Bells(frequencies, sample_rate)
    low = [gain_db[0], fmath.log(frequency_hz[0]), gain_db[0], fmath.log(q[0])]
    high = [gain_db[1], fmath.log(frequency_hz[1]), gain_db[1], fmath.log(q[1])]

    def clip(params: list[float]) -> list[float]:
        return [
            min(
                max(v, low[1 + (i - 1) % 3] if i else low[0]),
                high[1 + (i - 1) % 3] if i else high[0],
            )
            for i, v in enumerate(params)
        ]

    def bands(params: list[float]) -> list[list[float]]:
        return [curves.db(*params[i : i + 3]) for i in range(1, len(params), 3)]

    def errors(params: list[float], curves_db: list[list[float]]) -> list[float]:
        return [t - params[0] - math.fsum(c) for t, *c in zip(target, *curves_db)]

    def refine(params: list[float], iterations: int) -> list[float]:
        damping = 1e-3
        curves_db = bands(params)
        r = errors(params, curves_db)
        cost = math.fsum(e * e for e in r)
        for _ in range(iterations):
            columns = [[1.0] * len(target)]
            for k, base in enumerate(curves_db):
                for j in range(3):
                    p = params[1 + 3 * k : 4 + 3 * k]
                    p[j] += 1e-4
                    columns.append(
                        [(v - b) / 1e-4 for v, b in zip(curves.db(*p), base)]
                    )
            jtj = [
                [math.fsum(map(operator.mul, a, b)) for b in columns] for a in columns
            ]
            jtr = [math.fsum(map(operator.mul, a, r)) for a in columns]
            while True:
                m = [
                    [
                        v * (1 + damping) + 1e-9 if i == j else v
                        for j, v in enumerate(row)
                    ]
                    for i, row in enumerate(jtj)
                ]
                new = clip([p + d for p, d in zip(params, _solve(m, jtr))])
                new_curves = bands(new)
                new_r = errors(new, new_curves)
                new_cost = math.fsum(e * e for e in new_r)
                if new_cost < cost:
                    damping = max(damping / 3, 1e-9)
                    break
                damping *= 4
                if damping > 1e9:
                    return params
            converged = cost - new_cost < 1e-9 * cost
            params, curves_db, r, cost = new, new_curves, new_r, new_cost
            if converged:
                break
        return params

    params = clip([statistics.median(target)])
    for _ in range(count):
        r = errors(params, bands(params))
        i = max(range(len(r)), key=lambda n: abs(r[n]))
        if abs(r[i]) < tolerance_db:
            break
        # Width: where the error falls to half, on the same side of zero.
        lo = hi = i
        while lo > 0 and r[lo - 1] * r[i] > 0 and abs(r[lo - 1]) > abs(r[i]) / 2:
            lo -= 1
        while (
            hi < len(r) - 1 and r[hi + 1] * r[i] > 0 and abs(r[hi + 1]) > abs(r[i]) / 2
        ):
            hi += 1
        octaves = max(fmath.log2(frequencies[hi] / frequencies[lo]), 1 / 12)
        params = clip(
            params
            + [
                fmath.log(frequencies[i]),
                r[i],
                fmath.log(fmath.pow(2, octaves / 2) / (fmath.pow(2, octaves) - 1)),
            ]
        )
        params = refine(params, 10)
    params = refine(params, 200)
    r = errors(params, bands(params))
    # exp(log(bound)) can land one ulp outside the bound; clamp again.
    bells = sorted(
        (
            min(max(fmath.exp(params[i]), frequency_hz[0]), frequency_hz[1]),
            params[i + 1],
            min(max(fmath.exp(params[i + 2]), q[0]), q[1]),
        )
        for i in range(1, len(params), 3)
    )
    return BellFit(
        params[0],
        bells,
        math.sqrt(math.fsum(e * e for e in r) / len(r)),
        max(abs(e) for e in r),
    )
