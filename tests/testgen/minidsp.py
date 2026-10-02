"""miniDSP UMIK calibration files."""

from __future__ import annotations

from eqx import fmath

from .common import high_cut, hp2, peak, response

UMIK_DIR = "minidsp/umik"


def _log_grid(low: float, high: float, points: int) -> list[float]:
    return [low * fmath.pow(high / low, i / (points - 1)) for i in range(points)]


# The UMIK grids: 615 points, 10.054-20016.816 Hz (1/56 octave); the first
# serials: 80 points, 20.396-19152.066 Hz (1/8 octave); some serials: 133
# points, 10-20000 Hz (1/12 octave).
UMIK_GRID = _log_grid(10.054, 20016.816, 615)
UMIK_GRID_8 = _log_grid(20.396, 19152.066, 80)
UMIK_GRID_12 = [float(f"{f:.3g}") for f in _log_grid(10, 20000, 133)]
UMIK_SECTIONS = {
    "degrees_0": [hp2(14, 0.6), peak(9000, 1.5, 1.2)],
    "degrees_90": [hp2(14, 0.6), peak(9000, 1.5, 1.2), high_cut(6000, -6)],
}
UMIK = {
    # serial: (header, grid, row format, trailing empty line, 90 degree file)
    "7000042": (
        '"Sens Factor =-5.5dB, SERNO: 7000042"',
        UMIK_GRID,
        "{:.3f}\t{:.4f}",
        False,
        True,
    ),
    "8100042": (
        '"Sens Factor =-14.5dB, AGain =18dB, SERNO: 8100042"',
        UMIK_GRID,
        "{:.6g}\t{:.6g}",
        True,
        True,
    ),
    "7000007": (
        '"Sens Factor =-16.25dB, SERNO: 7000007"',
        UMIK_GRID_8,
        "{:.3f}\t{:.9f}",
        False,
        False,
    ),
    "7060042": (
        "Sens Factor =-.75dB, SERNO: 7060042",
        UMIK_GRID_12,
        "{:g}\t{:.2f}",
        True,
        False,
    ),
    "7080042": (
        '"Sens Factor =2.25dB, AGain =18dB, SERNO: 7080042"',
        UMIK_GRID,
        "{:.3f}\t{:.4f}",
        True,
        False,
    ),
}
# A serial without calibration data: the download is an error message.
NO_DATA = {
    "7085042": '<p style="color:red;">Unable to locate calibration data. Please '
    'contact <a href="https://minidsp.desk.com">miniDSP support</a>.</p>'
}


def write_umik(serial: str, angle: str) -> bytes:
    """The 90 degree file has a second header line and 3/4 decimals."""
    header, grid, row, empty_line, _ = UMIK[serial]
    lines = [header]
    if angle == "degrees_90":
        lines.append('"Auto-generated 90-degree calibration file"')
        row, empty_line = "{:.3f}\t{:.4f}", False
    lines += [row.format(f, response(UMIK_SECTIONS[angle], f)[0]) for f in grid]
    return ("\r\n".join(lines) + "\r\n" + ("\r\n" if empty_line else "")).encode()


def files() -> dict[str, bytes]:
    return {
        **{
            f"{UMIK_DIR}/{serial}{suffix}.txt": write_umik(serial, angle)
            for serial, (*_, side) in UMIK.items()
            for angle, suffix in (("degrees_0", ""), ("degrees_90", "_90deg"))
            if side or angle == "degrees_0"
        },
        **{
            f"{UMIK_DIR}/{serial}.txt": text.encode()
            for serial, text in NO_DATA.items()
        },
    }
