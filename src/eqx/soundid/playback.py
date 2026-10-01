"""The filter SoundID Reference plays for a headphone profile or a speaker project.

Found in the SoundID Reference 5.13.8 VST3 plug-in and checked by running it
on an impulse.  Flat target.  Headphones: the profile's group delay, error
bands and the plug-in's limit controls have no effect.
"""

from __future__ import annotations

import base64
import bisect
import json
import math
from dataclasses import dataclass

from .. import dsp
from . import swproj
from .peqb import Peqb

SIDES = ("Left", "Right")
# SoundID sets DC and Nyquist to this gain.
EDGE_DB = 0.0


def _curve(p: Peqb, type_name: str) -> list[tuple[float, float]]:
    for c in p.curves:
        if c.type_name == type_name:
            return [(f, r) for f, r, _ in c.points]
    raise ValueError(f"no {type_name} curve; curves: "
                     f"{', '.join(c.type_name for c in p.curves) or 'none'}")


def _interp_log(points: list[tuple[float, float]], f: float) -> float:
    """Linear in log frequency, clamped to the end values."""
    xs = [x for x, _ in points]
    if f <= xs[0]:
        return points[0][1]
    if f >= xs[-1]:
        return points[-1][1]
    i = bisect.bisect_right(xs, f) - 1
    (f0, g0), (f1, g1) = points[i], points[i + 1]
    return g0 + math.log(f / f0) / math.log(f1 / f0) * (g1 - g0)


def headphone_curves(p: Peqb) -> dict[str, list[tuple[float, float]]]:
    """{side: (frequency, dB)}: the correction capped by the profile's frame."""
    has_frame = any(c.type_name == "Frame" for c in p.curves)
    frame = _curve(p, "Frame") if has_frame else None
    out = {}
    for side in SIDES:
        points = _curve(p, f"Correction{side}")
        out[side] = [(f, min(g, _interp_log(frame, f)) if frame else g) for f, g in points]
    return out


def safe_headroom_db(curves: dict) -> float:
    """SoundID's safe headroom gain: minus the highest boost of all channels, at most 0 dB."""
    return min(0.0, -max(g for points in curves.values() for _, g in points))


def headphone_fir(p: Peqb, sample_rate: float = 48000.0, phase: str = "minimum",
                  taps: int | None = None, safe_headroom: bool = True) -> tuple[list, float]:
    """([left, right] impulse responses, gain dB) of the filter SoundID plays."""
    curves = headphone_curves(p)
    gain = safe_headroom_db(curves) if safe_headroom else 0.0
    scale = 10 ** (gain / 20)
    channels = [[v * scale for v in dsp.design_fir([f for f, _ in curves[side]],
                                                   [g for _, g in curves[side]],
                                                   sample_rate, phase, taps, EDGE_DB)]
                for side in SIDES]
    return channels, gain


# --------------------------------------------------------------------------
# speakers
# --------------------------------------------------------------------------
LIMIT_CORRECTION_DB = (12.0, 6.0, 0.0)
# Limit control values; a roll-off moves by value / slope octaves.
LIMIT_LOW = {"reduced": -12.0, "neutral": 0.0, "extended": 12.0, "aggressive": 24.0}
LIMIT_HIGH = {"neutral": 0.0, "extended": 12.0, "aggressive": 24.0}
LIMIT_MIN_HZ, LIMIT_MAX_HZ = 20.0, 22000.0
# A roll-off is found where the measurement is this far below its level.
ROLLOFF_DB = -12.0
LOW_SLOPE = 24.0                         # dB per octave


@dataclass(frozen=True)
class RolloffRule:
    level_hz: tuple[float, float]        # band of the level
    low_search_hz: float                 # the low roll-off is searched from 20 Hz up to here
    high_search_hz: tuple[float, float]  # the high roll-off is searched downwards in here
    high_slope: float                    # dB per octave


SPEAKER_RULE = RolloffRule((200.0, 10000.0), 200.0, (10000.0, 22000.0), 48.0)
# A channel group with an LFE.
LFE_RULE = RolloffRule((50.0, 150.0), 60.0, (80.0, 500.0), 24.0)


@dataclass
class SpeakerChannel:
    index: int
    name: str
    group: str
    lfe: bool
    measurement: list[tuple[float, float]]
    correction: list[tuple[float, float]]
    delay_ms: float                      # listening spot adjustment
    gain_db: float


def _param(curve, key: str) -> float:
    value = curve.parameters.get(key, "0") or "0"
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{curve.type_name}: {key} {value!r} is not a number") from None


def _lfe_map(project: swproj.SwProj) -> list[bool]:
    """``lfeChannelMap`` of the ``TestSignalConfig`` parameter; SoundID's only LFE source."""
    for kv in project.tree().iter(f"{{{swproj.NS['a']}}}KeyValueOfstringstring"):
        if kv.findtext("a:Key", namespaces=swproj.NS) == "TestSignalConfig":
            try:
                config = json.loads(base64.b64decode(kv.findtext("a:Value", namespaces=swproj.NS)))
            except ValueError:
                continue
            return [bool(v) for v in config.get("lfeChannelMap", [])]
    return []


def sonarworks_reference_channels(curves: list) -> list[SpeakerChannel]:
    """Left and right of a Sonarworks Reference 3 / 4 ``eqb``: no channel parameters; the
    listening spot is the curve's ``transfer`` and ``delay_ms``.  A missing
    measurement is the negated correction, as in the Reference plug-ins."""
    by_type = {}
    for c in curves:
        by_type.setdefault(c.type_name, c)
    channels = []
    for index, side in enumerate(SIDES):
        c = by_type.get(f"Correction{side}")
        if c is None:
            raise ValueError(f"the project has no Correction{side} curve")
        m = by_type.get(f"Measurement{side}")
        measurement = ([(f, r) for f, r, _ in m.points] if m is not None
                       else [(f, -r) for f, r, _ in c.points])
        channels.append(SpeakerChannel(index, side, "Front", False, measurement,
                                       [(f, r) for f, r, _ in c.points],
                                       c.delay_ms or 0.0, c.transfer or 0.0))
    return channels


def speaker_channels(project: swproj.SwProj) -> list[SpeakerChannel]:
    """The channels of a speaker project's ``eqb`` part, in channel order."""
    if project.eqb is None:
        raise ValueError("the project has no eqb part")
    if project.eqb.version < (3, 0, 0, 2):
        return sonarworks_reference_channels(project.eqb.curves)
    measurements, corrections = {}, {}
    for c in project.eqb.curves:
        if "ChannelIndex" not in c.parameters:
            continue
        index = int(_param(c, "ChannelIndex"))
        if c.type_name == "Measurement":
            measurements[index] = c
        elif c.type_name in ("CorrectionLeft", "CorrectionRight", "Correction"):
            corrections[index] = c
    if not corrections:
        raise ValueError("the project has no speaker correction curves")
    lfe = _lfe_map(project)
    channels = []
    for index in sorted(corrections):
        if index not in measurements:
            raise ValueError(f"channel {index} has no measurement curve")
        c, m = corrections[index], measurements[index]
        channels.append(SpeakerChannel(
            index, c.parameters.get("ChannelName") or f"Channel {index + 1}",
            c.parameters.get("ChannelGroup", ""), index < len(lfe) and lfe[index],
            [(f, r) for f, r, _ in m.points], [(f, r) for f, r, _ in c.points],
            _param(c, "ChannelDelayMs"), _param(c, "Transfer")))
    return channels


def level_db(points, band: tuple[float, float]) -> float:
    """Power average of the points in ``band``, dB."""
    powers = [10 ** (g / 10) for f, g in points if band[0] <= f <= band[1]]
    if not powers:
        return 0.0
    mean = sum(powers) / len(powers)
    return 10 * math.log10(mean) if mean > 1e-12 else -120.0


def low_rolloff(points, level: float, search_hz: float) -> float:
    """The first point from 20 Hz up that is above level - 12 dB, moved up by
    its distance to the level at 24 dB/octave.  20 Hz if there is none."""
    for f, g in points:
        if f < LIMIT_MIN_HZ:
            continue
        if f > search_hz:
            break
        if g > level + ROLLOFF_DB:
            return max(LIMIT_MIN_HZ, f * 2 ** ((level - g) / LOW_SLOPE))
    return LIMIT_MIN_HZ


def high_rolloff(points, level: float, search_hz: tuple[float, float], slope: float) -> float:
    """``low_rolloff`` mirrored: searched downwards from the top of ``search_hz``."""
    for f, g in reversed(points):
        if f > search_hz[1]:
            continue
        if f < search_hz[0]:
            break
        if g > level + ROLLOFF_DB:
            return min(search_hz[1], f * 2 ** ((g - level) / slope))
    return search_hz[1]


def limit_points(low_hz: float, high_hz: float, top_db: float, high_slope: float,
                 low_shift: float = 0.0, high_shift: float = 0.0) -> list[tuple[float, float]]:
    """SoundID's boost limit: 0 dB beyond the roll-offs, ``top_db`` between."""
    low = low_hz * 2 ** (-low_shift / LOW_SLOPE)
    high = high_hz * 2 ** (high_shift / high_slope)
    points: dict[float, float] = {}
    if low > LIMIT_MIN_HZ:
        points[LIMIT_MIN_HZ] = 0.0
    points[low] = 0.0
    if top_db > 3:
        points[low * 2 ** (top_db / LOW_SLOPE)] = top_db - 1
    points[low * 2 ** (top_db / LOW_SLOPE) * 1.1] = top_db
    points[math.sqrt(low * high)] = top_db
    points[high * 2 ** (-top_db / high_slope) / 1.1] = top_db
    if top_db > 3:
        points[high * 2 ** (-top_db / high_slope)] = top_db - 1
    points[high] = 0.0
    if LIMIT_MAX_HZ > high:
        points[LIMIT_MAX_HZ] = 0.0
    return sorted(points.items())


def speaker_curves(channels: list[SpeakerChannel], limit_correction_db: float = 12.0,
                   limit_low: str = "neutral", limit_high: str = "neutral"
                   ) -> dict[int, list[tuple[float, float]]]:
    """{channel index: (frequency, dB)}: the corrections after SoundID's limits.

    A channel group shares the roll-offs of its narrowest channel.  Beyond
    them, no channel ends above that channel's corrected response, each
    relative to its level.
    """
    if limit_correction_db not in LIMIT_CORRECTION_DB:
        raise ValueError("the correction limit must be 12, 6 or 0 dB")
    if limit_low not in LIMIT_LOW or limit_high not in LIMIT_HIGH:
        raise ValueError(f"limits: low one of {', '.join(LIMIT_LOW)}, "
                         f"high one of {', '.join(LIMIT_HIGH)}")
    low_shift, high_shift = LIMIT_LOW[limit_low], LIMIT_HIGH[limit_high]
    groups: dict[str, list[SpeakerChannel]] = {}
    for c in channels:
        groups.setdefault(c.group or c.name, []).append(c)
    out = {}
    for members in groups.values():
        rule = LFE_RULE if any(c.lfe for c in members) else SPEAKER_RULE
        grid = [f for f, _ in members[0].correction]
        logs = [math.log(f) for f in grid]
        meas, corr, levels, lows, highs = {}, {}, {}, {}, {}
        for c in members:
            meas[c.index] = [_interp_log(c.measurement, f) for f in grid]
            corr[c.index] = [_interp_log(c.correction, f) for f in grid]
            points = list(zip(grid, meas[c.index]))
            level = levels[c.index] = level_db(points, rule.level_hz)
            lows[c.index] = low_rolloff(points, level, rule.low_search_hz)
            highs[c.index] = high_rolloff(points, level, rule.high_search_hz, rule.high_slope)
        low, high = max(lows.values()), min(highs.values())
        pts = limit_points(low, high, limit_correction_db, rule.high_slope, low_shift, high_shift)
        # The interpolated limit dips below 0 dB next to its corners; it never cuts.
        limit = [max(0.0, v) for v in dsp.hermite([math.log(f) for f, _ in pts],
                                                  [v for _, v in pts], logs)]
        for c in members:
            corr[c.index] = [min(g, lim) for g, lim in zip(corr[c.index], limit)]
        low_top = low * 2 ** ((limit_correction_db - low_shift) / LOW_SLOPE)
        high_top = high * 2 ** ((high_shift - limit_correction_db) / rule.high_slope)
        low_ref = max(lows, key=lambda i: (lows[i], -i))
        high_ref = min(highs, key=lambda i: (highs[i], i))
        for c in members:
            values = list(corr[c.index])
            for ref, detected, below in ((low_ref, low > LIMIT_MIN_HZ, True),
                                         (high_ref, high < rule.high_search_hz[1], False)):
                if not detected:
                    continue
                for k, f in enumerate(grid):
                    if (f < low_top) if below else (f > high_top):
                        after = meas[ref][k] - levels[ref] + corr[ref][k]
                        values[k] = min(values[k], after - (meas[c.index][k] - levels[c.index]))
            out[c.index] = list(zip(grid, values))
    return out


def spot_samples(delay_ms: float, sample_rate: float) -> int:
    """Samples SoundID delays a channel by: the delay rounded down, less one."""
    return max(0, math.floor(delay_ms * sample_rate / 1000) - 1)


def speaker_fir(project: swproj.SwProj, sample_rate: float = 48000.0, phase: str = "minimum",
                taps: int | None = None, safe_headroom: bool = True, listening_spot: bool = True,
                limit_correction_db: float = 12.0, limit_low: str = "neutral",
                limit_high: str = "neutral") -> tuple[list[SpeakerChannel], list, float]:
    """(channels, impulse responses in channel order, safe headroom gain dB)."""
    channels = speaker_channels(project)
    curves = speaker_curves(channels, limit_correction_db, limit_low, limit_high)
    gain = safe_headroom_db(curves) if safe_headroom else 0.0
    top_gain = max(c.gain_db for c in channels)
    first_delay = min(c.delay_ms for c in channels)
    irs = []
    for c in channels:
        points = curves[c.index]
        g = gain + (c.gain_db - top_gain if listening_spot else 0.0)
        delay = spot_samples(c.delay_ms - first_delay, sample_rate) if listening_spot else 0
        ir = dsp.design_fir([f for f, _ in points], [v for _, v in points], sample_rate, phase,
                            taps, EDGE_DB)
        irs.append([0.0] * delay + [v * 10 ** (g / 20) for v in ir])
    length = max(len(ir) for ir in irs)
    return channels, [ir + [0.0] * (length - len(ir)) for ir in irs], gain
