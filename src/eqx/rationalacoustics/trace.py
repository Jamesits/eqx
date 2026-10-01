"""Reader and writer for Smaart trace files (``*.trf``, ``*.srf``); reader for
reference files of Smaart 7 and older (``*.ref``).

A captured transfer function (``.trf``) or spectrum (``.srf``) trace of
Smaart 9, little endian::

    0    "JACKREF!", float32 file version 1.0, uint32 header offset (16)
    16   trace header, 664 bytes (float32 version 2.0, kind, names, settings,
         point count, data offsets)
         optional chunks: "MTWBEAST" (the MTW data set), "MICCORRT" (the
         microphone correction), "EXTNDBLE"
         data: float32 frequencies (MTW only), float64 arrays

Transfer function data: magnitude dB, real and imaginary part, optional
coherence.  A real part of 1234.5678 marks a bin without data (below the
coherence threshold).  Spectrum data: power, optional peak hold power.

A reference file holds a spectrum or a transfer function::

    0    01 23 45 67 89 AB CD EF, "REF" or "SIA" (28 bytes)
    28   header, 136 bytes ("REFH", version, sample rate, FFT size, type, ...);
         version 5.0 and newer: 80 more bytes
         chunks: 4-byte tag, uint32 size; "REFD" / "4.5D" magnitude dB,
         "REFP" / "4.5P" phase degrees, "5.0E" coherence: float32 per bin

Bin 0 (DC) is not shown by Smaart.  Rational Acoustics publishes no
specification; the layouts are taken from Smaart 9.6.4.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import dsp
from ..curve import log_resample
from ..fileformat import Format, Inspector, file_section, frequency_range
from ..options import Option
from ..report import Section, Table

MAGIC = b"JACKREF!"
# Reference files of Smaart 7 and older (``.ref``).
LEGACY_MAGIC = bytes.fromhex("0123456789abcdef")
LEGACY_NAMES = (b"REF", b"SIA")
LEGACY_PREFIX, LEGACY_HEADER, LEGACY_EXTRA = 28, 136, 80
LEGACY_HEADER_TAG = b"REFH"
# Version 5.0 and newer have the extra header bytes.
LEGACY_EXTRA_VERSION = 0x500
LEGACY_SPECTRUM, LEGACY_TRANSFER_FUNCTION = 1, 2
# Chunk tags; the first one names the data.
LEGACY_MAGNITUDE, LEGACY_PHASE = (b"REFD", b"4.5D"), (b"REFP", b"4.5P")
LEGACY_COHERENCE = (b"5.0E",)
# Magnitude of a bin without data.
LEGACY_BLANK = 100000.0
# Window numbers of reference files.
LEGACY_WINDOWS = {1: "Hamming", 2: "Hann", 3: "Blackman", 4: "Blackman-Harris", 5: "Flat Top",
                  6: "Parzen", 7: "Welch", 8: "Tukey"}
FILE_VERSION, HEADER_VERSION = 1.0, 2.0
FILE_HEADER_SIZE, HEADER_SIZE = 16, 664
MTW_MAGIC, MTW_SIZE = b"MTWBEAST", 36
MIC_MAGIC, MIC_SIZE = b"MICCORRT", 20
EXTENDED_MAGIC, EXTENDED_SIZE = b"EXTNDBLE", 16
BLANK = 1234.5678
SPECTRUM, TRANSFER_FUNCTION, LIVE_SPECTRUM, LIVE_TRANSFER_FUNCTION = range(4)
KINDS = {SPECTRUM: "spectrum", TRANSFER_FUNCTION: "transfer function",
         LIVE_SPECTRUM: "live average spectrum",
         LIVE_TRANSFER_FUNCTION: "live average transfer function"}
EXTENSIONS = {SPECTRUM: ".srf", TRANSFER_FUNCTION: ".trf", LIVE_SPECTRUM: ".srf",
              LIVE_TRANSFER_FUNCTION: ".trf"}
# FFT field values other than an FFT size.
FFT_MTW_LEGACY, FFT_MTW, FFT_FPPO = 1, 2, 0xFFFF
# Average type labels of Smaart's trace info.
TRANSFER_FUNCTION_AVERAGES = {0: "Complex"}
SPECTRUM_AVERAGES = {0: "Magnitude", 1: "Power", 2: "dB"}
# Written traces: Smaart's version fields of the analysed build.
APP_VERSION = (9, 6, 4, 0)
COLOR = (1.0, 0.5, 0.0)

# Header strings: offset, size with the terminating NUL.
_STRINGS = {
    "name": (24, 33), "time": (57, 19), "measurement": (109, 33),
    "mea_device": (142, 33), "ref_device": (175, 33), "mea_info": (208, 33),
    "ref_info": (241, 33), "mea_channel": (274, 33), "ref_channel": (307, 33),
    "averaging": (348, 33), "window": (404, 33), "comment": (437, 129),
    "lir_averaging": (588, 33),
}
# Header data offsets: frequencies, magnitude, real, imaginary, coherence,
# peak, the two Live IR arrays.
_OFFSETS = 632
_ARRAYS = ("frequencies", "magnitude", "real", "imag", "coherence", "peak", "lir0", "lir1")

MTW_OPTION = Option("--mtw", action="store_true",
                    help="convert the MTW data set of the trace (default: the main data set)")
CALIBRATED_OPTION = Option(
    "--calibrated", action="store_true",
    help="add the calibration offset of a spectrum trace, as Smaart's Plot Calibrated Levels "
         "(default: dB as stored)")


@dataclass
class DataSet:
    """One resolution of a trace; bin 0 is DC."""

    frequencies: list[float]                # Hz
    magnitude: list[float]                  # transfer function: dB; spectrum: power
    real: list[float] = field(default_factory=list)     # transfer function; BLANK: no data
    imag: list[float] = field(default_factory=list)
    coherence: list[float] | None = None
    peak: list[float] | None = None         # spectrum: peak hold power
    mic_correction: list[float] | None = None   # transfer function: dB per bin


@dataclass
class Trace:
    kind: int                               # KINDS
    sample_rate: int
    fft: int                                # FFT size, FFT_MTW, FFT_MTW_LEGACY, FFT_FPPO
    data: DataSet
    mtw: DataSet | None = None              # transfer function: the MTW data set
    name: str = ""
    measurement: str = ""
    comment: str = ""
    time_ms: int = 0                        # capture time, ms since 1970
    app_version: tuple[int, int, int, int] = APP_VERSION
    mea_device: str = ""
    ref_device: str = ""
    mea_info: str = ""
    ref_info: str = ""
    mea_channel: str = ""
    ref_channel: str = ""
    averaging: str = ""
    average_type: int = 0
    bit_depth: int = 24
    calibration_db: float = 0.0             # spectrum: Smaart's calibration offset
    magnitude_threshold_db: float = 0.0     # transfer function
    delay_ms: float = 0.0                   # transfer function: measurement time reference
    window: str = "None"
    color: tuple[float, float, float] = COLOR
    lir: tuple[list[float] | None, list[float] | None] = (None, None)
    lir_averaging: str = ""
    peak_hold_s: int = 0
    peak_hold_averaged: bool = False
    extended: int = 0                       # the EXTNDBLE value
    reference_file: bool = False            # read from a Smaart 7 or older .ref file

    @property
    def transfer_function(self) -> bool:
        return self.kind in (TRANSFER_FUNCTION, LIVE_TRANSFER_FUNCTION)

    def data_set(self, mtw: bool = False) -> DataSet:
        if not mtw:
            return self.data
        if self.mtw is None:
            raise ValueError(f"{self.name or 'trace'}: no MTW data set")
        return self.mtw


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def load(path) -> Trace:
    path = Path(path)
    return read(path.read_bytes(), path.name)


def read(data: bytes, name: str = "") -> Trace:
    where = name or "trace"
    if data[:8] == LEGACY_MAGIC:
        return read_reference(data, name)
    if len(data) < FILE_HEADER_SIZE or data[:8] != MAGIC:
        raise ValueError(f"{where}: not a Smaart trace file (no JACKREF! magic)")
    (start,) = struct.unpack_from("<I", data, 12)
    if start + HEADER_SIZE > len(data):
        raise ValueError(f"{where}: truncated header")
    h = data[start:start + HEADER_SIZE]
    (version,) = struct.unpack_from("<f", h, 0)
    if version != HEADER_VERSION:
        raise ValueError(f"{where}: trace header version {version:g} is not supported")
    u32 = lambda offset: struct.unpack_from("<I", h, offset)[0]
    i32 = lambda offset: struct.unpack_from("<i", h, offset)[0]
    f32 = lambda offset: struct.unpack_from("<f", h, offset)[0]
    text = {k: _text(h[o:o + n]) for k, (o, n) in _STRINGS.items()}
    kind = u32(4)
    if kind not in KINDS:
        raise ValueError(f"{where}: unknown trace kind {kind}")
    n, fft, rate = i32(580), i32(340), i32(388)
    if n <= 0 or rate <= 0:
        raise ValueError(f"{where}: no points or no sample rate")
    offsets = dict(zip(_ARRAYS, struct.unpack_from("<8I", h, _OFFSETS)))

    chunks = _chunks(data, start + HEADER_SIZE, where)
    tf = kind in (TRANSFER_FUNCTION, LIVE_TRANSFER_FUNCTION)
    if tf and fft in (FFT_MTW, FFT_MTW_LEGACY):
        frequencies = _array(data, offsets["frequencies"], n, "f", where, "frequencies")
    elif fft == FFT_FPPO:
        raise ValueError(f"{where}: fixed points per octave (FPPO) data is not supported")
    elif fft > 0:
        frequencies = [i * rate / fft for i in range(n)]
    else:
        raise ValueError(f"{where}: no FFT size")
    doubles = lambda key, count=n: _array(data, offsets[key], count, "d", where, key)
    optional = lambda key, count=n: doubles(key, count) if offsets[key] else None
    mic = chunks.get(MIC_MAGIC)
    if tf:
        main = DataSet(frequencies, doubles("magnitude"), doubles("real"), doubles("imag"),
                       optional("coherence"),
                       mic_correction=_array(data, mic[0], n, "d", where, "mic correction")
                       if mic and mic[0] else None)
    else:
        main = DataSet(frequencies, doubles("magnitude"), peak=optional("peak"))
    mtw = None
    if MTW_MAGIC in chunks:
        count, *at = chunks[MTW_MAGIC]
        get = lambda i, code, what: _array(data, at[i], count, code, where, f"MTW {what}")
        mtw = DataSet(get(0, "f", "frequencies"), get(1, "d", "magnitude"),
                      get(2, "d", "real"), get(3, "d", "imaginary part"),
                      get(4, "d", "coherence") if at[4] else None,
                      mic_correction=_array(data, mic[1], count, "d", where, "MTW mic correction")
                      if mic and mic[1] else None)
    lir_count = i32(584)
    return Trace(
        kind=kind, sample_rate=rate, fft=fft, data=main, mtw=mtw,
        name=text["name"], measurement=text["measurement"], comment=text["comment"],
        time_ms=_int(text["time"]), app_version=struct.unpack_from("<4i", h, 8),
        mea_device=text["mea_device"], ref_device=text["ref_device"],
        mea_info=text["mea_info"], ref_info=text["ref_info"],
        mea_channel=text["mea_channel"], ref_channel=text["ref_channel"],
        averaging=text["averaging"], average_type=i32(384), bit_depth=i32(392),
        calibration_db=f32(396), magnitude_threshold_db=f32(344), delay_ms=f32(400),
        window=text["window"], color=struct.unpack_from("<3f", h, 568),
        lir=(optional("lir0", lir_count) if lir_count > 0 else None,
             optional("lir1", lir_count) if lir_count > 0 else None),
        lir_averaging=text["lir_averaging"],
        peak_hold_s=i32(624), peak_hold_averaged=bool(h[628]),
        extended=chunks.get(EXTENDED_MAGIC, 0),
    )


def read_reference(data: bytes, name: str = "") -> Trace:
    """A reference file of Smaart 7 or older; ``name`` (the file name) names the trace."""
    where = name or "reference file"
    if data[:8] != LEGACY_MAGIC or data[8:LEGACY_PREFIX].split(b"\0", 1)[0] not in LEGACY_NAMES:
        raise ValueError(f"{where}: not a Smaart reference file")
    end = LEGACY_PREFIX + LEGACY_HEADER
    h = data[LEGACY_PREFIX:end]
    if len(h) < LEGACY_HEADER or LEGACY_HEADER_TAG not in h.split(b"\0", 1)[0]:
        raise ValueError(f"{where}: no REFH header")
    version, size, rate, fft, kind = struct.unpack_from("<HHIHH", h, 8)
    if fft == FFT_FPPO:
        raise ValueError(f"{where}: fixed points per octave (FPPO) data is not supported")
    if not fft or rate <= 0:
        raise ValueError(f"{where}: no FFT size or no sample rate")
    if size != 4:
        raise ValueError(f"{where}: {size}-byte values are not supported")
    n = fft // 2 + 1
    position = end + (LEGACY_EXTRA if version >= LEGACY_EXTRA_VERSION else 0)
    arrays: dict[bytes, list[float]] = {}
    # Data chunks hold one float32 per bin, whatever their size field says.
    while position + 8 <= len(data):
        tag, chunk = data[position:position + 4], struct.unpack_from("<I", data, position + 4)[0]
        position += 8
        key = next((k for k in (LEGACY_MAGNITUDE, LEGACY_PHASE, LEGACY_COHERENCE) if tag in k),
                   None)
        if key is None:
            position += chunk
            continue
        if position + 4 * n > len(data):
            raise ValueError(f"{where}: truncated {tag.decode(errors='replace')} chunk")
        arrays[key[0]] = list(struct.unpack_from(f"<{n}f", data, position))
        position += 4 * n
    if LEGACY_MAGNITUDE[0] not in arrays:
        raise ValueError(f"{where}: no magnitude chunk")
    db = arrays[LEGACY_MAGNITUDE[0]]
    frequencies = [i * rate / fft for i in range(n)]
    live = struct.unpack_from("<H", h, 104)[0] == 1
    window, averages = struct.unpack_from("<HB", h, 108)
    common = dict(sample_rate=rate, fft=fft, name=Path(name).stem, reference_file=True,
                  app_version=(version >> 8, version & 0xFF, 0, 0), comment=_text(h[24:104]),
                  window=LEGACY_WINDOWS.get(window, "None"),
                  averaging=str(averages) if averages > 1 else "None")
    if kind != LEGACY_TRANSFER_FUNCTION:
        power = [10 ** (v / 10) for v in db]
        return Trace(LIVE_SPECTRUM if live else SPECTRUM, data=DataSet(frequencies, power),
                     **common)
    phase = arrays.get(LEGACY_PHASE[0], [0.0] * n)
    coherence = arrays.get(LEGACY_COHERENCE[0])
    if coherence is not None and not any(coherence):
        coherence = None
    # Smaart squares the dB value for its real and imaginary parts; only the
    # phase of those is shown.
    real, imag = [], []
    for v, p in zip(db, phase):
        g = 10 ** (v / 20) if v != LEGACY_BLANK else 0.0
        real.append(g * math.cos(math.radians(p)) if v != LEGACY_BLANK else BLANK)
        imag.append(g * math.sin(math.radians(p)))
    return Trace(LIVE_TRANSFER_FUNCTION if live else TRANSFER_FUNCTION,
                 data=DataSet(frequencies, db, real, imag, coherence),
                 average_type=0 if h[128] == 3 else 1,
                 delay_ms=struct.unpack_from("<f", h, 112)[0], **common)


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def _array(data: bytes, offset: int, count: int, code: str, where: str,
           what: str) -> list[float]:
    size = struct.calcsize(code) * count
    if offset <= 0 or offset + size > len(data):
        raise ValueError(f"{where}: {what} outside the file")
    return list(struct.unpack_from(f"<{count}{code}", data, offset))


def _chunks(data: bytes, offset: int, where: str) -> dict:
    """The chunks after the header: MTW (count, 5 offsets), mic correction (2
    offsets), EXTNDBLE value."""
    out: dict = {}
    while offset + 12 <= len(data):
        magic, size = data[offset:offset + 8], struct.unpack_from("<I", data, offset + 8)[0]
        if magic == MTW_MAGIC and size == MTW_SIZE:
            out[magic] = struct.unpack_from("<6I", data, offset + 12)
        elif magic == MIC_MAGIC and size == MIC_SIZE:
            out[magic] = struct.unpack_from("<2I", data, offset + 12)
        elif magic == EXTENDED_MAGIC and size >= 12:
            value = data[offset + 12:offset + min(size, 16)]
            out[magic] = int.from_bytes(value.ljust(4, b"\0"), "little")
        else:
            break
        if offset + size > len(data):
            raise ValueError(f"{where}: truncated {magic.decode()} chunk")
        offset += size
    return out


# --------------------------------------------------------------------------
# curves
# --------------------------------------------------------------------------
def power_db(power: float) -> float | None:
    return 10 * math.log10(power) if power > 0 else None


def rows(trace: Trace, mtw: bool = False) -> tuple[list[str], list[tuple]]:
    """(columns, rows) of the points Smaart shows: bin 0 and transfer function
    bins without data are left out.  Spectrum power is in dB."""
    d = trace.data_set(mtw)
    out = []
    if trace.transfer_function:
        columns = ["frequency Hz", "dB", "phase deg"]
        if d.coherence is not None:
            columns.append("coherence")
        for i in range(1, len(d.frequencies)):
            if d.real[i] == BLANK or d.frequencies[i] <= 0:
                continue
            row = (d.frequencies[i], d.magnitude[i], math.degrees(math.atan2(d.imag[i], d.real[i])))
            out.append(row + ((d.coherence[i],) if d.coherence is not None else ()))
        return columns, out
    columns = ["frequency Hz", "dB"] + (["peak dB"] if d.peak is not None else [])
    for i in range(1, len(d.frequencies)):
        db = power_db(d.magnitude[i])
        if db is None:
            continue
        peak = (power_db(d.peak[i]),) if d.peak is not None else ()
        out.append((d.frequencies[i], db) + peak)
    return columns, out


def curve(trace: Trace, mtw: bool = False, calibrated: bool = False) -> list[tuple[float, float]]:
    """(frequency, dB) points; ``calibrated`` adds a spectrum's calibration offset."""
    if calibrated and trace.transfer_function:
        raise ValueError("--calibrated applies to spectrum traces only")
    offset = trace.calibration_db if calibrated else 0.0
    _, table = rows(trace, mtw)
    if len(table) < 2:
        raise ValueError(f"{trace.name or 'trace'}: fewer than two points with data")
    return [(r[0], r[1] + offset) for r in table]


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def _bins(points, sample_rate: int, fft: int) -> tuple[list[float], list[float]]:
    """(frequencies, dB) of the bins of ``fft``: ``points`` linear in log
    frequency, clamped to the end values."""
    if fft < 4 or fft & (fft - 1):
        raise ValueError("the FFT size must be a power of two")
    frequencies = [i * sample_rate / fft for i in range(fft // 2 + 1)]
    return frequencies, [points[0][1]] + log_resample(points, frequencies[1:])


def transfer_function(points, sample_rate: int, fft: int) -> DataSet:
    """The minimum-phase transfer function of (frequency, dB) ``points`` on the
    bins of ``fft``; coherence 1."""
    frequencies, db = _bins(points, sample_rate, fft)
    n = len(frequencies)
    spectrum = dsp.fft(dsp.minimum_phase([10 ** (g / 20) for g in db]))[:n]
    return DataSet(frequencies, db, [h.real for h in spectrum], [h.imag for h in spectrum],
                   [1.0] * n)


def spectrum(points, sample_rate: int, fft: int, offset_db: float = 0.0) -> DataSet:
    """The power of (frequency, dB) ``points`` less ``offset_db`` on the bins of ``fft``."""
    frequencies, db = _bins(points, sample_rate, fft)
    return DataSet(frequencies, [10 ** ((g - offset_db) / 10) for g in db])


def write(trace: Trace) -> bytes:
    """A trace file.  Chunks and arrays are laid out as Smaart writes them;
    strings longer than their field are cut."""
    d, tf = trace.data, trace.transfer_function
    n = len(d.frequencies)
    mtw = trace.mtw if tf else None
    mtw_fft = tf and trace.fft in (FFT_MTW, FFT_MTW_LEGACY)
    _check(d, n, tf, "data")
    if mtw is not None:
        _check(mtw, len(mtw.frequencies), True, "MTW data")
    if not tf and (trace.fft <= 0 or trace.fft == FFT_FPPO):
        raise ValueError("a spectrum trace needs an FFT size")
    lir = [a for a in trace.lir if a is not None]
    if len({len(a) for a in lir}) > 1:
        raise ValueError("the Live IR arrays differ in length")

    mic = tf and (d.mic_correction is not None or (mtw is not None
                                                   and mtw.mic_correction is not None))
    position = (FILE_HEADER_SIZE + HEADER_SIZE + (MTW_SIZE if mtw is not None else 0)
                + (MIC_SIZE if mic else 0) + EXTENDED_SIZE)
    body = bytearray()
    offsets = dict.fromkeys(_ARRAYS, 0)

    def put(values, code="d") -> int:
        nonlocal position
        at = position
        body.extend(struct.pack(f"<{len(values)}{code}", *values))
        position += struct.calcsize(code) * len(values)
        return at

    if mtw_fft:
        offsets["frequencies"] = put(d.frequencies, "f")
    offsets["magnitude"] = put(d.magnitude)
    if tf:
        offsets["real"], offsets["imag"] = put(d.real), put(d.imag)
        if d.coherence is not None:
            offsets["coherence"] = put(d.coherence)
        for key, values in zip(("lir0", "lir1"), trace.lir):
            if values is not None:
                offsets[key] = put(values)
    elif d.peak is not None:
        offsets["peak"] = put(d.peak)
    chunks = bytearray()
    if mtw is not None:
        at = [put(mtw.frequencies, "f"), put(mtw.magnitude), put(mtw.real), put(mtw.imag),
              put(mtw.coherence) if mtw.coherence is not None else 0]
        chunks += MTW_MAGIC + struct.pack("<7I", MTW_SIZE, len(mtw.frequencies), *at)
    if mic:
        at = [put(s.mic_correction) if s is not None and s.mic_correction is not None else 0
              for s in (d, mtw)]
        chunks += MIC_MAGIC + struct.pack("<3I", MIC_SIZE, *at)
    chunks += EXTENDED_MAGIC + struct.pack("<2I", EXTENDED_SIZE, trace.extended)

    h = bytearray(HEADER_SIZE)
    struct.pack_into("<fI4i", h, 0, HEADER_VERSION, trace.kind, *trace.app_version)
    values = {k: getattr(trace, k) for k in _STRINGS if k != "time"}
    values["time"] = str(trace.time_ms)
    for key, (offset, size) in _STRINGS.items():
        raw = values[key].encode("utf-8")[:size - 1].decode("utf-8", errors="ignore").encode()
        h[offset:offset + len(raw)] = raw
    struct.pack_into("<if", h, 340, trace.fft, trace.magnitude_threshold_db)
    struct.pack_into("<iiiff", h, 384, trace.average_type, trace.sample_rate, trace.bit_depth,
                     trace.calibration_db, trace.delay_ms)
    struct.pack_into("<3fii", h, 568, *trace.color, n, len(lir[0]) if lir else 0)
    struct.pack_into("<iB", h, 624, trace.peak_hold_s, trace.peak_hold_averaged)
    struct.pack_into("<8I", h, _OFFSETS, *(offsets[k] for k in _ARRAYS))
    head = MAGIC + struct.pack("<fI", FILE_VERSION, FILE_HEADER_SIZE)
    return bytes(head + h + chunks + body)


def _check(d: DataSet, n: int, tf: bool, what: str) -> None:
    lengths = [len(d.magnitude)]
    if tf:
        lengths += [len(d.real), len(d.imag)]
    lengths += [len(a) for a in (d.coherence, d.peak, d.mic_correction) if a is not None]
    if n < 2 or any(length != n for length in lengths):
        raise ValueError(f"{what}: needs at least two points and arrays of equal length")


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
def fft_text(trace: Trace) -> str:
    if trace.fft == FFT_MTW:
        return "MTW"
    if trace.fft == FFT_MTW_LEGACY:
        return "MTW (legacy)"
    if trace.fft == FFT_FPPO:
        return "FPPO"
    return f"{trace.fft // 1024}k" if trace.fft > 512 else str(trace.fft)


def average_text(trace: Trace) -> str:
    if trace.transfer_function:
        label = TRANSFER_FUNCTION_AVERAGES.get(trace.average_type, "Polar")
    else:
        label = SPECTRUM_AVERAGES.get(trace.average_type, "unknown")
    return f"{trace.average_type} ({label})"


def _data_section(trace: Trace, title: str, mtw: bool) -> Section:
    d = trace.data_set(mtw)
    columns, table = rows(trace, mtw)
    blank = (sum(1 for v in d.real[1:] if v == BLANK) if trace.transfer_function else None)
    return Section(title, [("bins", len(d.frequencies)), ("bins without data", blank or None),
                           ("range", frequency_range(table))],
                   Table(columns, table))


class SmaartTraceInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        t = read(data, path.name)
        tf = t.transfer_function
        when = (datetime.fromtimestamp(t.time_ms / 1000, timezone.utc).isoformat()
                if t.time_ms > 0 else None)
        lir = next((a for a in t.lir if a is not None), None)
        if t.reference_file:
            sections = [file_section(
                path, data, ("kind", f"{KINDS[t.kind]}, reference file of Smaart 7 or older"),
                ("comment", t.comment), ("version", f"{t.app_version[0]}.{t.app_version[1]}"),
                ("sample rate", t.sample_rate), ("FFT", fft_text(t)), ("window", t.window),
                ("averages", t.averaging),
                ("average type", average_text(t) if tf else None),
                ("delay ms", t.delay_ms if tf else None))]
            return sections + [_data_section(t, "data", False)]
        sections = [file_section(
            path, data, ("kind", KINDS[t.kind]), ("name", t.name), ("measurement", t.measurement),
            ("comment", t.comment), ("captured", when),
            ("version", ".".join(map(str, t.app_version))),
            ("sample rate", t.sample_rate), ("bit depth", t.bit_depth), ("FFT", fft_text(t)),
            ("window", t.window), ("averaging", t.averaging), ("average type", average_text(t)),
            ("measurement device", t.mea_device), ("measurement channel", t.mea_channel),
            ("measurement info", t.mea_info), ("reference device", t.ref_device),
            ("reference channel", t.ref_channel), ("reference info", t.ref_info),
            ("delay ms", t.delay_ms if tf else None),
            ("magnitude threshold dB", t.magnitude_threshold_db if tf else None),
            ("calibration offset dB", None if tf else t.calibration_db),
            ("peak hold s", t.peak_hold_s or None),
            ("peak hold averaged", t.peak_hold_averaged or None),
            ("color", " ".join(f"{v:g}" for v in t.color)),
            ("Live IR", f"{len(lir)} values, averaging {t.lir_averaging or '-'}" if lir else None),
            ("EXTNDBLE", t.extended))]
        sections.append(_data_section(t, "data", False))
        if t.mtw is not None:
            sections.append(_data_section(t, "MTW data", True))
        for title, s in (("mic correction", t.data), ("MTW mic correction", t.mtw)):
            if s is not None and s.mic_correction is not None:
                points = list(zip(s.frequencies[1:], s.mic_correction[1:]))
                sections.append(Section(title, [("range", frequency_range(points))],
                                        Table(["frequency Hz", "dB"], points)))
        return sections


# One reader, three formats: the output extension selects the written kind.
FORMATS = (
    Format("smaart-trf", (".trf",), "Smaart transfer function trace", SmaartTraceInspector),
    Format("smaart-srf", (".srf",), "Smaart spectrum trace", SmaartTraceInspector),
    Format("smaart-ref", (".ref",), "Smaart 7 or older reference file", SmaartTraceInspector),
)
