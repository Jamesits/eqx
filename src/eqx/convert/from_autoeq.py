"""AutoEq frequency response CSV -> REW calibration and measurement, SoundID project and
headphone profile, TotalMix Room EQ, ARC X session, SoundSource Headphone EQ profile,
Dirac Live target curve."""

from __future__ import annotations

import argparse
import math
import statistics
from pathlib import Path

from .. import dsp
from ..autoeq import response
from ..dirac import targetcurve
from ..ik import arcx
from ..model import Correction, Measurement, MicProfile, Peq
from ..options import Option
from ..rew import cal, mdat
from ..rme import tmreq
from ..rogueamoeba import soundsource
from ..soundid import layout, peqb
from .base import Converter, Result
from .mdat_swproj import (LEVEL_HIGH_HZ, LEVEL_LOW_HZ, SpeakerProjectConverter, interp, resample,
                          standard_grid)

COLUMN_OPTION = Option("--column", help=f"AutoEq CSV column to read (default: {response.RAW})")
DEFAULT_RATE = 48000.0
RATE_OPTION = Option("--rate", type=float, help="sample rate, Hz (default: 48000)")

# AutoEq's parametric EQ uses ten filters.
SOUNDSOURCE_FILTERS = 10
# SoundSource has no upper Q limit; this one keeps each bell several grid points wide.
SOUNDSOURCE_MAX_Q = 9.9

# Downloaded average profiles use this error band.
ERROR_BAND_DB = 3.0
REFERENCE_HZ = 1000.0


def _resample(points, grid: list[float]) -> list[float]:
    return resample([f for f, _ in points], [v for _, v in points], grid)


def _log_resample(points, grid: list[float]) -> list[float]:
    """Linear in log frequency; flat beyond the ends."""
    return resample([math.log(f) for f, _ in points], [v for _, v in points],
                    [math.log(f) for f in grid])


def _stereo(paths: tuple[Path, ...], column: str) -> list[tuple[str, list]]:
    """Left and Right; one input is both."""
    curves = _curves(paths, column)
    return curves if len(curves) == 2 else [curves[0], ("Right", curves[0][1])]


def _curves(paths: tuple[Path, ...], column: str) -> list[tuple[str, list]]:
    """[(channel, points)]: the first input as Left, the second as Right."""
    return [(channel, response.load(path).curve(column))
            for channel, path in zip(("Left", "Right"), paths)]


class AutoeqToRewcal(Converter):
    source = "autoeq"
    target = "rewcal"
    description = "one CSV column as a REW calibration file"
    options = (COLUMN_OPTION,)

    def __init__(self, column: str = response.RAW):
        self.column = column

    def _convert(self, path: Path) -> Result:
        points = response.load(path).curve(self.column)
        profile = MicProfile.from_points(path.stem, "", points)
        return Result(cal.write(profile, "AutoEq").encode("utf-8"), f"{path.stem}.txt",
                      [f"{len(points)} points, {points[0][0]:g}-{points[-1][0]:g} Hz"])


class AutoeqToSwproj(SpeakerProjectConverter):
    """Group delay is not in the CSV, so it is 0 and there is no group-delay correction."""

    source = "autoeq"
    description = ("speaker measurements (inputs: left, optional right) as a SoundID "
                   "speaker project")
    options = SpeakerProjectConverter.options + (COLUMN_OPTION,)
    inputs = 2

    def __init__(self, column: str = response.RAW, **settings):
        super().__init__(**settings)
        self.column = column

    def measurements(self, *paths: Path) -> tuple[layout.Layout, list[Measurement]]:
        return layout.STEREO, [
            Measurement(channel, index, [f for f, _ in points], [v for _, v in points],
                        [0.0] * len(points), name=f"{channel} {paths[0].stem}")
            for index, (channel, points) in enumerate(_curves(paths, self.column))
        ]


class AutoeqToPeqb(Converter):
    """Correction = target - response + reference, on the standard grid.

    Not capped (SoundID caps on playback) and no group delay.  SoundID's own
    profiles correct towards its own target; an uncompensated measurement
    needs ``target_curve``.
    """

    source = "autoeq"
    target = "peqb"
    description = ("headphone measurement (inputs: both sides, or left then right) as an "
                   "unencrypted .swhp headphone profile")
    inputs = 2
    options = (
        COLUMN_OPTION,
        Option("--make", help="headphone manufacturer (default: AutoEq)"),
        Option("--model", help="headphone model (default: the input file name)"),
        Option("--target-curve", type=Path, metavar="CSV",
               help="AutoEq target CSV (raw column); the correction is target - response "
                    "(default: 0 dB)"),
        Option("--reference-db", type=float,
               help="response - target level mapped to 0 dB "
                    f"(default: the mean {REFERENCE_HZ:g} Hz level)"),
    )

    def __init__(self, column: str = response.RAW,
                 make: str = "AutoEq", model: str | None = None,
                 target_curve: Path | None = None, reference_db: float | None = None):
        self.column = column
        self.make = make
        self.model = model
        self.target_curve = Path(target_curve) if target_curve is not None else None
        self.reference_db = reference_db

    def _convert(self, *paths: Path) -> Result:
        path = paths[0]
        grid = standard_grid()
        curves = dict(_curves(paths, self.column))
        curves.setdefault("Right", curves["Left"])
        target = [0.0] * len(grid)
        if self.target_curve is not None:
            target = _resample(response.load(self.target_curve).curve(), grid)
        deviation = {side: [v - t for v, t in zip(_resample(points, grid), target)]
                  for side, points in curves.items()}
        reference = self.reference_db
        if reference is None:
            reference = sum(interp(grid, v, REFERENCE_HZ) for v in deviation.values()) / len(deviation)
        # One reference for both sides keeps their balance.
        correction = {side: [(f, reference - v, 0.0) for f, v in zip(grid, values)]
                      for side, values in deviation.items()}
        model = self.model or path.stem
        data = peqb.write_v1(
            peqb.headphone_curves(correction["Left"], correction["Right"], ERROR_BAND_DB),
            peqb.headphone_parameters(self.make, model, True, ERROR_BAND_DB))
        notes = [f"{self.make} {model}; {len(grid)} points; reference: {reference:.2f} dB = 0 dB"]
        for side, c in correction.items():
            capped = sum(r > peqb.headphone_boost_cap(f) for f, r, _ in c)
            notes.append(f"{side} correction: {min(r for _, r, _ in c):+.2f} to "
                         f"{max(r for _, r, _ in c):+.2f} dB; "
                         f"{capped} points above SoundID's boost cap")
        return Result(data, f"{path.stem}.swhp", notes)


# REW measurement grid: the FFT bins of this length.
MDAT_FFT_LENGTH = 65536


class AutoeqToMdat(Converter):
    """On REW's linear grid within the CSV range; phase and group delay are 0."""

    source = "autoeq"
    target = "mdat"
    description = "measurements (inputs: left, optional right) as a REW measurement file"
    options = (COLUMN_OPTION, RATE_OPTION)
    inputs = 2

    def __init__(self, column: str = response.RAW, rate: float = DEFAULT_RATE):
        if not rate > 0 or rate != round(rate):
            raise ValueError("the sample rate must be a positive integer")
        self.column = column
        self.rate = int(rate)

    def _convert(self, *paths: Path) -> Result:
        step = self.rate / MDAT_FFT_LENGTH
        measurements, notes = [], []
        for (channel, points), path in zip(_curves(paths, self.column), paths):
            first = max(1, math.ceil(points[0][0] / step))
            last = min(MDAT_FFT_LENGTH // 2 - 1, math.floor(points[-1][0] / step))
            if last <= first:
                raise ValueError(f"{path.name}: its range holds fewer than two REW points")
            grid = [k * step for k in range(first, last + 1)]
            measurements.append(Measurement(
                channel, len(measurements), grid, _log_resample(points, grid), [0.0] * len(grid),
                sample_rate=self.rate, name=f"{channel[0]} {path.stem}", source_file=path.name,
                source_format="AutoEq CSV"))
            notes.append(f"{channel}: {len(grid)} points, {grid[0]:g}-{grid[-1]:g} Hz")
        return Result(mdat.write(measurements), f"{paths[0].stem}.mdat", notes)


class AutoeqToTmreq(Converter):
    """The column is the EQ gain; nine bells and the channel gain are fitted to it.

    The fit runs on the standard grid within the Room EQ frequency range.
    """

    source = "autoeq"
    target = "tmreq"
    description = ("an EQ curve (inputs: both channels, or left then right) as a TotalMix "
                   "Room EQ preset of nine bells")
    options = (COLUMN_OPTION,)
    inputs = 2

    def __init__(self, column: str = response.RAW):
        self.column = column

    def _convert(self, *paths: Path) -> Result:
        low, high = tmreq.FREQUENCY_HZ
        grid = [f for f in standard_grid() if low <= f <= high]
        corrections, notes = [], []
        for channel, points in _stereo(paths, self.column):
            fit = dsp.fit_bells(grid, _log_resample(points, grid), tmreq.BANDS,
                                frequency_hz=tmreq.FREQUENCY_HZ, gain_db=tmreq.GAIN_DB,
                                q=tmreq.Q)
            corrections.append(Correction(channel[0], fit.gain_db,
                                          peqs=[Peq(f, g, q) for f, g, q in fit.bells]))
            notes.append(f"{channel}: gain {fit.gain_db:+.2f} dB, {len(fit.bells)} bells; "
                         f"error {fit.rms_db:.2f} dB RMS, {fit.max_db:.2f} dB max")
        return Result(tmreq.write(corrections).encode("utf-8"), f"{paths[0].stem}.tmreq", notes)


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
        Option("--filters", type=int,
               help=f"maximum number of bells, 1-{soundsource.MAX_FILTERS} "
                    f"(default: {SOUNDSOURCE_FILTERS})"),
    )

    def __init__(self, column: str = response.RAW, filters: int = SOUNDSOURCE_FILTERS):
        if not 1 <= filters <= soundsource.MAX_FILTERS:
            raise ValueError(f"--filters must be 1-{soundsource.MAX_FILTERS}")
        self.column = column
        self.filters = filters

    def _convert(self, path: Path) -> Result:
        low, high = soundsource.FREQUENCY_HZ
        grid = [f for f in standard_grid() if low <= f <= high]
        points = response.load(path).curve(self.column)
        fit = dsp.fit_bells(grid, _log_resample(points, grid), self.filters,
                            frequency_hz=soundsource.FREQUENCY_HZ,
                            q=(soundsource.MIN_Q, SOUNDSOURCE_MAX_Q))
        c = Correction(soundsource.CHANNEL, fit.gain_db,
                       peqs=[Peq(f, g, q) for f, g, q in fit.bells])
        return Result(soundsource.write(c).encode("utf-8"), f"{path.stem}.txt",
                      [f"preamp {fit.gain_db:+.2f} dB, {len(fit.bells)} bells; "
                       f"error {fit.rms_db:.2f} dB RMS, {fit.max_db:.2f} dB max"])


# The impulse starts this many samples into the impulse response, as after a
# time of flight.  ARC X loads sessions with impulses 150 and 160 samples in.
ARCX_LEAD = 150
# Minimum-phase filter length of the impulse response; the rest is silence.
ARCX_TAPS = 16384


class AutoeqToArcx(Converter):
    """One measurement point per speaker: the minimum-phase impulse response of the curve.

    Both channels are shifted by one level: the median dB of 200 Hz-10 kHz
    becomes 0 dB re a full-scale impulse.
    """

    source = "autoeq"
    target = "arcx"
    description = ("speaker measurements (inputs: both speakers, or left then right) as an "
                   "ARC X stereo session")
    options = (
        COLUMN_OPTION, RATE_OPTION,
        Option("--session", action=argparse.BooleanOptionalAction,
               help="write a session (.arcXs); --no-session writes an analysis (.arcXa) "
                    "(default: on)"),
    )
    inputs = 2

    def __init__(self, column: str = response.RAW, rate: float = DEFAULT_RATE,
                 session: bool = True):
        if float(rate) not in arcx.SAMPLE_RATES:
            raise ValueError(f"the ARC X sample rate must be one of: "
                             f"{', '.join(f'{r:g}' for r in arcx.SAMPLE_RATES)}")
        self.column = column
        self.rate = float(rate)
        self.session = session

    def _convert(self, *paths: Path) -> Result:
        curves = _stereo(paths, self.column)
        band = [f for f in standard_grid() if LEVEL_LOW_HZ <= f <= LEVEL_HIGH_HZ]
        level = statistics.median(v for _, points in curves for v in _log_resample(points, band))
        channels = []
        for _, points in curves:
            ir = dsp.design_fir([f for f, _ in points], [v - level for _, v in points],
                                self.rate, "minimum", ARCX_TAPS)
            impulse = [0.0] * arcx.IR_LENGTH
            impulse[ARCX_LEAD] = 1.0
            channels.append([arcx.Point(
                [0.0] * ARCX_LEAD + ir + [0.0] * (arcx.IR_LENGTH - ARCX_LEAD - len(ir)), impulse)])
        kind, suffix = ("session", ".arcXs") if self.session else ("analysis", ".arcXa")
        return Result(arcx.write(self.rate, channels, 1, self.session),
                      f"{paths[0].stem}{suffix}",
                      [f"Stereo {kind}, 1 point, {self.rate:g} Hz; "
                       f"{level:.2f} dB = 0 dB re full scale"])


TARGETCURVE_TOLERANCE_DB = 0.1


class AutoeqToTargetcurve(Converter):
    """The column is the target; breakpoints are the fewest standard grid
    points that follow it within the tolerance."""

    source = "autoeq"
    target = "targetcurve"
    description = "a curve as a Dirac Live target curve"
    options = (
        COLUMN_OPTION,
        Option("--tolerance-db", type=float,
               help=f"largest deviation from the curve, dB "
                    f"(default: {TARGETCURVE_TOLERANCE_DB:g})"),
        Option("--low-hz", type=float,
               help=f"low correction limit, Hz (default: {targetcurve.DEFAULT_LOW_HZ:g})"),
        Option("--high-hz", type=float,
               help=f"high correction limit, Hz (default: {targetcurve.DEFAULT_HIGH_HZ:g})"),
    )

    def __init__(self, column: str = response.RAW,
                 tolerance_db: float = TARGETCURVE_TOLERANCE_DB,
                 low_hz: float = targetcurve.DEFAULT_LOW_HZ,
                 high_hz: float = targetcurve.DEFAULT_HIGH_HZ):
        if tolerance_db < 0:
            raise ValueError("--tolerance-db must be >= 0")
        self.column = column
        self.tolerance_db = tolerance_db
        self.low_hz, self.high_hz = low_hz, high_hz

    def _convert(self, path: Path) -> Result:
        grid = standard_grid()
        points = list(zip(grid, _log_resample(response.load(path).curve(self.column), grid)))
        breakpoints = targetcurve.simplify(points, self.tolerance_db)
        curve = targetcurve.TargetCurve(path.stem, "", breakpoints, self.low_hz, self.high_hz)
        error = max(abs(a - v) for a, (_, v) in zip(curve.response(grid), points))
        return Result(targetcurve.write(curve).encode("utf-8"), f"{path.stem}.targetcurve",
                      [f"{len(breakpoints)} breakpoints; error {error:.2f} dB max"])
