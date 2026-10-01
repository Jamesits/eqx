"""SoundID profiles, AutoEq CSV, Dirac Live Processor filter slot -> FIR filter WAV."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import dsp
from ..autoeq import response
from ..dirac import filterslot
from ..dirac import playback as dirac_playback
from ..options import Option
from ..soundid import peqb, playback, swproj
from ..wav import fir
from .base import Converter, Result
from .common import COLUMN_OPTION, DEFAULT_RATE, RATE_OPTION, curves, dirac_notes, dirac_rate

PHASE_OPTION = Option("--phase", choices=dsp.PHASES,
                      help="minimum (SoundID Zero Latency) or linear (SoundID Linear Phase) "
                           "phase (default: minimum)")
TAPS_OPTION = Option("--taps", type=int,
                     help="filter length (default: SoundID's, at 48 kHz 4096 minimum phase, "
                          "4353 linear phase)")
SAFE_HEADROOM_OPTION = Option(
    "--safe-headroom", action=argparse.BooleanOptionalAction,
    help="lower the gain by the highest boost, as SoundID's Safe Headroom (default: on)")


def _check(phase: str, rate: float, taps: int | None) -> None:
    if phase not in dsp.PHASES:
        raise ValueError(f"phase must be one of: {', '.join(dsp.PHASES)}")
    if not rate > 0:
        raise ValueError("the sample rate must be positive")
    if taps is not None and (taps < 3 or (phase == "linear" and taps % 2 == 0)):
        raise ValueError("--taps must be at least 3, and odd for linear phase")


def _notes(channels: list[list[float]], rate: float, phase: str,
           taps: int | None = None) -> list[str]:
    """``taps``: the filter length before the listening spot delays."""
    latency = (taps or dsp.fir_taps(rate, phase)) // 2 if phase == "linear" else 0
    taps = len(channels[0])
    return [f"{len(channels)} channel(s), {taps} taps at {rate:g} Hz, {phase} phase, "
            f"latency {latency} samples ({1000 * latency / rate:.2f} ms)"]


class PeqbToFir(Converter):
    source = "peqb"
    target = "fir"
    description = "the stereo filter SoundID Reference plays for a headphone profile"
    options = (
        PHASE_OPTION, RATE_OPTION, TAPS_OPTION, peqb.COMPUTER_ID_OPTION, peqb.KEY_OPTION,
        SAFE_HEADROOM_OPTION,
    )

    def __init__(self, phase: str = "minimum", rate: float = DEFAULT_RATE, taps: int | None = None,
                 computer_id: str | None = None, key: str | None = None,
                 safe_headroom: bool = True):
        _check(phase, rate, taps)
        peqb.key_for(computer_id, key)
        self.phase, self.rate, self.taps = phase, rate, taps
        self.computer_id, self.key = computer_id, key
        self.safe_headroom = safe_headroom

    def _convert(self, path: Path) -> Result:
        p = peqb.open_decoded(path.read_bytes(), self.computer_id, self.key)
        channels, gain = playback.headphone_fir(p, self.rate, self.phase, self.taps,
                                                self.safe_headroom)
        return Result(fir.write(fir.Fir(self.rate, channels)), f"{path.stem}.wav",
                      _notes(channels, self.rate, self.phase, self.taps)
                      + [f"gain {gain:.2f} dB"])


class SwprojToFir(Converter):
    """One channel per project channel, in channel order."""

    source = "swproj"
    target = "fir"
    description = "the filter SoundID Reference plays for a speaker project"
    options = (
        PHASE_OPTION, RATE_OPTION, TAPS_OPTION, swproj.PASSWORD_OPTION, SAFE_HEADROOM_OPTION,
        Option("--listening-spot", action=argparse.BooleanOptionalAction,
               help="apply the project's listening spot delay and gain, as SoundID's "
                    "Listening Spot (default: on)"),
        Option("--limit-correction", type=float, choices=playback.LIMIT_CORRECTION_DB,
               help="maximum boost, dB, as SoundID's Limit Controls Correction (default: 12)"),
        Option("--limit-low", choices=playback.LIMIT_LOW,
               help="SoundID's Limit Controls Max low frequencies (default: neutral)"),
        Option("--limit-high", choices=playback.LIMIT_HIGH,
               help="SoundID's Limit Controls Max high frequencies (default: neutral)"),
    )

    def __init__(self, phase: str = "minimum", rate: float = DEFAULT_RATE, taps: int | None = None,
                 password: str | None = None, safe_headroom: bool = True,
                 listening_spot: bool = True, limit_correction: float = 12.0,
                 limit_low: str = "neutral", limit_high: str = "neutral"):
        _check(phase, rate, taps)
        if limit_correction not in playback.LIMIT_CORRECTION_DB:
            raise ValueError("--limit-correction must be 12, 6 or 0")
        if limit_low not in playback.LIMIT_LOW or limit_high not in playback.LIMIT_HIGH:
            raise ValueError(f"--limit-low: one of {', '.join(playback.LIMIT_LOW)}; "
                             f"--limit-high: one of {', '.join(playback.LIMIT_HIGH)}")
        self.phase, self.rate, self.taps = phase, rate, taps
        self.password = swproj.password_bytes(password)
        self.safe_headroom, self.listening_spot = safe_headroom, listening_spot
        self.limits = (limit_correction, limit_low, limit_high)

    def _convert(self, path: Path) -> Result:
        channels, irs, gain = playback.speaker_fir(
            swproj.SwProj.open(path, self.password), self.rate, self.phase, self.taps,
            self.safe_headroom, self.listening_spot, *self.limits)
        notes = _notes(irs, self.rate, self.phase, self.taps) + [
            f"channels: {', '.join(c.name for c in channels)}; gain {gain:.2f} dB; "
            f"limits: {self.limits[0]:g} dB, low {self.limits[1]}, high {self.limits[2]}"]
        if self.listening_spot:
            top = max(c.gain_db for c in channels)
            first = min(c.delay_ms for c in channels)
            notes += [f"{c.name} listening spot: "
                      f"{playback.spot_samples(c.delay_ms - first, self.rate)} samples, "
                      f"{c.gain_db - top:+.2f} dB"
                      for c in channels if c.delay_ms != first or c.gain_db != top]
        return Result(fir.write(fir.Fir(self.rate, irs)), f"{path.stem}.wav", notes)


class AutoeqToFir(Converter):
    """The curve is the filter's gain; DC and Nyquist keep the end values."""

    source = "autoeq"
    target = "fir"
    description = "a curve (inputs: left, optional right) as a FIR filter"
    options = (COLUMN_OPTION, PHASE_OPTION, RATE_OPTION, TAPS_OPTION)
    inputs = 2

    def __init__(self, column: str = response.RAW,
                 phase: str = "minimum", rate: float = DEFAULT_RATE, taps: int | None = None):
        _check(phase, rate, taps)
        self.column = column
        self.phase, self.rate, self.taps = phase, rate, taps

    def _convert(self, *paths: Path) -> Result:
        channels = [dsp.design_fir_points(points, self.rate, self.phase, self.taps)
                    for _, points in curves(paths, self.column)]
        return Result(fir.write(fir.Fir(self.rate, channels)), f"{paths[0].stem}.wav",
                      _notes(channels, self.rate, self.phase, self.taps))


class DiracFilterToFir(Converter):
    """One WAV channel per output, in output order."""

    source = "dirac-filter"
    target = "fir"
    description = "the impulse responses the Dirac Live Processor plays, as a FIR filter"
    options = (RATE_OPTION,)

    def __init__(self, rate: float = DEFAULT_RATE):
        self.rate = dirac_rate(rate)

    def _convert(self, path: Path) -> Result:
        slot = filterslot.load(path).slot
        names = dirac_playback.output_names(slot)
        played = [dirac_playback.impulse_response(slot, i, self.rate) for i in range(len(names))]
        channels = dsp.padded([ir for ir, _ in played])
        return Result(fir.write(fir.Fir(self.rate, channels)), f"{path.stem}.wav",
                      [f"{len(channels)} channel(s): {', '.join(names)}; {len(channels[0])} taps"]
                      + dirac_notes(slot, self.rate,
                                    [(n, c) for n, (_, c) in zip(names, played)]))
