"""AutoEq CSV -> REW measurement file."""

from __future__ import annotations

import math
from pathlib import Path

from ..autoeq import response
from ..curve import log_resample
from ..model import Measurement
from ..rew import mdat
from .base import Converter, Result
from .common import COLUMN_OPTION, DEFAULT_RATE, RATE_OPTION, curves

# REW measurement grid: the FFT bins of this length.
FFT_LENGTH = 65536


class AutoeqToMdat(Converter):
    """On REW's linear grid within the CSV range; phase and group delay are 0."""

    source = "autoeq"
    target = "mdat"
    description = (
        "measurements (inputs: left, optional right) as a REW measurement file"
    )
    options = (COLUMN_OPTION, RATE_OPTION)
    inputs = 2

    def __init__(self, column: str = response.RAW, rate: float = DEFAULT_RATE):
        if not rate > 0 or rate != round(rate):
            raise ValueError("the sample rate must be a positive integer")
        self.column = column
        self.rate = int(rate)

    def _convert(self, *paths: Path) -> Result:
        step = self.rate / FFT_LENGTH
        measurements, notes = [], []
        for (channel, points), path in zip(curves(paths, self.column), paths):
            first = max(1, math.ceil(points[0][0] / step))
            last = min(FFT_LENGTH // 2 - 1, math.floor(points[-1][0] / step))
            if last <= first:
                raise ValueError(
                    f"{path.name}: its range holds fewer than two REW points"
                )
            grid = [k * step for k in range(first, last + 1)]
            measurements.append(
                Measurement(
                    channel,
                    len(measurements),
                    grid,
                    log_resample(points, grid),
                    [0.0] * len(grid),
                    sample_rate=self.rate,
                    name=f"{channel[0]} {path.stem}",
                    source_file=path.name,
                    source_format="AutoEq CSV",
                )
            )
            notes.append(f"{channel}: {len(grid)} points, {grid[0]:g}-{grid[-1]:g} Hz")
        return Result(mdat.write(measurements), f"{paths[0].stem}.mdat", notes)
