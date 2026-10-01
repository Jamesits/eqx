"""AutoEq frequency response CSV -> REW calibration, SoundID project, SoundID headphone profile."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..model import Measurement, MicProfile
from ..options import Option
from ..rew import cal
from ..soundid import layout, peqb
from .base import Converter, Result
from .mdat_swproj import SpeakerProjectConverter, interp, resample, standard_grid

COLUMN_OPTION = Option("--column", help=f"AutoEq CSV column to read (default: {response.RAW})")

# Downloaded average profiles use this error band.
ERROR_BAND_DB = 3.0
REFERENCE_HZ = 1000.0


def _resample(points, grid: list[float]) -> list[float]:
    return resample([f for f, _ in points], [v for _, v in points], grid)


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
