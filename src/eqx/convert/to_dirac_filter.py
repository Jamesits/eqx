"""AutoEq CSV, FIR filter WAV -> Dirac Live Processor filter slot."""

from __future__ import annotations

from pathlib import Path

from .. import dsp
from ..autoeq import response
from ..dirac import filterslot, playback
from ..wav import fir
from .base import Converter, Result
from .common import COLUMN_OPTION, stereo

# Taps of the target filter; the low-rate FIR spans 16 x 1024 samples.
DESIGN_TAPS = playback.FACTOR * playback.TAPS


def _slot_result(name: str, channels: list[str], targets: dict[int, list[list[float]]],
                 notes: list[str]) -> Result:
    designs = {rate: [playback.design(t, rate) for t in per_channel]
               for rate, per_channel in targets.items()}
    slot = playback.dual_rate_slot(name, channels, designs)
    lost = max(d.lost for per_channel in designs.values() for d in per_channel)
    latency = playback.LATENCY
    notes = notes + [
        f"dual-rate filter, {len(channels)} channel(s) at "
        f"{', '.join(map(str, designs))} Hz; latency {latency} samples "
        f"({1000 * latency / 48000:.2f} ms at 48 kHz)",
        f"high band outside the high-rate FIR: {100 * lost:.3g} % of its energy",
        "unsigned; the processor reads it as filters/<slot>/filter.bin"]
    return Result(filterslot.write(slot), f"{name}.bin", notes)


class AutoeqToDiracFilter(Converter):
    """The curve is the filter's gain, minimum phase, at every slot rate."""

    source = "autoeq"
    target = "dirac-filter"
    description = ("a curve (inputs: both channels, or left then right) as a Dirac Live "
                   "Processor filter slot")
    options = (COLUMN_OPTION,)
    inputs = 2

    def __init__(self, column: str = response.RAW):
        self.column = column

    def _convert(self, *paths: Path) -> Result:
        curves = stereo(paths, self.column)
        targets = {rate: [dsp.design_fir_points(points, rate, "minimum", DESIGN_TAPS)
                          for _, points in curves]
                   for rate in playback.RATES}
        return _slot_result(paths[0].stem, [c for c, _ in curves], targets, [])


class FirToDiracFilter(Converter):
    """The WAV plays as it is at its own rate; the other slot rates get a
    minimum-phase filter with its gain."""

    source = "fir"
    target = "dirac-filter"
    description = "a FIR filter as a Dirac Live Processor filter slot"

    def _convert(self, path: Path) -> Result:
        f = fir.load(path)
        rate = round(f.sample_rate)
        if rate not in playback.RATES:
            raise ValueError(f"the WAV is at {f.sample_rate:g} Hz; a slot takes "
                             f"{', '.join(map(str, playback.RATES))} Hz")
        if f.taps > DESIGN_TAPS:
            raise ValueError(f"{f.taps} taps; a slot plays at most {DESIGN_TAPS}")
        count = len(f.channels)
        targets = {r: ([list(c) for c in f.channels] if r == rate else
                       [dsp.design_fir_points(fir.response(f, i), r, "minimum", DESIGN_TAPS)
                        for i in range(count)])
                   for r in playback.RATES}
        channels = [fir.channel_name(i, count) for i in range(count)]
        return _slot_result(path.stem, channels, targets,
                            [f"{rate} Hz: the WAV; other rates: minimum phase"])
