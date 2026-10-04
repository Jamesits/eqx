"""AutoEq CSV, measurements corrected by DRC, DRC filters -> SoundSource
Headphone EQ profile."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..options import Option
from ..rogueamoeba import soundsource
from .base import Converter, Result
from .common import COLUMN_OPTION, fit_correction
from .to_autoeq import MEASUREMENTS, AutoeqToAutoeq
from .with_drc import (
    RUN_OPTIONS,
    DrcFilterOutput,
    DrcRun,
    MeasurementDrc,
    measurement_converters,
)

# AutoEq's parametric EQ uses ten filters.
FILTERS = 10
# SoundSource has no upper Q limit; this one keeps each bell several grid points wide.
MAX_Q = 9.9
FILTERS_OPTION = Option(
    "--filters",
    type=int,
    help=f"maximum number of bells, 1-{soundsource.MAX_FILTERS} (default: {FILTERS})",
)


def check_filters(filters: int) -> None:
    if not 1 <= filters <= soundsource.MAX_FILTERS:
        raise ValueError(f"--filters must be 1-{soundsource.MAX_FILTERS}")


def soundsource_result(points, filters: int, name: str, notes=()) -> Result:
    """Bells and the preamp fitted to ``points`` on the standard grid within
    SoundSource's frequency range."""
    c, note = fit_correction(
        soundsource.CHANNEL,
        points,
        filters,
        soundsource.FREQUENCY_HZ,
        q=(soundsource.MIN_Q, MAX_Q),
    )
    return Result(
        soundsource.write(c).encode("utf-8"),
        name,
        [*notes, f"preamp {c.gain_db:+.2f} dB, {note}"],
    )


class MeasurementDrcToSoundsource(MeasurementDrc):
    target = "soundsource"
    stereo = False
    output_options = (FILTERS_OPTION,)

    def setup(self, filters: int = FILTERS) -> None:
        check_filters(filters)
        self.filters = filters

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        ((_, f),) = filters
        return soundsource_result(f.correction, self.filters, stem + ".txt", notes)


class AutoeqDrcToSoundsource(MeasurementDrcToSoundsource):
    """``AutoeqToSoundsource`` with --drc-config."""

    source = "autoeq"
    reader = AutoeqToAutoeq
    description = ""


class AutoeqToSoundsource(Converter):
    """The column is the EQ gain; bells and the preamp are fitted to it.

    The fit runs on the standard grid within SoundSource's frequency range.
    One profile applies to both channels, so there is one input.
    With --drc-config the column is a measurement; the fit is to its DRC
    correction.
    """

    source = "autoeq"
    target = "soundsource"
    description = (
        "an EQ curve as a SoundSource Headphone EQ profile of bells; or a "
        "measurement corrected by DRC"
    )
    options = (COLUMN_OPTION, FILTERS_OPTION) + RUN_OPTIONS

    def __init__(self, column: str = response.RAW, filters: int = FILTERS, **drc):
        check_filters(filters)
        self.column = column
        self.filters = filters
        self.drc = (
            AutoeqDrcToSoundsource(column=column, filters=filters, **drc)
            if DrcRun.of(**drc)
            else None
        )

    def _convert(self, path: Path) -> Result:
        if self.drc is not None:
            return self.drc.convert([path])
        return soundsource_result(
            response.load(path).curve(self.column), self.filters, f"{path.stem}.txt"
        )


MEASUREMENTS_TO_SOUNDSOURCE = measurement_converters(
    MeasurementDrcToSoundsource,
    [r for r in MEASUREMENTS if r.source != "autoeq"],
    "Soundsource",
    "a measurement corrected by DRC (--drc-config required) as a SoundSource "
    "Headphone EQ profile of bells",
)


class DrcToSoundsource(DrcFilterOutput):
    target = "soundsource"
    description = "a DRC filter as a SoundSource Headphone EQ profile of bells"
    stereo = False
    output_options = (FILTERS_OPTION,)

    def setup(self, filters: int = FILTERS) -> None:
        check_filters(filters)
        self.filters = filters

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        ((_, f),) = filters
        return soundsource_result(f.correction, self.filters, stem + ".txt", notes)
