"""What the Dirac Live Processor plays for a filter slot, and a dual-rate
filter design that plays a given impulse response.

The processor runs at the device rate up to 48 kHz and plays the matrix of
that rate from the slot's live section.  An output plays its own input here:
the cells from input ``o`` to output ``o``, then the slot's gain and delay of
output ``o``.  Cross-term cells (bass management) are counted, not played.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .. import fmath
from .filterslot import FILTER_TYPES, LIVE

# The rates a slot holds.
RATES = (32000, 44100, 48000)
# Built-in decimation / interpolation stages of the dual-rate filter, factor 4
# each; each sums to 2.  The processor ignores FilterCommon.drfir.low_pass.
STAGE_0 = (
    -0.000230519669,
    -0.00164272031,
    -0.00607869308,
    -0.0149214212,
    -0.0256543458,
    -0.0283253286,
    -0.00625678943,
    0.0557697453,
    0.158562511,
    0.279902488,
    0.379529536,
    0.418175429,
    0.379529536,
    0.279902488,
    0.158562511,
    0.0557697453,
    -0.00625678943,
    -0.0283253286,
    -0.0256543458,
    -0.0149214212,
    -0.00607869308,
    -0.00164272031,
    -0.000230519669,
)
STAGE_1 = (
    4.62973003e-05,
    3.63992658e-05,
    -3.2747892e-05,
    -0.000219429639,
    -0.00053578458,
    -0.000920722901,
    -0.00122024899,
    -0.00121029525,
    -0.00067389413,
    0.000479904906,
    0.00209674821,
    0.00371919665,
    0.00464885589,
    0.00415266166,
    0.00177781086,
    -0.00232590619,
    -0.00721998699,
    -0.0112616802,
    -0.0125205796,
    -0.00946604833,
    -0.00172824133,
    0.00935579091,
    0.0206971988,
    0.0280797593,
    0.0274044089,
    0.0162569359,
    -0.00469154678,
    -0.0309898593,
    -0.0548181161,
    -0.0665965006,
    -0.0574897714,
    -0.0221571326,
    0.0390784256,
    0.119386613,
    0.206322238,
    0.284362555,
    0.338487267,
    0.35784626,
    0.338487267,
    0.284362555,
    0.206322238,
    0.119386613,
    0.0390784256,
    -0.0221571326,
    -0.0574897714,
    -0.0665965006,
    -0.0548181161,
    -0.0309898593,
    -0.00469154678,
    0.0162569359,
    0.0274044089,
    0.0280797593,
    0.0206971988,
    0.00935579091,
    -0.00172824133,
    -0.00946604833,
    -0.0125205796,
    -0.0112616802,
    -0.00721998699,
    -0.00232590619,
    0.00177781086,
    0.00415266166,
    0.00464885589,
    0.00371919665,
    0.00209674821,
    0.000479904906,
    -0.00067389413,
    -0.00121029525,
    -0.00122024899,
    -0.000920722901,
    -0.00053578458,
    -0.000219429639,
    -3.2747892e-05,
    3.63992658e-05,
    4.62973003e-05,
)
FACTOR = 16  # the low-rate FIR runs at rate / 16
CHAIN_LATENCY = 318  # samples of the decimate-interpolate chain
# The designer's delay_hi refers to a 370-sample chain; the processor shortens
# the high-rate delay by the difference.
DELAY_OFFSET = 52
TAPS = 1024  # per branch
MAX_DELAY_MS = 100.0
# Longest parallel IIR tail of an FIIR filter, samples, and where it ends.
FIIR_TAIL = 65536
SECTION_FLOOR = 1e-9


def _convolve(a, b) -> list[float]:
    out = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                out[i + j] += x * y
    return out


def _upsample(h, factor: int) -> list[float]:
    out = [0.0] * ((len(h) - 1) * factor + 1)
    out[::factor] = h
    return out


_CHAIN: list[float] = []


def chain() -> list[float]:
    """Impulse response of the low-rate branch around a unit low-rate FIR:
    the linear part of decimating and interpolating with both stages."""
    if not _CHAIN:
        stage0 = _convolve(STAGE_0, STAGE_0)
        stage1 = _upsample(_convolve(STAGE_1, STAGE_1), 4)
        _CHAIN.extend(v / 16 for v in _convolve(stage0, stage1))
    return _CHAIN


def _add(out: list[float], h, offset: int, scale: float = 1.0) -> None:
    if offset + len(h) > len(out):
        out.extend([0.0] * (offset + len(h) - len(out)))
    for i, v in enumerate(h):
        out[offset + i] += scale * v


def _low_branch(fir_lo) -> list[float]:
    out: list[float] = []
    h = chain()
    for k, g in enumerate(fir_lo):
        if g:
            _add(out, h, FACTOR * k, g)
    return out


def _taps(fir: dict | None) -> list[float]:
    return list((fir or {}).get("fir_taps", []))


def _drfir(cell: dict) -> list[float]:
    d = cell["drfir"]
    out = _low_branch(_taps(d.get("fir_lo")))
    hi = _taps(d.get("fir_hi"))
    if hi:
        _add(out, hi, max(0, d.get("delay_hi", 0) - DELAY_OFFSET))
    return out


def _section(a1: float, a2: float, b0: float, b1: float) -> list[float]:
    """Impulse response of (b0 + b1 z^-1) / (1 + a1 z^-1 + a2 z^-2), until it
    decays below SECTION_FLOOR of its peak."""
    out: list[float] = []
    y1 = y2 = peak = 0.0
    for n in range(FIIR_TAIL):
        y = (b0 if n == 0 else b1 if n == 1 else 0.0) - a1 * y1 - a2 * y2
        out.append(y)
        peak = max(peak, abs(y))
        if n > 1 and max(abs(y), abs(y1)) <= SECTION_FLOOR * peak:
            break
        y1, y2 = y, y1
    return out


def _fiir(cell: dict) -> list[float]:
    """The FIR, then from its end a bank of parallel sections
    (b0 + b1 z^-1) / (1 + a1 z^-1 + a2 z^-2); b2 is not used."""
    f = cell["fiir"]
    out = _taps(f.get("fir"))
    start = len(out)
    for bq in f.get("iir", {}).get("biquads", []):
        _add(out, _section(*(bq.get(k, 0.0) for k in ("a1", "a2", "b0", "b1"))), start)
    return out


CELL_RESPONSES = {"DUAL_RATE_FILTER": ("drfir", _drfir), "FIIR_FILTER": ("fiir", _fiir)}


def live_section(slot: dict) -> dict:
    lives = [
        s for s in slot.get("sections", {}).values() if s.get("disposition", 0) == LIVE
    ]
    if len(lives) != 1:
        raise ValueError(f"{len(lives)} live sections; the processor plays exactly one")
    return lives[0]


def filter_type(section: dict) -> str:
    value = section.get("filter_type", 0)
    return FILTER_TYPES[value] if 0 <= value < len(FILTER_TYPES) else str(value)


def output_names(slot: dict) -> list[str]:
    """The designer's output labels, else Left / Right / Channel N."""
    section = live_section(slot)
    count = section.get("num_outputs", 0) or 1 + max(
        (
            c.get("output_idx", 0)
            for m in section.get("rates", {}).values()
            for c in m.get("cells", [])
        ),
        default=-1,
    )
    names = list(section.get("channel_names", {}).get("outputs", []))
    if len(names) == count and all(names):
        return names
    return (
        ["Left", "Right"] if count == 2 else [f"Channel {i + 1}" for i in range(count)]
    )


def impulse_response(slot: dict, output: int, rate: int) -> tuple[list[float], int]:
    """(impulse response at ``rate``, cross-term cells left out) of one output."""
    section = live_section(slot)
    kind = filter_type(section)
    if kind not in CELL_RESPONSES:
        raise ValueError(f"{kind} live filters are not supported")
    rates = section.get("rates", {})
    if rate not in rates:
        raise ValueError(
            f"no {rate} Hz filter; available: "
            f"{', '.join(str(r) for r in sorted(rates)) or 'none'}"
        )
    field_name, cell_response = CELL_RESPONSES[kind]
    out: list[float] = []
    cross = 0
    for cell in rates[rate].get("cells", []):
        if cell.get("output_idx", 0) != output:
            continue
        if cell.get("input_idx", 0) != output:
            cross += 1
        elif field_name in cell:
            _add(out, cell_response(cell), 0)
    if not out:
        raise ValueError(
            f"no filter from input {output} to output {output} at {rate} Hz"
        )
    gain_delay = slot.get("gain_delay", [])
    if output < len(gain_delay):
        g = gain_delay[output]
        gain = fmath.pow(10, g.get("gain_db", 0.0) / 20)
        # The processor ignores gains outside 0-10.
        gain = gain if gain <= 10 else 1.0
        delay = max(0, int(rate * g.get("delay_ms", 0.0) / 1000))
        out = [0.0] * delay + [v * gain for v in out]
    return out, cross


# --------------------------------------------------------------------------
# dual-rate design
# --------------------------------------------------------------------------
# The low band of the target is taken with this zero-phase low pass; its half
# length adds to the latency.
LOWPASS_HALF = 128
LOWPASS_CUTOFF = 1 / 40  # of the sample rate
# The played response is the target delayed by this many samples.
LATENCY = CHAIN_LATENCY + LOWPASS_HALF


def _lowpass() -> list[float]:
    """Blackman-windowed sinc, unit DC gain; index LOWPASS_HALF is the center."""
    w = 2 * math.pi * LOWPASS_CUTOFF
    h = [
        (fmath.sin(w * n) / (math.pi * n) if n else 2 * LOWPASS_CUTOFF)
        * (
            0.42
            + 0.5 * fmath.cos(math.pi * n / (LOWPASS_HALF + 1))
            + 0.08 * fmath.cos(2 * math.pi * n / (LOWPASS_HALF + 1))
        )
        for n in range(-LOWPASS_HALF, LOWPASS_HALF + 1)
    ]
    total = math.fsum(h)
    return [v / total for v in h]


@dataclass
class Design:
    fir_hi: list[float]
    fir_lo: list[float]
    delay_hi: int  # as stored: the high-rate delay + DELAY_OFFSET
    lost: float  # residual energy outside the high-rate FIR, relative


def design(target: list[float], rate: int) -> Design:
    """A dual-rate cell that plays ``target`` delayed by ``LATENCY`` samples.

    The low-rate FIR takes the band below rate / 40; the high-rate FIR takes
    the rest at the 1024 samples that hold most of its energy.
    """
    lowpass = _lowpass()
    # The chain plays fir_lo[k] / 16 at sample 16 k + CHAIN_LATENCY, so the
    # low band sampled LOWPASS_HALF early lands at LATENCY.
    fir_lo = []
    for k in range(TAPS):
        n = FACTOR * k - LOWPASS_HALF
        acc = 0.0
        for j, h in enumerate(lowpass):
            i = n + LOWPASS_HALF - j
            if 0 <= i < len(target):
                acc += h * target[i]
        fir_lo.append(FACTOR * acc)
    low = _low_branch(fir_lo)
    size = max(len(low), LATENCY + len(target))
    want = [0.0] * LATENCY + list(target) + [0.0] * (size - LATENCY - len(target))
    low += [0.0] * (size - len(low))
    residual = [w - v for w, v in zip(want, low)]
    energy = [v * v for v in residual] + [0.0] * TAPS
    last = min(int(rate * MAX_DELAY_MS / 1000), size)
    window = math.fsum(energy[:TAPS])
    best, start = window, 0
    for s in range(1, last + 1):
        window += energy[s + TAPS - 1] - energy[s - 1]
        if window > best:
            best, start = window, s
    total = math.fsum(energy)
    fir_hi = residual[start : start + TAPS]
    fir_hi += [0.0] * (TAPS - len(fir_hi))
    return Design(
        fir_hi, fir_lo, start + DELAY_OFFSET, (total - best) / total if total else 0.0
    )


def drfir_info(rate: int) -> dict:
    """The processor's dual-rate topology at ``rate``."""
    return {
        "variant": 2 * RATES.index(rate),
        "samplerate": float(rate),
        "num_fir_taps_hi": TAPS,
        "num_fir_taps_lo": TAPS,
        "sub_sampling_factor": 4,
        "cascade_number": 2,
        "delay_hi_min": DELAY_OFFSET,
        "delay_hi_max": int(rate * MAX_DELAY_MS / 1000),
    }


def dual_rate_slot(
    name: str, channels: list[str], designs: dict[int, list[Design]]
) -> dict:
    """A slot with one live dual-rate section; output ``i`` plays input ``i``."""
    rates = {
        rate: {
            "cells": [
                {
                    "input_idx": i,
                    "output_idx": i,
                    "drfir": {
                        "info": drfir_info(rate),
                        "fir_hi": {"fir_taps": d.fir_hi},
                        "fir_lo": {"fir_taps": d.fir_lo},
                        "delay_hi": d.delay_hi,
                    },
                }
                for i, d in enumerate(per_channel)
            ]
        }
        for rate, per_channel in designs.items()
    }
    count = len(channels)
    return {
        "info": {
            "name": name,
            "filter_type_name": "DRFIR",
            "num_inputs": count,
            "num_outputs": count,
            "num_sections": 1,
        },
        "sections": {
            0: {
                "disposition": LIVE,
                "filter_type": FILTER_TYPES.index("DUAL_RATE_FILTER"),
                "rates": rates,
                "num_inputs": count,
                "num_outputs": count,
                "channel_names": {"inputs": list(channels), "outputs": list(channels)},
            }
        },
        "gain_delay": [{"input_idx": i} for i in range(count)],
    }
