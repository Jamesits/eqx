"""AutoEq CSV -> Smaart target curve."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..options import Option
from ..rationalacoustics import crv
from .base import Converter, Result
from .common import COLUMN_OPTION, TOLERANCE_DB, TOLERANCE_OPTION, simplified


class AutoeqToSmaartCurve(Converter):
    """Points are the fewest standard grid points that follow the column
    within the tolerance."""

    source = "autoeq"
    target = "smaart-curve"
    description = (
        "a curve as a Smaart transfer function (or spectrum, --band) target curve"
    )
    options = (
        COLUMN_OPTION,
        TOLERANCE_OPTION,
        Option(
            "--band",
            type=int,
            choices=crv.BANDS,
            help="write a spectrum target curve of this banding, 1/n octave "
            "(default: a transfer function target curve)",
        ),
    )

    def __init__(
        self,
        column: str = response.RAW,
        tolerance_db: float = TOLERANCE_DB,
        band: int | None = None,
    ):
        if tolerance_db < 0:
            raise ValueError("--tolerance-db must be >= 0")
        self.column = column
        self.tolerance_db = tolerance_db
        self.band = band

    def _convert(self, path: Path) -> Result:
        points, error = simplified(
            response.load(path).curve(self.column), self.tolerance_db
        )
        kind = f"spectrum, 1/{self.band} octave" if self.band else "transfer function"
        return Result(
            crv.write(points, self.band).encode("utf-8"),
            f"{path.stem}.crv",
            [f"{kind} target curve; {len(points)} points; error {error:.2f} dB max"],
        )
