"""AutoEq CSV -> ARC X session or analysis."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..autoeq import response
from ..ik import arcx
from ..options import Option
from .base import Converter, Result
from .common import (
    COLUMN_OPTION,
    DEFAULT_RATE,
    RATE_OPTION,
    impulse_response,
    median_level,
    stereo,
)

# The impulse starts this many samples into the impulse response, as after a
# time of flight.  ARC X loads sessions with impulses 150 and 160 samples in.
LEAD = 150
# Minimum-phase filter length of the impulse response; the rest is silence.
TAPS = 16384


class AutoeqToArcx(Converter):
    """One measurement point per speaker: the minimum-phase impulse response of the curve.

    Both channels are shifted by one level: the median dB of 200 Hz-10 kHz
    becomes 0 dB re a full-scale impulse.
    """

    source = "autoeq"
    target = "arcx"
    description = (
        "speaker measurements (inputs: both speakers, or left then right) as an "
        "ARC X stereo session"
    )
    options = (
        COLUMN_OPTION,
        RATE_OPTION,
        Option(
            "--session",
            action=argparse.BooleanOptionalAction,
            help="write a session (.arcXs); --no-session writes an analysis (.arcXa) "
            "(default: on)",
        ),
    )
    inputs = 2

    def __init__(
        self,
        column: str = response.RAW,
        rate: float = DEFAULT_RATE,
        session: bool = True,
    ):
        if float(rate) not in arcx.SAMPLE_RATES:
            raise ValueError(
                f"the ARC X sample rate must be one of: "
                f"{', '.join(f'{r:g}' for r in arcx.SAMPLE_RATES)}"
            )
        self.column = column
        self.rate = float(rate)
        self.session = session

    def _convert(self, *paths: Path) -> Result:
        curves = stereo(paths, self.column)
        level = median_level(curves)
        impulse = [0.0] * arcx.IR_LENGTH
        impulse[LEAD] = 1.0
        channels = [
            [
                arcx.Point(
                    impulse_response(
                        points, level, self.rate, LEAD, arcx.IR_LENGTH, TAPS
                    ),
                    impulse,
                )
            ]
            for _, points in curves
        ]
        kind, suffix = ("session", ".arcXs") if self.session else ("analysis", ".arcXa")
        return Result(
            arcx.write(self.rate, channels, 1, self.session),
            f"{paths[0].stem}{suffix}",
            [
                (
                    f"Stereo {kind}, 1 point, {self.rate:g} Hz; "
                    f"{level:.2f} dB = 0 dB re full scale"
                )
            ],
        )
