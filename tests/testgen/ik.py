"""IK Multimedia ARC X sessions and analyses, ARC 4 analysis."""

from __future__ import annotations

import math

from eqx import dsp, fmath
from eqx.ik import arcx, pak
from eqx.wav import fir

from .common import SPEAKER, bells, impulse_response, subwoofer

# ---------------------------------------------------------------------------
# ARC X
# ---------------------------------------------------------------------------
ARCX_DIR = "ik/arcx"
# Level offset dB of each measurement point.
ARCX_POINT_GAINS = (0.0, -1.0, 2.0)
# Time of flight, samples, per speaker; other speakers: ARCX_OTHER_DELAY.
ARCX_DELAY = {"Left": 150, "Right": 160, "Subwoofer": 170}
ARCX_OTHER_DELAY = 180
# name: (layout id, points, sample rate, with session)
ARCX = {
    "Arc.arcXs": (1, 3, 48000, True),
    "Arc.arcXa": (1, 3, 48000, False),
    "Arc Sub.arcXs": (2, 1, 44100, True),
    "Arc 5.1.arcXs": (4, 1, 48000, True),
}


def arcx_filters(speaker: str, rate: float) -> list:
    """Left, Right: the device export bells; Subwoofer: a low pass; others: one bell."""
    if speaker == "Subwoofer":
        return subwoofer(rate)
    if speaker in SPEAKER:
        return bells(speaker, rate)
    position = next(n for n, name in arcx.POSITIONS.items() if name == speaker)
    return [dsp.bell(200.0 * position, 4.0, 1.0, rate)]


def write_arcx(layout: int, points: int, rate: int, session: bool) -> bytes:
    channels = []
    for position in arcx.LAYOUTS[layout][1]:
        speaker = arcx.POSITIONS[position]
        delay = ARCX_DELAY.get(speaker, ARCX_OTHER_DELAY)
        cc = impulse_response(arcx.IR_LENGTH, delay, 0.0, [])
        channels.append(
            [
                arcx.Point(
                    impulse_response(
                        arcx.IR_LENGTH,
                        delay,
                        ARCX_POINT_GAINS[p],
                        arcx_filters(speaker, rate),
                    ),
                    cc,
                )
                for p in range(points)
            ]
        )
    return arcx.write(rate, channels, layout, session)


# ---------------------------------------------------------------------------
# ARC 4
# ---------------------------------------------------------------------------
ARC4_DIR = "ik/arc4"
# ARC 4 Analysis's FFT size, fixed in the binary.
ARC4_FFT_SIZE = 32768
ARC4_RATE = 48000
ARC4_ID = "0123456789abcdef0123456789abcdef"
ARC4_STEPS = 2
# Recorded sweeps and tails: silent; the spectra are not rebuilt from them.
ARC4_RECORDING = [0.0] * 16


def arc4_spectrum(side: str, rate: float) -> list[float]:
    """Packed real FFT of the device export bells; mean power over 40 Hz-10 kHz is 1."""
    half = ARC4_FFT_SIZE // 2
    filters = bells(side, rate)
    magnitude = [
        fmath.pow(
            10, dsp.cascade_db(filters, max(k, 1e-3) * rate / ARC4_FFT_SIZE, rate) / 20
        )
        for k in range(half + 1)
    ]
    low, high = int(40 * ARC4_FFT_SIZE / rate), int(10000 * ARC4_FFT_SIZE / rate)
    scale = 1 / math.sqrt(
        math.fsum(m * m for m in magnitude[low : high + 1]) / (high - low + 1)
    )
    packed = [magnitude[0] * scale, magnitude[half] * scale]
    for k in range(1, half):
        packed += [magnitude[k] * scale, 0.0]
    return packed


def write_arc4(version: str = "4.0.0", sha: str = ARC4_ID) -> bytes:
    entries = {
        "info.xml": arcx.juce_xml(
            arcx.value_tree(
                "SerializedMeasure",
                [
                    ("Version", version),
                    ("SampleRate", f"{ARC4_RATE:.1f}"),
                    ("SelectedMicType", "MEMS"),
                    ("CorrectionSpeaker", ARC4_ID),
                    ("SHA", sha),
                ],
            )
        )
    }
    for c, side in enumerate(SPEAKER):
        entries[f"ch{c}.wav"] = fir.write(
            fir.Fir(ARC4_RATE, [arc4_spectrum(side, ARC4_RATE)])
        )
        for step in range(ARC4_STEPS):
            for name in (f"sweep0ch{c}", f"tailch{c}"):
                entries[f"step{step}/{name}.wav"] = fir.write(
                    fir.Fir(ARC4_RATE, [ARC4_RECORDING])
                )
    return pak.write(entries)


def files() -> dict[str, bytes]:
    out = {f"{ARCX_DIR}/{name}": write_arcx(*spec) for name, spec in ARCX.items()}
    out[f"{ARC4_DIR}/Arc4.arc4a"] = write_arc4()
    return out
