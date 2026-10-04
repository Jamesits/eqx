"""AutoEq CSV, measurements corrected by DRC, DRC filters -> TotalMix Room EQ
preset."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..rme import tmreq
from .base import Converter, Result
from .common import COLUMN_OPTION, fit_correction, stereo
from .to_autoeq import MEASUREMENTS, AutoeqToAutoeq
from .with_drc import (
    RUN_OPTIONS,
    DrcFilterOutput,
    DrcRun,
    MeasurementDrc,
    measurement_converters,
)


def tmreq_result(curves: list[tuple[str, list]], name: str, notes=()) -> Result:
    """Nine bells and the gain fitted to each (channel, points) on the
    standard grid within the Room EQ frequency range."""
    corrections, out = [], list(notes)
    for channel, points in curves:
        c, note = fit_correction(
            channel[0],
            points,
            tmreq.BANDS,
            tmreq.FREQUENCY_HZ,
            gain_db=tmreq.GAIN_DB,
            q=tmreq.Q,
        )
        corrections.append(c)
        out.append(f"{channel}: gain {c.gain_db:+.2f} dB, {note}")
    return Result(tmreq.write(corrections).encode("utf-8"), name, out)


def drc_result(stem: str, filters: list, notes: list[str]) -> Result:
    """One filter is both channels."""
    curves = [(channel, f.correction) for channel, f in filters]
    if len(curves) == 1:
        curves.append(("Right", curves[0][1]))
    return tmreq_result(curves, stem + ".tmreq", notes)


class MeasurementDrcToTmreq(MeasurementDrc):
    target = "tmreq"
    stereo = True

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        return drc_result(stem, filters, notes)


class AutoeqDrcToTmreq(MeasurementDrcToTmreq):
    """``AutoeqToTmreq`` with --drc-config."""

    source = "autoeq"
    reader = AutoeqToAutoeq
    description = ""


class AutoeqToTmreq(Converter):
    """The column is the EQ gain; nine bells and the channel gain are fitted to it.

    The fit runs on the standard grid within the Room EQ frequency range.
    With --drc-config the column is a measurement; the fit is to its DRC
    correction.
    """

    source = "autoeq"
    target = "tmreq"
    description = (
        "an EQ curve (inputs: both channels, or left then right) as a TotalMix "
        "Room EQ preset of nine bells; or measurements corrected by DRC"
    )
    options = (COLUMN_OPTION,) + RUN_OPTIONS
    inputs = 2

    def __init__(self, column: str = response.RAW, **drc):
        self.column = column
        self.drc = AutoeqDrcToTmreq(column=column, **drc) if DrcRun.of(**drc) else None

    def _convert(self, *paths: Path) -> Result:
        if self.drc is not None:
            return self.drc.convert(paths)
        return tmreq_result(stereo(paths, self.column), f"{paths[0].stem}.tmreq")


MEASUREMENTS_TO_TMREQ = measurement_converters(
    MeasurementDrcToTmreq,
    [r for r in MEASUREMENTS if r.source != "autoeq"],
    "Tmreq",
    "measurements corrected by DRC (--drc-config required) as a TotalMix Room "
    "EQ preset of nine bells",
)


class DrcToTmreq(DrcFilterOutput):
    target = "tmreq"
    description = (
        "DRC filters (inputs: both channels, or left then right) as a TotalMix "
        "Room EQ preset of nine bells"
    )
    stereo = True

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        return drc_result(stem, filters, notes)
