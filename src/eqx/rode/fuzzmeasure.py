"""Reader and writer for FuzzMeasure documents (``*.fume4``, ``*.fume3``, ``*.fume``).

FuzzMeasure 4 and 3 documents are packages (directories)::

    TopLevel.dat      keyed archive: FUMEVersion "3.0", MeasurementRecords, ...
    <UUID>.caf        impulse response of a measurement (FuzzMeasure 4)
    <UUID>            keyed archive of the impulse response (FuzzMeasure 3)
    thumb.png, preview.pdf

A FuzzMeasure 2 document (``*.fume``) is one keyed archive with the impulse
responses inside the measurement records.  Records of FuzzMeasure 4 have
lower-case keys and ``version`` 1; older ones capitalized keys.

An impulse response is circular: negative sample indices count from its
end.  The analysis window selects the part the frequency response is
computed from.  SuperMegaUltraGroovy publishes no specification; the layout
is taken from FuzzMeasure 4.2.2 and 3.3.3.
"""

from __future__ import annotations

import argparse
import bisect
import math
import struct
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import caf, impulse, keyedarchive
from ..fileformat import Format, Inspector, frequency_range, response_section
from ..keyedarchive import Instance, Ref
from ..options import Option
from ..report import Section, Table

TOP_LEVEL = "TopLevel.dat"
# Key of the first unkeyed value (encodeObject:, encodeValueOfObjCType:) in a keyed archive.
UNKEYED = "$0"
FUME_VERSION = "3.0"
KINDS = {".fume4": "FuzzMeasure 4", ".fume3": "FuzzMeasure 3", ".fume": "FuzzMeasure 2"}
# Analysis window shapes.  The half windows fall from 1 over the window.
WINDOW_TYPES = {0: "rectangular", 1: "Hamming", 2: "half Hamming", 3: "Hann", 4: "half Hann",
                5: "Bingham", 6: "half Bingham"}
HAMMING, HANN, BINGHAM_TAPER = 0.54, 0.5, 0.2
# The FFT spans the window, at least 32768 samples (2048 in compatibility
# mode, for FuzzMeasure 1 measurements).
MIN_FFT, MIN_FFT_COMPATIBLE = 32768, 2048
# FuzzMeasure's SPL graphs add 94 dB minus the reference level: the level
# the 94 dB SPL calibrator tone was recorded at, dB re full scale.
CALIBRATOR_DB = 94.0
SPEED_OF_SOUND = 340.29                     # m/s, FuzzMeasure's default
# NSColorSpace values: calibrated RGB, device RGB, calibrated white, device white.
RGB_SPACES, WHITE_SPACES = (1, 2), (3, 4)
# Written impulse responses: 32768 samples, window over the first half.
IR_LENGTH = 32768
# Written UUIDs are UUID 5 of a name: reproducible.
UUID_NAMESPACE = uuid.UUID("3d0c9f4e-2f61-5b7a-8e1d-6a4b0c2f9e57")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# A new SMUGLogSweepSettings: 1 s full-range sweep.
SWEEP = {"version": Ref(1), "Duration": 1000.0, "StartFreq": 1.0, "EndFreq": 20000.0,
         "BeginSilence": 0.0, "EndSilence": 0.0, "LeadIn": 0.0, "LeadOut": 0.0,
         "Name": "Untitled", "FullRangeSweep": True, "DefaultSilence": True, "amplitude": 1.0}
# The graph of written documents, with its default settings: FuzzMeasure's
# frequency response graph.
GRAPH_CLASSES = ["FuzzMeasureMagnitudeResponseGraph", "FuzzMeasureFrequencyDomainGraph",
                 "FuzzMeasureGraph", "SMUGGraph", "NSObject"]
# Plot colors of written measurements, RGB.
COLORS = ((0.85, 0.15, 0.15), (0.15, 0.35, 0.85), (0.1, 0.6, 0.2), (0.9, 0.55, 0.0),
          (0.55, 0.2, 0.75), (0.0, 0.6, 0.65), (0.5, 0.5, 0.5), (0.0, 0.0, 0.0))

MEASUREMENT_OPTION = Option(
    "--measurement",
    help="measurement: its number from 0 as listed by inspect, or its title, "
         "case-insensitive (default: 0)")
MIC_CALIBRATION_OPTION = Option(
    "--mic-calibration", action=argparse.BooleanOptionalAction,
    help="subtract the microphone calibration stored with the measurement, where FuzzMeasure "
         "applies it (default: on)")
SPL_OPTION = Option("--spl", action="store_true",
                    help="dB SPL as FuzzMeasure's SPL graphs: + 94 dB - the measurement's SPL "
                         "reference level (default: dB re full scale)")


@dataclass
class Calibration:
    """A microphone calibration record: the microphone's response."""

    name: str
    points: list[tuple[float, float]]       # (Hz, dB), the magnitude spline
    phase: list[tuple[float, float]] = field(default_factory=list)   # (Hz, degrees)
    serial: str | None = None
    sensitivity: float = 0.0
    uuid: str | None = None

    def db(self, frequencies) -> list[float | None]:
        """The spline at each frequency; None outside its range."""
        return spline([f for f, _ in self.points], [v for _, v in self.points], frequencies)


@dataclass
class Record:
    """One measurement."""

    title: str
    sample_rate: int
    ir: list[float]
    uuid: str
    # Analysis window: begin, end (samples from sample 0; negative indices
    # count from the end of the impulse response) and shape (WINDOW_TYPES).
    window: tuple[int, int, int] = (0, 0, 0)
    notes: str = ""
    date: datetime | None = None
    normalized: bool = False                # shown scaled to a peak of 1
    compatibility: bool = False
    start_hz: float = 0.0                   # shown frequency range
    end_hz: float = 0.0
    calibration: Calibration | None = None
    use_calibration: bool = False
    use_spl: bool = False
    spl_reference: float = 0.0              # dB re full scale of the 94 dB SPL tone
    averages: int = 1                       # synchronous averages
    speed_of_sound: float = SPEED_OF_SOUND
    color: tuple[float, float, float] | None = None
    sweep: dict[str, Any] = field(default_factory=dict)
    device: str | None = None
    version: int = 1                        # record coding: 1 FuzzMeasure 4, 0 older

    @property
    def fft_length(self) -> int:
        duration = self.window[1] - self.window[0]
        n = 1 << max(0, duration - 1).bit_length()
        return max(n, MIN_FFT_COMPATIBLE if self.compatibility else MIN_FFT)

    def peak_index(self) -> int:
        """Sample of the impulse peak, from -n/2 to n/2."""
        peak = impulse.peak_index(self.ir)
        return peak - len(self.ir) if peak >= len(self.ir) // 2 else peak

    def peak_ms(self) -> float:
        return self.peak_index() / self.sample_rate * 1000


@dataclass
class Document:
    kind: str                               # extension: .fume4, .fume3, .fume
    version: str                            # FUMEVersion; "1.x" if absent
    records: list[Record]
    top: dict[str, Any]                     # the resolved TopLevel archive
    entries: dict[str, int] = field(default_factory=dict)   # package file -> size

    def record(self, key: str | int | None) -> int:
        """Index of the measurement with this number or title (case-insensitive)."""
        if not self.records:
            raise ValueError("the document has no measurements")
        if key is None:
            return 0
        text = str(key).strip()
        if text.lstrip("-").isdigit():
            i = int(text)
            if not 0 <= i < len(self.records):
                raise ValueError(f"measurement {i} does not exist; measurements: "
                                 f"0-{len(self.records) - 1}")
            return i
        for i, r in enumerate(self.records):
            if r.title.lower() == text.lower():
                return i
        raise ValueError(f"no measurement {key!r}; available: "
                         f"{', '.join(repr(r.title) for r in self.records)}")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def load(path) -> Document:
    """A package directory (FuzzMeasure 4, 3) or a FuzzMeasure 2 file."""
    path = Path(path)
    kind = path.suffix.lower()
    if path.is_dir():
        files = {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}
        return read_package(files, kind if kind in (".fume4", ".fume3") else ".fume4")
    return read(path.read_bytes())


def read(data: bytes) -> Document:
    """A FuzzMeasure 2 document."""
    top = _unarchive(data, "FuzzMeasure 2 document")
    return _document(".fume", top, {}, {})


def read_package(files: dict[str, bytes], kind: str = ".fume4") -> Document:
    """A FuzzMeasure 4 or 3 package: file name -> contents."""
    if TOP_LEVEL not in files:
        raise ValueError(f"not a FuzzMeasure document: no {TOP_LEVEL}")
    top = _unarchive(files[TOP_LEVEL], TOP_LEVEL)
    return _document(kind, top, files, {name: len(data) for name, data in sorted(files.items())})


def _unarchive(data: bytes, name: str) -> dict[str, Any]:
    try:
        top = keyedarchive.unarchive(data)
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from None
    if not isinstance(top.get("MeasurementRecords"), list):
        raise ValueError(f"{name}: not a FuzzMeasure document: no MeasurementRecords")
    return top


def _document(kind: str, top: dict, files: dict[str, bytes], entries: dict[str, int]) -> Document:
    version = top.get("FUMEVersion")
    records = []
    for i, r in enumerate(top["MeasurementRecords"]):
        if not isinstance(r, Instance):
            raise ValueError(f"measurement record {i} is not an object")
        records.append(_record(r, files, i))
    return Document(kind, version if isinstance(version, str) else "1.x", records, top, entries)


def _record(r: Instance, files: dict[str, bytes], index: int) -> Record:
    # FuzzMeasure 4 writes ``version`` 1 and lower-case keys.
    v1 = r.get("version") is not None
    get = (lambda new, old: r.get(new)) if v1 else (lambda new, old: r.get(old))
    where = f"measurement {index}"
    rate = _number(get("sampleRate", "SampleRate"), 0)
    key = get("impulseUUID", "ImpulseUUID")
    inline = get("impulseResponse", "ImpulseResponse")
    if inline is not None:
        ir = _vector(inline, where)
    else:
        if not isinstance(key, str):
            raise ValueError(f"{where}: no impulse response and no impulse UUID")
        if f"{key}.caf" in files:
            file_rate, channels = caf.read(files[f"{key}.caf"], f"{key}.caf")
            ir = channels[0]
            rate = rate or file_rate
        elif key in files:
            # Written with the unkeyed encodeObject:, which takes the key $0.
            ir = _vector(keyedarchive.unarchive(files[key]).get(UNKEYED), key)
        else:
            raise ValueError(f"{where}: impulse response {key} is not in the document")
    if not rate:
        raise ValueError(f"{where}: no sample rate")
    if not ir:
        raise ValueError(f"{where}: empty impulse response")

    if v1:
        spl = _number(r.get("SPLReferenceLevel"), 0.0)
        use_spl = bool(r.get("useSPLReferenceLevel"))
    else:
        # Stored as a linear gain; 0 is no reference.
        linear = _number(r.get("SPLReferenceLevel"), 0.0)
        spl = 20 * math.log10(linear) if linear > 0 else 0.0
        use_spl = False
    calibration = get("correctionRecord", "CorrectionRecord")
    sweep = get("logSweepSettings", "LogSweepSettings")
    return Record(
        title=str(get("comment", "Comment") or ""),
        sample_rate=int(rate),
        ir=ir,
        uuid=key if isinstance(key, str) else "",
        window=_window(get("impulseResponseWindow", "ImpulseResponseWindow")),
        notes=str(get("notes", "Notes") or ""),
        date=get("date", "Date") if isinstance(get("date", "Date"), datetime) else None,
        normalized=bool(get("normalized", "Normalized")),
        # FuzzMeasure 1 data: FuzzMeasure decodes it in compatibility mode.
        compatibility=(bool(get("compatibilityMode", "CompatibilityMode"))
                       or isinstance(inline, bytes)),
        start_hz=_number(r.get("startFrequency"), 0.0),
        end_hz=_number(r.get("endFrequency"), 0.0),
        calibration=_calibration(calibration) if isinstance(calibration, Instance) else None,
        use_calibration=bool(get("correctionEnabled", "CorrectionEnabled")),
        use_spl=use_spl,
        spl_reference=spl,
        averages=int(_number(get("synchronousAverages", "SynchronousAverages"), 1)) or 1,
        speed_of_sound=_number(r.get("speedOfSound"), SPEED_OF_SOUND) if v1 else SPEED_OF_SOUND,
        color=_color(get("plotColor", "PlotColor")),
        sweep=dict(sweep.fields) if isinstance(sweep, Instance) else {},
        device=r.get("audioDeviceName") if isinstance(r.get("audioDeviceName"), str) else None,
        version=1 if v1 else 0,
    )


def _number(value: Any, default: float) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _vector(value: Any, where: str) -> list[float]:
    """Samples of an SMUGRealVector, or of FuzzMeasure 1 data (big-endian floats)."""
    if isinstance(value, Instance) and value.classname in ("SMUGRealVector",
                                                           "SMUGMutableRealVector"):
        data = value.get("VectorData")
        # CFByteOrder 1 is little endian; without it the data is big endian.
        order = "<" if value.get("CFByteOrder") == 1 else ">"
    elif isinstance(value, bytes):
        data, order = value, ">"
    else:
        raise ValueError(f"{where}: the impulse response is not a vector")
    if not isinstance(data, bytes) or len(data) % 4:
        raise ValueError(f"{where}: vector data is not float32 samples")
    return list(struct.unpack(f"{order}{len(data) // 4}f", data))


def _window(w: Any) -> tuple[int, int, int]:
    if not isinstance(w, Instance):
        return 0, 0, 0
    if w.get("Version") == "2.0":
        return int(w.get("Begin", 0)), int(w.get("End", 0)), int(w.get("Type", 0))
    # Version 1 is three unkeyed values ($0, $1, $2); the type is stored plus one.
    return int(w.get("$0", 0)), int(w.get("$1", 0)), max(0, int(w.get("$2", 1)) - 1)


def _color(c: Any) -> tuple[float, float, float] | None:
    if not isinstance(c, Instance) or c.classname != "NSColor":
        return None
    try:
        if c.get("NSColorSpace") in RGB_SPACES and isinstance(c.get("NSRGB"), bytes):
            r, g, b = [float(v) for v in c.get("NSRGB").rstrip(b"\0").split()[:3]]
            return r, g, b
        if c.get("NSColorSpace") in WHITE_SPACES and isinstance(c.get("NSWhite"), bytes):
            w = float(c.get("NSWhite").rstrip(b"\0").split()[0])
            return w, w, w
    except (ValueError, IndexError):
        pass
    return None


def _calibration(c: Instance) -> Calibration:
    """A MicrophoneCalibrationRecord (FuzzMeasure 4) or SMUGCorrectionRecord."""
    def points(s: Any) -> list[tuple[float, float]]:
        if not isinstance(s, Instance) or s.get("X") is None or s.get("Y") is None:
            return []
        return list(zip(_vector(s.get("X"), "spline"), _vector(s.get("Y"), "spline")))
    serial = c.get("SerialNumber")
    return Calibration(str(c.get("Name") or ""), points(c.get("MagnitudeSpline")),
                       points(c.get("PhaseSpline")), serial if isinstance(serial, str) else None,
                       _number(c.get("Sensitivity"), 0.0),
                       c.get("UUID") if isinstance(c.get("UUID"), str) else None)


# --------------------------------------------------------------------------
# response
# --------------------------------------------------------------------------
def spline(xs: list[float], ys: list[float], queries) -> list[float | None]:
    """FuzzMeasure's natural cubic spline through (xs, ys); None outside xs."""
    n = len(xs)
    if n < 2:
        return [None for _ in queries]
    y2, u = [0.0] * n, [0.0] * n
    for i in range(1, n - 1):
        sig = (xs[i] - xs[i - 1]) / (xs[i + 1] - xs[i - 1])
        p = sig * y2[i - 1] + 2
        y2[i] = (sig - 1) / p
        d = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) - (ys[i] - ys[i - 1]) / (xs[i] - xs[i - 1])
        u[i] = (6 * d / (xs[i + 1] - xs[i - 1]) - sig * u[i - 1]) / p
    for k in range(n - 2, -1, -1):
        y2[k] = y2[k] * y2[k + 1] + u[k]
    out = []
    for x in queries:
        if not xs[0] <= x <= xs[-1]:
            out.append(None)
            continue
        lo = min(max(bisect.bisect_right(xs, x) - 1, 0), n - 2)
        h = xs[lo + 1] - xs[lo]
        a, b = (xs[lo + 1] - x) / h, (x - xs[lo]) / h
        out.append(a * ys[lo] + b * ys[lo + 1]
                   + ((a ** 3 - a) * y2[lo] + (b ** 3 - b) * y2[lo + 1]) * h * h / 6)
    return out


def windowed(record: Record) -> list[float]:
    """The samples of the analysis window, shaped."""
    ir = record.ir
    if record.normalized:
        peak = max(abs(v) for v in ir) or 1.0
        ir = [v / peak for v in ir]
    n = len(ir)
    begin, end, shape = record.window
    if begin >= end or end > n // 2 or begin < -n:
        raise ValueError(f"{record.title!r}: empty analysis window ({begin}, {end}) "
                         f"for {n} samples")
    if begin >= 0:
        x = ir[begin:end]
    elif end <= 0:
        x = ir[n + begin:n + end]
    else:
        x = ir[n + begin:] + ir[:end]
    return shape_window(x, shape)


def shape_window(x: list[float], shape: int) -> list[float]:
    """FuzzMeasure's window shapes."""
    n = len(x)
    if shape in (1, 3):
        a = HAMMING if shape == 1 else HANN
        w = [a - (1 - a) * math.cos(2 * math.pi * i / (n - 1)) if n > 1 else 1.0
             for i in range(n)]
    elif shape in (2, 4):
        a = HAMMING if shape == 2 else HANN
        w = [a - (1 - a) * math.cos(2 * math.pi * (n + i) / (2 * n - 1)) for i in range(n)]
    elif shape == 5:
        # Tukey: cosine tapers over 10 % at each end.
        m = math.floor(0.5 * BINGHAM_TAPER * n)
        w = [1.0] * n
        width = (n + 1) * BINGHAM_TAPER
        for i in range(m):
            w[i] = 0.5 * (1 - math.cos(2 * math.pi * (i + 1) / width))
            w[n - m + i] = 0.5 * (1 - math.cos(2 * math.pi * (m - i) / width))
    elif shape == 6:
        m = math.floor(0.5 * BINGHAM_TAPER * 2 * n)
        w = [1.0] * n
        width = (2 * n + 1) * BINGHAM_TAPER
        for i in range(m):
            w[n - m + i] = 0.5 * (1 - math.cos(2 * math.pi * (m - i) / width))
    else:
        return list(x)
    return [v * g for v, g in zip(x, w)]


def response(record: Record, frequencies: list[float] | None = None, calibration: bool = True,
             spl: bool = False) -> tuple[list[float], list[float], list[float]]:
    """(frequencies, dB, group delay s) of the analysis window.

    The grid is ``impulse.log_grid`` within the record's frequency range.
    ``calibration`` subtracts the stored microphone calibration where the
    record applies it; ``spl`` adds FuzzMeasure's SPL offset.
    """
    if frequencies is None:
        frequencies = impulse.log_grid(record.sample_rate)
        if record.end_hz > record.start_hz:
            frequencies = [f for f in frequencies if record.start_hz <= f <= record.end_hz]
        if not frequencies:
            raise ValueError(f"{record.title!r}: no frequency in "
                             f"{record.start_hz:g}-{record.end_hz:g} Hz")
    x = windowed(record)
    x += [0.0] * (record.fft_length - len(x))
    bands = impulse.point_bands(x, record.sample_rate, frequencies)
    db = [impulse.power_db(p) for p, _ in bands]
    if calibration and record.use_calibration and record.calibration is not None:
        db = [v - (c or 0.0) for v, c in zip(db, record.calibration.db(frequencies))]
    if spl:
        offset = CALIBRATOR_DB - (record.spl_reference if record.use_spl else 0.0)
        db = [v + offset for v in db]
    return frequencies, db, [gd for _, gd in bands]


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def record_uuid(name: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, name)).upper()


def write(records: list[Record]) -> dict[str, bytes]:
    """A FuzzMeasure 4 package: file name -> contents.

    Each record's impulse response is written as ``<uuid>.caf``.  The
    document has one frequency response graph.
    """
    if not records:
        raise ValueError("a document needs a measurement")
    uuids = [r.uuid for r in records]
    if not all(uuids) or len(set(uuids)) != len(uuids):
        raise ValueError("every measurement needs its own UUID")
    files = {}
    for r in records:
        if r.sample_rate <= 0 or not r.ir:
            raise ValueError(f"{r.title!r}: no samples or no sample rate")
        files[f"{r.uuid}.caf"] = caf.write(r.sample_rate, r.ir)
    top = {
        "FUMEVersion": FUME_VERSION,
        "MeasurementRecords": [_record_instance(r) for r in records],
        "Graphs": [Instance(GRAPH_CLASSES[0], {}, GRAPH_CLASSES)],
        "ColorIndex": Ref(len(records)),
    }
    return {TOP_LEVEL: keyedarchive.archive(top), **files}


def _record_instance(r: Record) -> Instance:
    begin, end, shape = r.window
    fields = {
        "version": Ref(1),
        "impulseResponseWindow": Instance("SMUGWindow", {
            "Version": "2.0", "Begin": begin, "End": end, "Type": shape}),
        "impulseUUID": r.uuid,
        "date": r.date or EPOCH,
        "comment": r.title,
        "notes": r.notes,
        "sampleRate": Ref(int(r.sample_rate)),
        "plotColor": _color_instance(r.color or COLORS[0]),
        "logSweepSettings": Instance("SMUGLogSweepSettings", dict(r.sweep or SWEEP)),
        "synchronousAverages": Ref(int(r.averages)),
        "compatibilityMode": Ref(bool(r.compatibility)),
        "normalized": Ref(bool(r.normalized)),
        "correctionEnabled": Ref(bool(r.use_calibration)),
        "correctionRecord": (_calibration_instance(r.calibration) if r.calibration is not None
                             else None),
        "useSPLReferenceLevel": Ref(bool(r.use_spl)),
        "SPLReferenceLevel": Ref(float(r.spl_reference)),
        "startFrequency": Ref(float(r.start_hz)),
        "endFrequency": Ref(float(r.end_hz)),
        "recordChannelEntry": None,
        "audioDeviceName": r.device,
        "audioDeviceUID": None,
        "speedOfSound": Ref(float(r.speed_of_sound)),
    }
    return Instance("SMUGMeasurementRecord", fields)


def _color_instance(rgb: tuple[float, float, float]) -> Instance:
    return Instance("NSColor", {"NSColorSpace": 1,
                                "NSRGB": " ".join(f"{v:g}" for v in rgb).encode() + b"\0"})


def _vector_instance(values: list[float]) -> Instance:
    return Instance("SMUGRealVector", {
        "version": Ref(2), "CFByteOrder": Ref(1),
        "VectorData": Ref(struct.pack(f"<{len(values)}f", *values))})


def _spline_instance(points: list[tuple[float, float]]) -> Instance:
    return Instance("SMUGSpline", {"SMUGSplineVersion": "1.0",
                                   "X": _vector_instance([f for f, _ in points]),
                                   "Y": _vector_instance([v for _, v in points])})


def _calibration_instance(c: Calibration) -> Instance:
    fields = {"Name": c.name}
    if c.serial is not None:
        fields["SerialNumber"] = c.serial
    fields.update({
        "Sensitivity": Ref(float(c.sensitivity)), "SensitivityUnits": Ref(0),
        "MagnitudeSpline": _spline_instance(c.points), "PhaseSpline": _spline_instance(c.phase),
        "UUID": c.uuid or record_uuid(f"calibration/{c.name}"),
    })
    return Instance("MicrophoneCalibrationRecord", fields)


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
def _elided(value: Any) -> Any:
    """The archive values without the sample data of impulse responses."""
    if isinstance(value, Instance):
        return Instance(value.classname, {k: _elided(v) for k, v in value.fields.items()})
    if isinstance(value, list):
        return [_elided(v) for v in value]
    if isinstance(value, dict):
        return {k: _elided(v) for k, v in value.items()}
    return value


def _window_text(r: Record) -> str:
    begin, end, shape = r.window
    ms = lambda s: s / r.sample_rate * 1000
    return (f"{ms(begin):.3f} to {ms(end):.3f} ms ({begin} to {end} samples), "
            f"{WINDOW_TYPES.get(shape, f'type {shape}')}")


class FuzzmeasureInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        d = load(path)
        fields = [("name", path.name),
                  ("kind", f"{KINDS.get(d.kind, 'FuzzMeasure')} document"),
                  ("FUMEVersion", d.version), ("measurements", len(d.records))]
        if d.entries:
            fields.append(("size", f"{sum(d.entries.values()):,} bytes in {len(d.entries)} files"))
            table = Table(["file", "size"], list(d.entries.items()))
        else:
            fields.append(("size", f"{path.stat().st_size:,} bytes"))
            table = None
        others = {k: v for k, v in d.top.items() if k not in ("FUMEVersion", "MeasurementRecords")}
        fields += [(k, keyedarchive.describe(v)) for k, v in others.items()
                   if isinstance(v, (str, int, float))]
        sections = [Section("file", fields, table,
                            raw=keyedarchive.describe(_elided(d.top)))]
        for i, r in enumerate(d.records):
            c = r.calibration
            fields = [
                ("notes", r.notes), ("date", r.date.isoformat() if r.date else None),
                ("sample rate", r.sample_rate), ("samples", len(r.ir)), ("uuid", r.uuid),
                ("record version", r.version),
                ("window", _window_text(r)), ("FFT length", r.fft_length),
                ("normalized", r.normalized), ("compatibility mode", r.compatibility),
                ("frequency range", f"{r.start_hz:g}-{r.end_hz:g} Hz"),
                ("peak delay ms", round(r.peak_ms(), 3)),
                ("distance to peak m", round(r.peak_ms() / 1000 * r.speed_of_sound, 3)),
                ("speed of sound m/s", r.speed_of_sound),
                ("microphone calibration", (f"{c.name} ({len(c.points)} points, "
                                            f"{'applied' if r.use_calibration else 'off'})"
                                            if c else None)),
                ("SPL reference dB FS", (f"{r.spl_reference:g} "
                                         f"({'applied' if r.use_spl else 'off'})"
                                         if r.use_spl or r.spl_reference else None)),
                ("synchronous averages", r.averages),
                ("plot color", " ".join(f"{v:g}" for v in r.color) if r.color else None),
                ("audio device", r.device),
                *((f"sweep {k}", v) for k, v in r.sweep.items() if not isinstance(v, Instance)),
            ]
            sections.append(response_section(f"measurement {i} {r.title}", fields,
                                             lambda: response(r)))
        for i, r in enumerate(d.records):
            c = r.calibration
            if c is not None:
                sections.append(Section(f"calibration of measurement {i} {c.name}", [
                    ("serial number", c.serial), ("sensitivity", c.sensitivity or None),
                    ("uuid", c.uuid), ("range", frequency_range(c.points))],
                    Table(["frequency Hz", "dB"], c.points)))
        return sections


FORMAT = Format("fuzzmeasure", tuple(KINDS), "FuzzMeasure 4 / 3 / 2 document",
                FuzzmeasureInspector)
