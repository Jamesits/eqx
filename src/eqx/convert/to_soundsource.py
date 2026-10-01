"""AutoEq CSV -> SoundSource Headphone EQ profile."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..options import Option
from ..rogueamoeba import soundsource
from .base import Converter, Result
from .common import COLUMN_OPTION, fit_correction

# AutoEq's parametric EQ uses ten filters.
FILTERS = 10
# SoundSource has no upper Q limit; this one keeps each bell several grid points wide.
MAX_Q = 9.9


class AutoeqToSoundsource(Converter):
    """The column is the EQ gain; bells and the preamp are fitted to it.

    The fit runs on the standard grid within SoundSource's frequency range.
    One profile applies to both channels, so there is one input.
    """

    source = "autoeq"
    target = "soundsource"
    description = "an EQ curve as a SoundSource Headphone EQ profile of bells"
    options = (
        COLUMN_OPTION,
        Option(
            "--filters",
            type=int,
            help=f"maximum number of bells, 1-{soundsource.MAX_FILTERS} "
            f"(default: {FILTERS})",
        ),
    )

    def __init__(self, column: str = response.RAW, filters: int = FILTERS):
        if not 1 <= filters <= soundsource.MAX_FILTERS:
            raise ValueError(f"--filters must be 1-{soundsource.MAX_FILTERS}")
        self.column = column
        self.filters = filters

    def _convert(self, path: Path) -> Result:
        c, note = fit_correction(
            soundsource.CHANNEL,
            response.load(path).curve(self.column),
            self.filters,
            soundsource.FREQUENCY_HZ,
            q=(soundsource.MIN_Q, MAX_Q),
        )
        return Result(
            soundsource.write(c).encode("utf-8"),
            f"{path.stem}.txt",
            [f"preamp {c.gain_db:+.2f} dB, {note}"],
        )
