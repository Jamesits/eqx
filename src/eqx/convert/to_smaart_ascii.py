"""AutoEq CSV -> Smaart Import ASCII table."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..fileformat import frequency_range
from ..rationalacoustics import ascii
from .base import Converter, Result
from .common import COLUMN_OPTION


class AutoeqToSmaartAscii(Converter):
    source = "autoeq"
    target = "smaart-ascii"
    description = "a curve as a Smaart Import ASCII table"
    options = (COLUMN_OPTION,)

    def __init__(self, column: str = response.RAW):
        self.column = column

    def _convert(self, path: Path) -> Result:
        points = response.load(path).curve(self.column)
        return Result(ascii.write(points, path.stem).encode("utf-8"), f"{path.stem}.txt",
                      [f"{len(points)} points, {frequency_range(points)}"])
