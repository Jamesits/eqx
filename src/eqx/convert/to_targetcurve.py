"""AutoEq CSV -> Dirac Live target curve."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..dirac import targetcurve
from ..options import Option
from .base import Converter, Result
from .common import COLUMN_OPTION, TOLERANCE_DB, TOLERANCE_OPTION, simplified


class AutoeqToTargetcurve(Converter):
    """The column is the target; breakpoints are the fewest standard grid
    points that follow it within the tolerance."""

    source = "autoeq"
    target = "targetcurve"
    description = "a curve as a Dirac Live target curve"
    options = (
        COLUMN_OPTION,
        TOLERANCE_OPTION,
        Option(
            "--low-hz",
            type=float,
            help=f"low correction limit, Hz (default: {targetcurve.DEFAULT_LOW_HZ:g})",
        ),
        Option(
            "--high-hz",
            type=float,
            help=f"high correction limit, Hz (default: {targetcurve.DEFAULT_HIGH_HZ:g})",
        ),
    )

    def __init__(
        self,
        column: str = response.RAW,
        tolerance_db: float = TOLERANCE_DB,
        low_hz: float = targetcurve.DEFAULT_LOW_HZ,
        high_hz: float = targetcurve.DEFAULT_HIGH_HZ,
    ):
        if tolerance_db < 0:
            raise ValueError("--tolerance-db must be >= 0")
        self.column = column
        self.tolerance_db = tolerance_db
        self.low_hz, self.high_hz = low_hz, high_hz

    def _convert(self, path: Path) -> Result:
        breakpoints, error = simplified(
            response.load(path).curve(self.column), self.tolerance_db
        )
        curve = targetcurve.TargetCurve(
            path.stem, "", breakpoints, self.low_hz, self.high_hz
        )
        return Result(
            targetcurve.write(curve).encode("utf-8"),
            f"{path.stem}.targetcurve",
            [f"{len(breakpoints)} breakpoints; error {error:.2f} dB max"],
        )
