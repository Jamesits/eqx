"""Options and helpers shared by the converters."""

from __future__ import annotations

import statistics
from pathlib import Path

from .. import dsp
from ..autoeq import response
from ..curve import log_resample
from ..dirac import playback, targetcurve
from ..fileformat import frequency_range
from ..model import Correction, Peq, standard_grid
from ..options import Option
from ..rew import cal
from ..soundid.speakerproject import LEVEL_HIGH_HZ, LEVEL_LOW_HZ
from .base import Result

COLUMN_OPTION = Option("--column", help=f"AutoEq CSV column to read (default: {response.RAW})")
DEFAULT_RATE = 48000.0
RATE_OPTION = Option("--rate", type=float, help="sample rate, Hz (default: 48000)")
CHANNELS = ("left", "right")
CHANNEL_OPTION = Option("--channel", choices=CHANNELS, help="channel to convert (default: left)")
TOLERANCE_DB = 0.1
TOLERANCE_OPTION = Option("--tolerance-db", type=float,
                          help=f"largest deviation from the curve, dB (default: {TOLERANCE_DB:g})")
SPEAKER_OPTION = Option("--speaker",
                        help="speaker, case-insensitive, as named by inspect, e.g. Left, "
                             "Subwoofer, Center (default: Left)")


def channel_name(channel: str) -> str:
    """``left`` or ``right`` as a channel name."""
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}")
    return channel.capitalize()


def missing(channel: str, available) -> ValueError:
    return ValueError(f"no {channel} channel; available: {', '.join(available) or 'none'}")


# ---------------------------------------------------------------------------
# AutoEq CSV
# ---------------------------------------------------------------------------
def csv_result(points, name: str, *notes: str) -> Result:
    """An AutoEq CSV of ``points``."""
    return Result(response.write(points).encode("utf-8"), name,
                  [f"{len(points)} points, {frequency_range(points)}", *notes])


def curves(paths: tuple[Path, ...], column: str) -> list[tuple[str, list]]:
    """[(channel, points)]: the first input as Left, the second as Right."""
    return [(channel, response.load(path).curve(column))
            for channel, path in zip(("Left", "Right"), paths)]


def stereo(paths: tuple[Path, ...], column: str) -> list[tuple[str, list]]:
    """Left and Right; one input is both."""
    pair = curves(paths, column)
    return pair if len(pair) == 2 else [pair[0], ("Right", pair[0][1])]


def simplified(points, tolerance_db: float) -> tuple[list[tuple[float, float]], float]:
    """The fewest standard grid points of ``points`` whose log-frequency
    interpolation follows them within ``tolerance_db``; and the largest error."""
    grid = standard_grid()
    on_grid = list(zip(grid, log_resample(points, grid)))
    breakpoints = targetcurve.simplify(on_grid, tolerance_db)
    error = max(abs(a - v) for a, (_, v) in zip(log_resample(breakpoints, grid), on_grid))
    return breakpoints, error


# ---------------------------------------------------------------------------
# curves to filters
# ---------------------------------------------------------------------------
def grid_band(low_hz: float, high_hz: float) -> list[float]:
    """The standard grid points within ``low_hz``-``high_hz``."""
    return [f for f in standard_grid() if low_hz <= f <= high_hz]


def median_level(named_curves: list[tuple[str, list]]) -> float:
    """Median dB of all curves in 200 Hz-10 kHz, on the standard grid."""
    band = grid_band(LEVEL_LOW_HZ, LEVEL_HIGH_HZ)
    return statistics.median(v for _, points in named_curves for v in log_resample(points, band))


def impulse_response(points, level_db: float, rate: float, lead: int, length: int,
                     taps: int | None = None) -> list[float]:
    """``length`` samples: silence for ``lead`` samples, then the minimum-phase
    filter of ``points`` less ``level_db``, ``taps`` long (default: the rest)."""
    ir = dsp.design_fir_points(points, rate, "minimum", taps or length - lead, level_db=level_db)
    return [0.0] * lead + ir + [0.0] * (length - lead - len(ir))


def fit_correction(channel: str, points, count: int, frequency_hz: tuple[float, float],
                   **limits) -> tuple[Correction, str]:
    """A gain and up to ``count`` bells fitted to ``points`` on the standard grid
    within ``frequency_hz``; and a note of the fit."""
    grid = grid_band(*frequency_hz)
    fit = dsp.fit_bells(grid, log_resample(points, grid), count, frequency_hz=frequency_hz,
                        **limits)
    return (Correction(channel, fit.gain_db, peqs=[Peq(f, g, q) for f, g, q in fit.bells]),
            f"{len(fit.bells)} bells; error {fit.rms_db:.2f} dB RMS, {fit.max_db:.2f} dB max")


def mic_response_db(path: Path, frequencies: list[float]) -> list[float]:
    """The table of a REW calibration file at ``frequencies``: linear in log frequency,
    clamped to the end values."""
    profile, _ = cal.load(path)
    points = [(f, g) for f, g in profile.points if f > 0]
    if len(points) < 2:
        raise ValueError(f"{Path(path).name}: too few calibration points above 0 Hz")
    return log_resample(points, frequencies)


# ---------------------------------------------------------------------------
# Dirac Live Processor playback
# ---------------------------------------------------------------------------
def dirac_rate(rate: float) -> int:
    if rate not in playback.RATES:
        raise ValueError(f"--rate must be one of: {', '.join(map(str, playback.RATES))}")
    return int(rate)


def dirac_output(slot: dict, speaker: str | None) -> tuple[int, str]:
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


def dirac_notes(slot: dict, rate: int, irs: list[tuple[str, int]]) -> list[str]:
    """``irs``: (output name, cross-term cells left out)."""
    notes = [f"{playback.filter_type(playback.live_section(slot))} filter at {rate} Hz"]
    notes += [f"{name}: {cross} cross-term cells left out" for name, cross in irs if cross]
    return notes
