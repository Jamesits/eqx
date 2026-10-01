"""AutoEq CSV -> TotalMix Room EQ preset."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..rme import tmreq
from .base import Converter, Result
from .common import COLUMN_OPTION, fit_correction, stereo


class AutoeqToTmreq(Converter):
    """The column is the EQ gain; nine bells and the channel gain are fitted to it.

    The fit runs on the standard grid within the Room EQ frequency range.
    """

    source = "autoeq"
    target = "tmreq"
    description = (
        "an EQ curve (inputs: both channels, or left then right) as a TotalMix "
        "Room EQ preset of nine bells"
    )
    options = (COLUMN_OPTION,)
    inputs = 2

    def __init__(self, column: str = response.RAW):
        self.column = column

    def _convert(self, *paths: Path) -> Result:
        corrections, notes = [], []
        for channel, points in stereo(paths, self.column):
            c, note = fit_correction(
                channel[0],
                points,
                tmreq.BANDS,
                tmreq.FREQUENCY_HZ,
                gain_db=tmreq.GAIN_DB,
                q=tmreq.Q,
            )
            corrections.append(c)
            notes.append(f"{channel}: gain {c.gain_db:+.2f} dB, {note}")
        return Result(
            tmreq.write(corrections).encode("utf-8"), f"{paths[0].stem}.tmreq", notes
        )
