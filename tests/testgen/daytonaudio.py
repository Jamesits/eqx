"""Dayton Audio microphone calibration files."""

from __future__ import annotations

from eqx import fmath
from eqx.fileformat import fixed

from .common import high_cut, hp2, peak, response

DAYTON_DIR = "daytonaudio/dayton"


def _log_grid(start: float, ratio: float, count: int) -> list[float]:
    return [start * fmath.pow(ratio, i) for i in range(count)]


def _decimals(value: float, digits: int) -> str:
    """``value`` rounded to ``digits`` decimals, trailing zeros removed."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


# The real grids: UMM-6 1/8 octave from 4.6758 Hz; OmniMic 1/48 octave up to
# 374 Hz, then 1/24 octave up to 40 kHz; EMM-6 256 log-spaced points, 20-20000 Hz.
USB_GRID = _log_grid(4.6758, fmath.pow(2, 1 / 8), 97)
_FINE = _log_grid(4.9182, fmath.pow(2, 1 / 48), 301)
OMNIMIC_GRID = _FINE + _log_grid(
    _FINE[-1] * fmath.pow(2, 1 / 24), fmath.pow(2, 1 / 24), 163
)
ANALOG_GRID = [20 * fmath.pow(1000, i / 255) for i in range(256)]

USB_SECTIONS = {
    "degrees_0": [hp2(12, 0.6), peak(8000, 1.5, 1.2)],
    "degrees_90": [hp2(12, 0.6), peak(8000, 1.5, 1.2), high_cut(5000, -6)],
}
OMNIMIC_SECTIONS = [hp2(6, 0.7), high_cut(15000, -4)]
ANALOG_SECTIONS = [hp2(15, 0.5), peak(12000, 3, 0.8)]

# name: (header lines, grid, sections, phase column, line end)
DAYTON = {
    "1600042.txt": (
        ["Sens Factor =-18.5dB, SERNO: 1600042"],
        USB_GRID,
        USB_SECTIONS["degrees_0"],
        True,
        "\r\n",
    ),
    "1600042_90deg.txt": (
        ["Sens Factor =-18.5dB, SERNO: 1600042"],
        USB_GRID,
        USB_SECTIONS["degrees_90"],
        True,
        "\r\n",
    ),
    "40k0042.omm": (
        ['"Sens Factor = -3.25dB, SERNO: 40k0042"'],
        OMNIMIC_GRID,
        OMNIMIC_SECTIONS,
        True,
        "\r\n",
    ),
    "99-00042.txt": (["*1000Hz\t-40.5", ""], ANALOG_GRID, ANALOG_SECTIONS, False, "\n"),
}


def write_dayton(name: str) -> bytes:
    """USB rows: ``f gain phase`` (4, 4, 2 decimals); analog rows: ``f<TAB>gain``
    (2, 1 decimals)."""
    header, grid, sections, phase, end = DAYTON[name]
    lines = list(header)
    for f in grid:
        db, degrees, _ = response(sections, f)
        if phase:
            lines.append(
                f"{_decimals(f, 4)} {_decimals(db, 4)} {_decimals(degrees, 2)}"
            )
        else:
            lines.append(f"{f:.2f}\t{fixed(db, 1)}")
    return (end.join(lines) + end).encode()


def files() -> dict[str, bytes]:
    return {f"{DAYTON_DIR}/{name}": write_dayton(name) for name in DAYTON}
