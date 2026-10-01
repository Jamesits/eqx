"""AutoEq CSV -> Dirac Live target curve."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..curve import log_resample
from ..dirac import targetcurve
from ..model import standard_grid
from ..options import Option
from .base import Converter, Result
from .common import COLUMN_OPTION

TOLERANCE_DB = 0.1


class AutoeqToTargetcurve(Converter):
    """The column is the target; breakpoints are the fewest standard grid
    points that follow it within the tolerance."""

    source = "autoeq"
    target = "targetcurve"
    description = "a curve as a Dirac Live target curve"
    options = (
        COLUMN_OPTION,
        Option("--tolerance-db", type=float,
               help=f"largest deviation from the curve, dB (default: {TOLERANCE_DB:g})"),
        Option("--low-hz", type=float,
               help=f"low correction limit, Hz (default: {targetcurve.DEFAULT_LOW_HZ:g})"),
        Option("--high-hz", type=float,
               help=f"high correction limit, Hz (default: {targetcurve.DEFAULT_HIGH_HZ:g})"),
    )

    def __init__(self, column: str = response.RAW,
                 tolerance_db: float = TOLERANCE_DB,
                 low_hz: float = targetcurve.DEFAULT_LOW_HZ,
                 high_hz: float = targetcurve.DEFAULT_HIGH_HZ):
        if tolerance_db < 0:
            raise ValueError("--tolerance-db must be >= 0")
        self.column = column
        self.tolerance_db = tolerance_db
        self.low_hz, self.high_hz = low_hz, high_hz

    def _convert(self, path: Path) -> Result:
        grid = standard_grid()
        points = list(zip(grid, log_resample(response.load(path).curve(self.column), grid)))
        breakpoints = targetcurve.simplify(points, self.tolerance_db)
        curve = targetcurve.TargetCurve(path.stem, "", breakpoints, self.low_hz, self.high_hz)
        error = max(abs(a - v) for a, (_, v) in zip(curve.response(grid), points))
        return Result(targetcurve.write(curve).encode("utf-8"), f"{path.stem}.targetcurve",
                      [f"{len(breakpoints)} breakpoints; error {error:.2f} dB max"])
