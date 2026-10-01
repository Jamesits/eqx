"""Dirac Live Processor filter slot conversions: slot -> AutoEq CSV, FIR WAV;
AutoEq CSV, FIR WAV -> slot."""

from __future__ import annotations

from pathlib import Path

from .. import dsp
from ..autoeq import response
from ..dirac import filterslot, playback
from ..wav import fir
from .base import Converter, Result
from .from_autoeq import COLUMN_OPTION, DEFAULT_RATE, RATE_OPTION, _stereo
from .to_autoeq import SPEAKER_OPTION, _result

# Taps of the target filter; the low-rate FIR spans 16 x 1024 samples.
DESIGN_TAPS = playback.FACTOR * playback.TAPS


def _check_rate(rate: float) -> int:
    if rate not in playback.RATES:
        raise ValueError(f"--rate must be one of: {', '.join(map(str, playback.RATES))}")
    return int(rate)


def _output(slot: dict, speaker: str | None) -> tuple[int, str]:
    """The output named ``speaker``; without one, Left or the only output."""
    names = playback.output_names(slot)
    if speaker is None:
        if len(names) == 1:
            return 0, names[0]
        speaker = "Left"
    lower = [n.lower() for n in names]
    if speaker.lower() in lower:
        index = lower.index(speaker.lower())
        return index, names[index]
    raise ValueError(f"no speaker {speaker!r}; outputs: {', '.join(names)}")


def _playback_notes(slot: dict, rate: int, irs: list[tuple[str, int]]) -> list[str]:
    notes = [f"{playback.filter_type(playback.live_section(slot))} filter at {rate} Hz"]
    notes += [f"{name}: {cross} cross-term cells left out" for name, cross in irs if cross]
    return notes


class DiracFilterToAutoeq(Converter):
    source = "dirac-filter"
    target = "autoeq"
    description = "the gain the Dirac Live Processor plays on one output, on the standard grid"
    options = (SPEAKER_OPTION, RATE_OPTION)

    def __init__(self, speaker: str | None = None, rate: float = DEFAULT_RATE):
        self.speaker = speaker
        self.rate = _check_rate(rate)

    def _convert(self, path: Path) -> Result:
        slot = filterslot.load(path).slot
        index, name = _output(slot, self.speaker)
        ir, cross = playback.impulse_response(slot, index, self.rate)
        points = fir.response(fir.Fir(self.rate, [ir]), 0)
        return _result(points, f"{path.stem} {name}.csv",
                       *_playback_notes(slot, self.rate, [(name, cross)]))


class DiracFilterToFir(Converter):
    """One WAV channel per output, in output order."""

    source = "dirac-filter"
    target = "fir"
    description = "the impulse responses the Dirac Live Processor plays, as a FIR filter"
    options = (RATE_OPTION,)

    def __init__(self, rate: float = DEFAULT_RATE):
        self.rate = _check_rate(rate)

    def _convert(self, path: Path) -> Result:
        slot = filterslot.load(path).slot
        names = playback.output_names(slot)
        played = [playback.impulse_response(slot, i, self.rate) for i in range(len(names))]
        size = max(len(ir) for ir, _ in played)
        channels = [ir + [0.0] * (size - len(ir)) for ir, _ in played]
        return Result(fir.write(fir.Fir(self.rate, channels)), f"{path.stem}.wav",
                      [f"{len(channels)} channel(s): {', '.join(names)}; {size} taps"]
                      + _playback_notes(slot, self.rate,
                                        [(n, c) for n, (_, c) in zip(names, played)]))


def _slot_result(name: str, channels: list[str], targets: dict[int, list[list[float]]],
                 stem: str, notes: list[str]) -> Result:
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
    return Result(filterslot.write(slot), f"{stem}.bin", notes)


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
        curves = _stereo(paths, self.column)
        targets = {rate: [dsp.design_fir([f for f, _ in points], [g for _, g in points], rate,
                                         "minimum", DESIGN_TAPS)
                          for _, points in curves]
                   for rate in playback.RATES}
        return _slot_result(paths[0].stem, [c for c, _ in curves], targets, paths[0].stem, [])


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
        targets = {}
        for r in playback.RATES:
            if r == rate:
                targets[r] = [list(c) for c in f.channels]
                continue
            targets[r] = []
            for i in range(count):
                points = fir.response(f, i)
                targets[r].append(dsp.design_fir([p for p, _ in points], [g for _, g in points],
                                                 r, "minimum", DESIGN_TAPS))
        channels = [fir.channel_name(i, count) for i in range(count)]
        return _slot_result(path.stem, channels, targets, path.stem,
                            [f"{rate} Hz: the WAV; other rates: minimum phase"])
