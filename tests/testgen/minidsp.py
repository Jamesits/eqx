"""miniDSP UMIK calibration files."""

from __future__ import annotations

from eqx import fmath

from .common import high_cut, hp2, peak, response

UMIK_DIR = "minidsp/umik"
# The UMIK grid: 615 log-spaced points, 10.054-20016.816 Hz.
UMIK_GRID = [10.054 * fmath.pow(20016.816 / 10.054, i / 614) for i in range(615)]
UMIK_SECTIONS = {
    "degrees_0": [hp2(14, 0.6), peak(9000, 1.5, 1.2)],
    "degrees_90": [hp2(14, 0.6), peak(9000, 1.5, 1.2), high_cut(6000, -6)],
}
UMIK = {
    # serial: (header, row format, trailing empty line) of the 0 degree file
    "7000042": ('"Sens Factor =-5.5dB, SERNO: 7000042"', "{:.3f}\t{:.4f}", False),
    "8100042": (
        '"Sens Factor =-14.5dB, AGain =18dB, SERNO: 8100042"',
        "{:.6g}\t{:.6g}",
        True,
    ),
}


def write_umik(serial: str, angle: str) -> bytes:
    """The 90 degree file has a second header line and 3/4 decimals."""
    header, row, empty_line = UMIK[serial]
    lines = [header]
    if angle == "degrees_90":
        lines.append('"Auto-generated 90-degree calibration file"')
        row, empty_line = "{:.3f}\t{:.4f}", False
    lines += [row.format(f, response(UMIK_SECTIONS[angle], f)[0]) for f in UMIK_GRID]
    return ("\r\n".join(lines) + "\r\n" + ("\r\n" if empty_line else "")).encode()


def files() -> dict[str, bytes]:
    return {
        f"{UMIK_DIR}/{serial}{suffix}.txt": write_umik(serial, angle)
        for serial in UMIK
        for angle, suffix in (("degrees_0", ""), ("degrees_90", "_90deg"))
    }
