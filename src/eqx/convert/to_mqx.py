"""AutoEq CSV -> MultEQ-X project."""

from __future__ import annotations

from pathlib import Path

from ..audyssey import mqx
from ..autoeq import response
from .base import Converter, Result
from .common import (
    COLUMN_OPTION,
    impulse_response,
    median_level,
    mic_response_db,
    profile_db,
    stereo,
)

# Time of flight of the written measurements: 3 m.
FLIGHT = 420


class AutoeqToMqx(Converter):
    """One position per speaker: the minimum-phase impulse response of the curve.

    Both channels are shifted by one level: the median dB of 200 Hz-10 kHz
    becomes 0 dB re a full-scale impulse.  The microphone response is added,
    since MultEQ-X subtracts it.
    """

    source = "autoeq"
    target = "mqx"
    description = (
        "speaker measurements (inputs: both speakers, or left then right) as a "
        "MultEQ-X project"
    )
    options = (COLUMN_OPTION, mqx.MIC_RESPONSE_OPTION)
    inputs = 2

    def __init__(self, column: str = response.RAW, mic_response: Path | None = None):
        self.column = column
        self.mic_response = mic_response

    def mic_db(self, frequencies: list[float]) -> list[float]:
        if self.mic_response is None:
            return profile_db(mqx.generic_mic(), frequencies)
        return mic_response_db(self.mic_response, frequencies)

    def _convert(self, *paths: Path) -> Result:
        curves = stereo(paths, self.column)
        curves = [
            (
                name,
                [
                    (f, v + g)
                    for (f, v), g in zip(points, self.mic_db([f for f, _ in points]))
                ],
            )
            for name, points in curves
        ]
        level = median_level(curves)
        lead = mqx.SYSTEM_DELAY + FLIGHT
        channels = [
            (
                designation,
                [impulse_response(points, level, mqx.SAMPLE_RATE, lead, mqx.IR_LENGTH)],
            )
            for designation, (_, points) in zip(("FL", "FR"), curves)
        ]
        return Result(
            mqx.write(channels),
            f"{paths[0].stem}.mqx",
            [
                (
                    f"FL, FR, 1 position, {mqx.SAMPLE_RATE:g} Hz; "
                    f"{level:.2f} dB = 0 dB re full scale"
                )
            ],
        )
