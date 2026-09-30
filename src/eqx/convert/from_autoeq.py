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
RIGHT_OPTION = Option("--right", type=Path, metavar="CSV",
                      help="AutoEq CSV of the right channel; the input is the left channel")

# Downloaded average profiles use this error band.
ERROR_BAND_DB = 3.0
REFERENCE_HZ = 1000.0


def _resample(points, grid: list[float]) -> list[float]:
    return resample([f for f, _ in points], [v for _, v in points], grid)


def _curves(path: Path, right: Path | None, column: str) -> list[tuple[str, list]]:
    """[(channel, points)]: the input as Left, then ``right`` as Right."""
    curves = [("Left", response.load(path).curve(column))]
    if right is not None:
        curves.append(("Right", response.load(right).curve(column)))
    return curves


class AutoeqToRewcal(Converter):
    source = "autoeq"
    target = "rewcal"
    description = "one CSV column as a REW calibration file"
    options = (COLUMN_OPTION,)

    def __init__(self, column: str = response.RAW):
        self.column = column

    def convert(self, path: Path) -> Result:
        path = Path(path)
        points = response.load(path).curve(self.column)
        profile = MicProfile.from_points(path.stem, "", points)
        return Result(cal.write(profile, "AutoEq").encode("utf-8"), f"{path.stem}.txt",
                      [f"{len(points)} points, {points[0][0]:g}-{points[-1][0]:g} Hz"])


class AutoeqToSwproj(SpeakerProjectConverter):
    """Group delay is not in the CSV, so it is 0 and there is no group-delay correction."""

    source = "autoeq"
    description = ("speaker measurements (input: left, --right: right) as a SoundID "
                   "speaker project")
    options = SpeakerProjectConverter.options + (COLUMN_OPTION, RIGHT_OPTION)

    def __init__(self, column: str = response.RAW, right: Path | None = None, **settings):
        super().__init__(**settings)
        self.column = column
        self.right = Path(right) if right is not None else None

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        return layout.STEREO, [
            Measurement(channel, index, [f for f, _ in points], [v for _, v in points],
                        [0.0] * len(points), name=f"{channel} {path.stem}")
            for index, (channel, points) in enumerate(_curves(path, self.right, self.column))
        ]


class AutoeqToPeqb(Converter):
    """Correction = target - response + reference, on the standard grid.

    Not capped (SoundID caps on playback) and no group delay.  SoundID's own
    profiles correct towards its own target; an uncompensated measurement
    needs ``target_curve``.
    """

    source = "autoeq"
    target = "peqb"
    description = ("headphone measurement (input: left and right, or --right) as an "
                   "unencrypted .swhp headphone profile")
    options = (
        COLUMN_OPTION,
        RIGHT_OPTION,
        Option("--make", help="headphone manufacturer (default: AutoEq)"),
        Option("--model", help="headphone model (default: the input file name)"),
        Option("--target-curve", type=Path, metavar="CSV",
               help="AutoEq target CSV (raw column); the correction is target - response "
                    "(default: 0 dB)"),
        Option("--reference-db", type=float,
               help="response - target level mapped to 0 dB "
                    f"(default: the mean {REFERENCE_HZ:g} Hz level)"),
    )

    def __init__(self, column: str = response.RAW, right: Path | None = None,
                 make: str = "AutoEq", model: str | None = None,
                 target_curve: Path | None = None, reference_db: float | None = None):
        self.column = column
        self.right = Path(right) if right is not None else None
        self.make = make
        self.model = model
        self.target_curve = Path(target_curve) if target_curve is not None else None
        self.reference_db = reference_db

    def convert(self, path: Path) -> Result:
        path = Path(path)
        grid = standard_grid()
        curves = dict(_curves(path, self.right, self.column))
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
