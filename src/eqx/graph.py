"""Terminal graph of a frequency response: log frequency against value."""

from __future__ import annotations

import math

import plotext

from .report import Table

_FREQUENCY_COLUMNS = ("frequency Hz", "frequency")
# Characters of the frame and the curve.
_GLYPHS = "┌┐└┘─│┤┬⣿"


def supported(encoding: str | None) -> bool:
    """Whether text in ``encoding`` can hold the graph."""
    try:
        _GLYPHS.encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def curves(table: Table) -> list[tuple[str, list[tuple[float, float]]]]:
    """The (column, points) curves of a table, against its first column.

    A curve table starts with a frequency column; every other numeric column
    except a time (`` s``) column is a curve.  Empty cells, frequencies <= 0 and
    non-finite values are dropped.
    """
    if not table.columns or table.columns[0] not in _FREQUENCY_COLUMNS:
        return []
    out = []
    for i, name in enumerate(table.columns[1:], 1):
        if name.endswith(" s"):
            continue
        points = []
        for row in table.rows:
            f, v = row[0], row[i] if i < len(row) else None
            if f is None or v is None:
                continue
            if not (_number(f) and _number(v)):
                points = []
                break
            if f > 0 and math.isfinite(f) and math.isfinite(v):
                points.append((float(f), float(v)))
        if len(points) >= 2:
            out.append((name, sorted(points)))
    return out


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def plot(points: list[tuple[float, float]], width: int, height: int,
         color: bool = False) -> str:
    """A graph of sorted ``points``, ``width`` x ``height`` characters."""
    # Log frequency on a linear axis: plotext 6.1 squeezes a log axis that has
    # explicit ticks into its first column.
    xs = [math.log10(f) for f, _ in points]
    ys = [v for _, v in points]
    figure = plotext.figure
    figure.clear()
    figure.theme("simple")
    figure.plot_size(width, height)
    signal = figure.signal(xs, ys, marker="braille")
    signal.lines()
    figure.draw(signal)
    ticks = _frequency_ticks(points[0][0], points[-1][0])
    figure.ruler("x").ticks([math.log10(f) for f in ticks], [_frequency_label(f) for f in ticks])
    if max(ys) - min(ys) < 1e-9:
        # A flat curve gets a 2 dB range instead of an empty one.
        figure.ruler("y").lim(ys[0] - 1, ys[0] + 1)
    return figure.build().string(colorless=not color)


def _frequency_ticks(low: float, high: float) -> list[float]:
    """1-2-5 ticks inside [low, high]; only the decades if there are more than two."""
    decades = range(math.floor(math.log10(low)), math.ceil(math.log10(high)) + 1)
    inside = lambda f: low * (1 - 1e-9) <= f <= high * (1 + 1e-9)
    ticks = [10.0 ** e for e in decades if inside(10.0 ** e)]
    if len(ticks) <= 2:
        ticks = sorted(f for e in decades for f in (10.0 ** e, 2 * 10.0 ** e, 5 * 10.0 ** e)
                       if inside(f))
    return ticks or [low, high]


def _frequency_label(f: float) -> str:
    if f >= 1000:
        return format(f / 1000, ".3g") + "k"
    return format(f, ".3g")
