"""Frequency response of measured impulse responses: band power and group delay
on a 1/48-octave grid."""

from __future__ import annotations

import math

from . import dsp, fmath

GRID_LOW_HZ, GRID_HIGH_HZ, GRID_STEPS_PER_OCTAVE = 20.0, 20000.0, 48


def power_db(power: float) -> float:
    return 10 * fmath.log10(max(power, 1e-30))


def log_grid(sample_rate: float) -> list[float]:
    """1/48 octave from 20 Hz up to 20 kHz, below Nyquist."""
    top = min(
        GRID_HIGH_HZ, sample_rate / 2 * fmath.pow(2, -1 / (2 * GRID_STEPS_PER_OCTAVE))
    )
    steps = math.floor(GRID_STEPS_PER_OCTAVE * fmath.log2(top / GRID_LOW_HZ) + 1e-9)
    return [
        GRID_LOW_HZ * fmath.pow(2, k / GRID_STEPS_PER_OCTAVE) for k in range(steps + 1)
    ]


def peak_index(ir: list[float]) -> int:
    return max(range(len(ir)), key=lambda i: abs(ir[i]))


def point_bands(
    ir: list[float], sample_rate: float, frequencies: list[float]
) -> list[tuple[float, float]]:
    """(power, group delay s) of one impulse response in the band around each frequency.

    The peak is moved to sample 0 first, so the delay before it (time of
    flight, latency) is not part of the group delay.
    """
    n = 1 << max(1, (len(ir) - 1).bit_length())
    padded = list(ir) + [0.0] * (n - len(ir))
    peak = peak_index(padded)
    spectrum = dsp.fft(padded[peak:] + padded[:peak])[: n // 2 + 1]
    step = sample_rate / n
    power = [fmath.pow(x.real, 2) + fmath.pow(x.imag, 2) for x in spectrum]
    # Central phase difference; its angle stays in (-pi, pi] without unwrapping.
    delay = [0.0] * len(spectrum)
    for k in range(1, len(spectrum) - 1):
        d = fmath.cmul(spectrum[k + 1], spectrum[k - 1].conjugate())
        delay[k] = -fmath.atan2(d.imag, d.real) / (2 * math.pi * 2 * step) if d else 0.0
    delay[0], delay[-1] = delay[1], delay[-2]
    return spectrum_bands(power, delay, step, frequencies)


def spectrum_bands(
    power: list[float], delay: list[float], step: float, frequencies: list[float]
) -> list[tuple[float, float]]:
    """(mean power, power-weighted delay) of the FFT bins within +-1/96 octave.

    Bin k is at k * ``step`` Hz.  A band with no bin interpolates the two
    nearest bins.
    """
    half = fmath.pow(2, 1 / (2 * GRID_STEPS_PER_OCTAVE))
    out = []
    for f in frequencies:
        first = math.ceil(f / half / step)
        last = min(math.ceil(f * half / step) - 1, len(power) - 1)
        if last >= first:
            p = math.fsum(power[first : last + 1])
            gd = (
                math.fsum(power[k] * delay[k] for k in range(first, last + 1)) / p
                if p
                else math.fsum(delay[first : last + 1]) / (last - first + 1)
            )
            out.append((p / (last - first + 1), gd))
        else:
            k = min(int(f / step), len(power) - 2)
            t = f / step - k
            out.append(
                (
                    power[k] + t * (power[k + 1] - power[k]),
                    delay[k] + t * (delay[k + 1] - delay[k]),
                )
            )
    return out


def average(
    irs: list[list[float]], sample_rate: float, frequencies: list[float]
) -> tuple[list[float], list[float]]:
    """(dB, group delay s): power average of the impulse responses' bands."""
    bands = [point_bands(ir, sample_rate, frequencies) for ir in irs]
    db, gd = [], []
    for i in range(len(frequencies)):
        powers = [b[i][0] for b in bands]
        total = math.fsum(powers)
        db.append(power_db(total / len(bands)))
        gd.append(
            math.fsum(p * b[i][1] for p, b in zip(powers, bands)) / total
            if total
            else math.fsum(b[i][1] for b in bands) / len(bands)
        )
    return db, gd
