"""REW measurements (``.mdat``) and a calibration file with a sensitivity line."""

from __future__ import annotations

from eqx.model import Measurement
from eqx.rew import mdat
from eqx.soundid import swmicpkg

from .common import high_cut, hp2, lp2, peak, response
from .soundid import MICS

MDAT_DIR, CAL_DIR = "rew/mdat", "rew/rewcal"


def write_mdat(
    channels, sample_rate: int, fft_length: int, rew: tuple[int, int]
) -> bytes:
    """``channels``: [(short description, level dB SPL, sections)]."""
    n = fft_length // 2 - 1
    step = sample_rate / fft_length
    freqs = [step * (i + 1) for i in range(n)]
    measurements, phases = [], []
    for index, (desc, level, sections) in enumerate(channels):
        points = [response(sections, f) for f in freqs]
        measurements.append(
            Measurement(
                "Right" if desc.startswith("R") else "Left",
                index,
                freqs,
                [level + p[0] for p in points],
                [p[2] for p in points],
                sample_rate=sample_rate,
                name=desc,
                source_format="Synthetic",
            )
        )
        phases.append([p[1] for p in points])
    return mdat.write(measurements, phases, rew)


MDAT = {
    # name: (channels, sample rate, FFT length, REW version)
    "Flat": ([("L Flat", 85.0, []), ("R Flat", 85.0, [])], 48000, 16384, (5, 31)),
    "Bandpass": (
        [
            ("L Bandpass", 88.0, [hp2(55, 0.71), lp2(18000, 0.71), peak(2500, 3, 2)]),
            ("R Bandpass", 87.0, [hp2(55, 0.71), lp2(18000, 0.71), peak(2500, 3, 2)]),
        ],
        48000,
        16384,
        (5, 40),
    ),
    "Room": (
        [
            (
                "L Room",
                83.0,
                [
                    hp2(35, 0.8),
                    peak(45, 8, 5),
                    peak(120, -15, 8),
                    peak(240, -6, 6),
                    high_cut(4000, -6),
                ],
            ),
            (
                "R Room",
                81.0,
                [hp2(35, 0.8), peak(52, 6, 4), peak(150, -16, 6), high_cut(4000, -6)],
            ),
        ],
        44100,
        16384,
        (5, 31),
    ),
    "Left only": (
        [("L Left only", 90.0, [hp2(45, 0.71), high_cut(2000, -3)])],
        48000,
        16384,
        (5, 40),
    ),
}


def write_cal_with_sensitivity(sections) -> bytes:
    """REW cal variant: quoted sensitivity line, three columns (phase 0)."""
    lines = ['"Sens Factor =-1.5dB, SERNO: TILT01"']
    lines += [f"{f:.3f} {response(sections, f)[0]:.3f} 0.000" for f in swmicpkg.grid()]
    return ("\r\n".join(lines) + "\r\n").encode()


def files() -> dict[str, bytes]:
    out = {f"{MDAT_DIR}/{name}.mdat": write_mdat(*spec) for name, spec in MDAT.items()}
    out[f"{CAL_DIR}/TILT01 sensitivity.cal"] = write_cal_with_sensitivity(
        MICS["TILT01"]["degrees_0"]
    )
    return out
