"""Interpolation and statistics of sampled curves."""

from __future__ import annotations

import bisect
import math

from . import fmath


def interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation, clamped to the end values."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x) - 1
    x0, x1, y0, y1 = xs[i], xs[i + 1], ys[i], ys[i + 1]
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def resample(xs: list[float], ys: list[float], grid) -> list[float]:
    """``interp`` at each point of ``grid``."""
    return [interp(xs, ys, x) for x in grid]


def log_resample(points, frequencies) -> list[float]:
    """(frequency, value) ``points`` at ``frequencies``: linear in log frequency,
    clamped to the end values."""
    return resample(
        [fmath.log(f) for f, _ in points],
        [v for _, v in points],
        [fmath.log(f) for f in frequencies],
    )


def quantile(values: list[float], q: float) -> float:
    """Linearly interpolated quantile, ``0 <= q <= 1``."""
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    i = math.floor(position)
    if i + 1 >= len(ordered):
        return ordered[-1]
    return ordered[i] + (position - i) * (ordered[i + 1] - ordered[i])
