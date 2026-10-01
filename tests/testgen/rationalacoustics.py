"""Smaart traces, ASCII exports and curves."""

from __future__ import annotations

import math
import struct

from eqx import dsp
from eqx.rationalacoustics import trace

from .common import bells, high_cut, hp2, peak, response

TRACE_DIR = "rationalacoustics/smaart-trace"
ASCII_DIR = "rationalacoustics/smaart-ascii"
CURVE_DIR = "rationalacoustics/smaart-curve"
# 2026-01-01 00:00 UTC.
TIME_MS = 1767225600000
# Coherence of the transfer functions: falls below the blanking threshold
# at low frequencies.
THRESHOLD = 0.5
# The microphone of the correction arrays, dB.
MIC_SECTIONS = [hp2(14, 0.6), peak(9000, 1.5, 1.2)]
LIR_LENGTH = 256
# MTW frequencies: 1/24 octave, 20 Hz-20 kHz; index 0 is DC.
MTW_FREQUENCIES = [0.0] + [20 * 2 ** (k / 24) for k in range(240)]
# Spectrum level: -3 dB per octave from 1 kHz plus the Left bells, dB re
# full scale; peak hold 3 dB above.
RTA_DB_1K, RTA_PEAK_DB, RTA_CALIBRATION_DB = -20.0, 3.0, 100.0


def coherence(f: float) -> float:
    return 1.0 if f <= 0 else 1 - 0.9 * math.exp(-f / 100)


def tf_point(side: str, f: float, rate: int) -> complex:
    h = 1 + 0j
    for bq in bells(side, rate):
        h *= bq.h(f, rate)
    return h


def tf_data(
    side: str,
    frequencies: list[float],
    rate: int,
    mic: bool = False,
    coherent: bool = True,
) -> trace.DataSet:
    """The bells of ``side``.  With coherence, bins below the threshold have no data."""
    h = [tf_point(side, f, rate) for f in frequencies]
    coh = [coherence(f) for f in frequencies] if coherent else None
    real = [
        v.real if coh is None or c >= THRESHOLD else trace.BLANK
        for v, c in zip(h, coh or h)
    ]
    mic_db = [response(MIC_SECTIONS, f)[0] if f > 0 else 0.0 for f in frequencies]
    return trace.DataSet(
        frequencies,
        [20 * math.log10(abs(v)) for v in h],
        real,
        [v.imag for v in h],
        coh,
        mic_correction=mic_db if mic else None,
    )


def rta_db(f: float, rate: int) -> float:
    return (
        RTA_DB_1K
        - 3 * math.log2(f / 1000)
        + dsp.cascade_db(bells("Left", rate), f, rate)
    )


def write_tf_left() -> bytes:
    """48 kHz, FFT 4096, coherence, an MTW data set, mic correction, Live IR."""
    rate, fft = 48000, 4096
    frequencies = [i * rate / fft for i in range(fft // 2 + 1)]
    lir = [math.exp(-i / 20) * math.cos(i / 3) for i in range(LIR_LENGTH)]
    return trace.write(
        trace.Trace(
            trace.TRANSFER_FUNCTION,
            rate,
            fft,
            tf_data("Left", frequencies, rate, True),
            mtw=tf_data("Left", MTW_FREQUENCIES, rate, True),
            name="Left",
            measurement="Main L",
            comment="Left speaker, delay found",
            time_ms=TIME_MS,
            mea_device="Interface A",
            ref_device="Interface A",
            mea_info="Mic",
            ref_info="Loopback",
            mea_channel="1",
            ref_channel="2",
            averaging="Fixed 16",
            average_type=1,
            delay_ms=8.75,
            magnitude_threshold_db=-60.0,
            window="Hann",
            color=(0.0, 0.5, 1.0),
            lir=(lir, [abs(v) for v in lir]),
            lir_averaging="Fixed 4",
            extended=1,
        )
    )


def write_tf_mtw() -> bytes:
    """44.1 kHz, the main data set is MTW, no coherence."""
    rate = 44100
    return trace.write(
        trace.Trace(
            trace.TRANSFER_FUNCTION,
            rate,
            trace.FFT_MTW,
            tf_data("Right", MTW_FREQUENCIES, rate, coherent=False),
            name="Right MTW",
            time_ms=TIME_MS,
        )
    )


def write_rta() -> bytes:
    """44.1 kHz, FFT 4096, peak hold, a calibration offset."""
    rate, fft = 44100, 4096
    frequencies = [i * rate / fft for i in range(fft // 2 + 1)]
    power = [0.0] + [10 ** (rta_db(f, rate) / 10) for f in frequencies[1:]]
    return trace.write(
        trace.Trace(
            trace.SPECTRUM,
            rate,
            fft,
            trace.DataSet(
                frequencies, power, peak=[p * 10 ** (RTA_PEAK_DB / 10) for p in power]
            ),
            name="Rta",
            time_ms=TIME_MS,
            mea_device="Interface A",
            mea_channel="1",
            averaging="Exp 2s",
            average_type=1,
            calibration_db=RTA_CALIBRATION_DB,
            window="Hann",
            peak_hold_s=3,
            peak_hold_averaged=True,
        )
    )


def _ref(
    name: bytes,
    version: int,
    rate: int,
    fft: int,
    kind: int,
    comment: str,
    chunks: list[tuple[bytes, bytes]],
    window: int = 0,
    averages: int = 1,
    delay_ms: float = 0.0,
    average_type: int = 0,
) -> bytes:
    """A reference file of Smaart 7 or older."""
    prefix = trace.LEGACY_MAGIC + name.ljust(trace.LEGACY_PREFIX - 8, b"\0")
    h = bytearray(trace.LEGACY_HEADER)
    h[:4] = trace.LEGACY_HEADER_TAG
    struct.pack_into("<HHIHH", h, 8, version, 4, rate, fft, kind)
    raw = comment.encode()
    h[24 : 24 + len(raw)] = raw
    struct.pack_into("<HBxf", h, 108, window, averages, delay_ms)
    h[128] = average_type
    extra = bytes(trace.LEGACY_EXTRA) if version >= trace.LEGACY_EXTRA_VERSION else b""
    body = b"".join(tag + struct.pack("<I", len(data)) + data for tag, data in chunks)
    return prefix + bytes(h) + extra + body


def _floats(values) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def write_ref_left() -> bytes:
    """Version 5.0 transfer function: an unknown chunk, phase before magnitude,
    coherence; bins below the threshold have the no-data magnitude."""
    rate, fft = 48000, 2048
    frequencies = [i * rate / fft for i in range(fft // 2 + 1)]
    h = [tf_point("Left", f, rate) for f in frequencies]
    db = [
        20 * math.log10(abs(v)) if coherence(f) >= THRESHOLD else trace.LEGACY_BLANK
        for f, v in zip(frequencies, h)
    ]
    return _ref(
        b"REF",
        0x500,
        rate,
        fft,
        trace.LEGACY_TRANSFER_FUNCTION,
        "Left, Smaart 6",
        [
            (b"NOTE", b"abcdef"),
            (b"4.5P", _floats([math.degrees(math.atan2(v.imag, v.real)) for v in h])),
            (b"REFD", _floats(db)),
            (b"5.0E", _floats([coherence(f) for f in frequencies])),
        ],
        window=2,
        averages=16,
        delay_ms=8.75,
        average_type=3,
    )


def write_ref_rta() -> bytes:
    """Version 4.0 spectrum: levels in dB."""
    rate, fft = 44100, 2048
    frequencies = [i * rate / fft for i in range(fft // 2 + 1)]
    db = [0.0] + [rta_db(f, rate) for f in frequencies[1:]]
    return _ref(
        b"SIA",
        0x400,
        rate,
        fft,
        trace.LEGACY_SPECTRUM,
        "Rta",
        [(b"4.5D", _floats(db))],
        window=1,
        averages=4,
    )


def write_tf_export() -> bytes:
    """Export to ASCII of Left and Right: 48 kHz, FFT 1024, CRLF."""
    rate, fft = 48000, 1024
    lines = [
        "".join(f"\t{side}\t\t" for side in ("Left", "Right")),
        "Frequency (Hz)" + "\tMagnitude (dB)\tPhase (degrees)\tCoherence" * 2,
    ]
    for i in range(1, fft // 2 + 1):
        f = i * rate / fft
        row = f"{f:.6f}"
        for side in ("Left", "Right"):
            h, c = tf_point(side, f, rate), coherence(f)
            if c < THRESHOLD:
                row += "\t*\t*\t*"
            else:
                row += (
                    f"\t{20 * math.log10(abs(h)):.2f}"
                    f"\t{math.degrees(math.atan2(h.imag, h.real)):.2f}\t{c:.2f}"
                )
        lines.append(row)
    return ("\r\n".join(lines) + "\r\n").encode()


def write_rta_export() -> bytes:
    """Export to ASCII of a spectrum with peak hold; decimal commas, no last line end."""
    rate, fft = 44100, 2048
    text = "Trace Name\tRta\t\r\nFrequency (Hz)\tLevel (dB)\tPeak (dB)\r\n"
    for i in range(1, fft // 2 + 1):
        f = i * rate / fft
        db = rta_db(f, rate)
        text += f"\r\n{f:.6f}\t{db:.2f}\t{db + RTA_PEAK_DB:.2f}".replace(".", ",")
    return text.encode()


def write_haystack() -> bytes:
    """A transfer function target curve with a tolerance, as Smaart's own files."""
    return (
        b"Line:\t3\r\nColor:\tffff15ee\r\nShow:\t1\r\nType:\tTF\r\nTolerance:\t3\r\n"
        b"Offset:\t0\r\n\r\n30.000000\t9.000000\r\n250.000000\t0.000000\r\n"
        b"20000.000000\t-2.000000\r\n"
    )


def write_house() -> bytes:
    """A spectrum target curve, 1/3 octave; rows out of order, a repeated frequency."""
    rows = [(1000, 75), (31.5, 85), (125, 80), (8000, 68), (1000, 70), (16000, 62)]
    return (
        "Line: 2\r\nColor: 80FF0000\r\nShow: 1\r\nBand: 3\r\n\r\n"
        + "".join(f"{f}\t{v}\r\n" for f, v in rows)
    ).encode()


def write_mic() -> bytes:
    """A microphone correction curve: an imported REW-style file, comma rows."""
    grid = [10 * 2 ** (k / 6) for k in range(62)]
    lines = ["* Mic TILT03", '"Sens Factor =-12dB, SERNO: TILT03"']
    lines += [
        f"{f:.3f},{response(MIC_SECTIONS + [high_cut(6000, -3)], f)[0]:.4f}"
        for f in grid
    ]
    return ("\r\n".join(lines) + "\r\n").encode()


def files() -> dict[str, bytes]:
    return {
        f"{TRACE_DIR}/Tf Left.trf": write_tf_left(),
        f"{TRACE_DIR}/Tf Mtw.trf": write_tf_mtw(),
        f"{TRACE_DIR}/Rta.srf": write_rta(),
        f"{TRACE_DIR}/Ref Left.ref": write_ref_left(),
        f"{TRACE_DIR}/Ref Rta.ref": write_ref_rta(),
        f"{ASCII_DIR}/Tf export.txt": write_tf_export(),
        f"{ASCII_DIR}/Rta export.txt": write_rta_export(),
        f"{CURVE_DIR}/Haystack.crv": write_haystack(),
        f"{CURVE_DIR}/House.crv": write_house(),
        f"{CURVE_DIR}/Mic.crv": write_mic(),
    }
