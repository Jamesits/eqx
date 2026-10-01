"""AutoEq CSV -> FuzzMeasure 4 document."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..rode import fuzzmeasure
from .base import Converter, Result
from .common import (
    COLUMN_OPTION,
    DEFAULT_RATE,
    RATE_OPTION,
    impulse_response,
    median_level,
)

# Time of flight of the written measurements, m.
FLIGHT_M = 3.0
INPUTS = 16


class AutoeqToFuzzmeasure(Converter):
    """One measurement per input: the minimum-phase impulse response of the curve.

    All measurements are shifted by one level: the median dB of 200 Hz-10 kHz
    becomes 0 dB re a full-scale impulse.  The SPL reference level puts
    FuzzMeasure's SPL graphs at the curves' own dB values.
    """

    source = "autoeq"
    target = "fuzzmeasure"
    description = "curves (one measurement per input) as a FuzzMeasure 4 document"
    options = (COLUMN_OPTION, RATE_OPTION)
    inputs = INPUTS

    def __init__(self, column: str = response.RAW, rate: float = DEFAULT_RATE):
        if rate <= 0 or rate != int(rate):
            raise ValueError("--rate must be a whole number of Hz")
        self.column = column
        self.rate = int(rate)

    def _convert(self, *paths: Path) -> Result:
        curves = [(path.stem, response.load(path).curve(self.column)) for path in paths]
        titles = [title for title, _ in curves]
        if len({t.lower() for t in titles}) != len(titles):
            raise ValueError(f"inputs need distinct names; got {', '.join(titles)}")
        level = median_level(curves)
        lead = round(FLIGHT_M / fuzzmeasure.SPEED_OF_SOUND * self.rate)
        half = fuzzmeasure.IR_LENGTH // 2
        records = []
        for i, (path, (title, points)) in enumerate(zip(paths, curves)):
            records.append(
                fuzzmeasure.Record(
                    title,
                    self.rate,
                    impulse_response(
                        points,
                        level,
                        self.rate,
                        lead,
                        fuzzmeasure.IR_LENGTH,
                        half - lead,
                    ),
                    fuzzmeasure.record_uuid(f"measurement/{i}/{title}"),
                    window=(0, half, 0),
                    notes=f"{path.name}, column {self.column}",
                    start_hz=points[0][0],
                    end_hz=min(points[-1][0], self.rate / 2),
                    use_spl=True,
                    spl_reference=fuzzmeasure.CALIBRATOR_DB - level,
                    color=fuzzmeasure.COLORS[i % len(fuzzmeasure.COLORS)],
                )
            )
        return Result(
            fuzzmeasure.write(records),
            f"{paths[0].stem}.fume4",
            [
                (
                    f"{len(records)} measurements, {self.rate} Hz; "
                    f"{level:.2f} dB = 0 dB re full scale"
                )
            ],
        )
