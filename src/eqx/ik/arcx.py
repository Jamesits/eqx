"""Reader for IK Multimedia ARC X sessions (``*.arcXs``) and analyses (``*.arcXa``).

Both are ``IKMPAK`` containers (``eqx.ik.pak``)::

    session.xml               the session (.arcXs only)
    info.xml                  analysis settings, root SerializedMeasure
    ch<c>/ch<c>p<p>_ir.wav    impulse response of channel c at point p
    ch<c>/ch<c>p<p>_cc.wav    cross correlation of channel c at point p

The XML files are JUCE ValueTree XML.  The WAVs are mono 32-bit float.
IK publishes no specification; the layout is taken from ARC X 2.0.2.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .. import dsp
from ..fileformat import Format, Inspector, file_section, frequency_range
from ..model import Measurement
from ..options import Option
from ..report import Section, Table
from ..wav import fir
from . import pak

# Speaker positions: the n of ``Speaker_<n>``, named as ARC X's
# ``OutputChannel<name>Index`` settings.
POSITIONS = {
    0: "Left", 1: "Right", 2: "Subwoofer", 3: "LeftRearSurround", 4: "RightRearSurround",
    5: "Center", 6: "LeftSideSurround", 7: "RightSideSurround", 8: "LeftTopFront",
    9: "RightTopFront", 10: "LeftTopBack", 11: "RightTopBack", 12: "LeftRear", 13: "RightRear",
    14: "LeftTopMiddle", 15: "RightTopMiddle", 16: "LeftWide", 17: "RightWide",
}
# Session ``Layout`` id -> (name, speaker positions in channel order).
LAYOUTS = {
    0: ("Plugin", (0, 1)),
    1: ("Stereo", (0, 1)),
    2: ("Stereo + Sub", (0, 1, 2)),
    3: ("4.0", (0, 1, 12, 13)),
    4: ("5.1", (0, 1, 5, 2, 3, 4)),
    7: ("7.1", (0, 1, 5, 2, 6, 7, 3, 4)),
    5: ("7.1.4", (0, 1, 5, 2, 6, 7, 3, 4, 8, 9, 10, 11)),
    6: ("7.1.6", (0, 1, 5, 2, 6, 7, 3, 4, 8, 9, 14, 15, 10, 11)),
    8: ("9.1.6", (0, 1, 5, 2, 6, 7, 3, 4, 16, 17, 8, 9, 14, 15, 10, 11)),
}
# ARC X probes ch0..ch15 and stops at the first missing one.
MAX_CHANNELS = 16
INFO_VERSION_MAJOR = 5
SESSION, INFO = "session.xml", "info.xml"
# Response grid: 1/48 octave from 20 Hz; bands of +-1/96 octave.
GRID_LOW_HZ, GRID_HIGH_HZ, GRID_STEPS_PER_OCTAVE = 20.0, 20000.0, 48

POINT_OPTION = Option("--point", type=int,
                      help="measurement point, from 0 as in the file names "
                           "(default: the power average of all points)")


@dataclass
class Point:
    ir: list[float]                         # impulse response
    cc: list[float]                         # cross correlation


@dataclass
class ArcX:
    pak_version: int
    sizes: dict[str, int]                   # pak entry name -> size
    info: ET.Element                        # SerializedMeasure
    session: ET.Element | None              # Session; None in an analysis file
    sample_rate: float
    channels: list[list[Point]]             # [channel][point]
    speakers: list[str]                     # name of each channel
    layout: int | None                      # layout id; None if not known
    info_text: str
    session_text: str = ""

    def channel(self, speaker: str) -> int:
        """Index of the channel named ``speaker`` (case-insensitive)."""
        names = [s.lower() for s in self.speakers]
        if speaker.lower() not in names:
            raise ValueError(f"no {speaker} speaker; available: {', '.join(self.speakers)}")
        return names.index(speaker.lower())


def read(data: bytes) -> ArcX:
    version, entries = pak.read(data)
    if INFO not in entries:
        raise ValueError("not an ARC X session or analysis: no info.xml")
    info_text = _text(entries[INFO], INFO)
    info = _root(info_text, INFO, "SerializedMeasure")
    major = info.get("Version", "").split(".")[0]
    if major != str(INFO_VERSION_MAJOR):
        raise ValueError(f"unsupported ARC X analysis version {info.get('Version')!r}")
    sample_rate = _number(info, "SampleRate", INFO)
    points = int(_number(info, "NumMeasurementPoints", INFO))
    if points <= 0:
        raise ValueError("ARC X analysis has no measurement points")

    session, session_text = None, ""
    if SESSION in entries:
        session_text = _text(entries[SESSION], SESSION)
        session = _root(session_text, SESSION, "Session")

    channels = []
    for c in range(MAX_CHANNELS):
        if not entries.get(_wav_name(c, 0, "ir")):
            break
        channels.append([Point(_samples(entries, c, p, "ir", sample_rate),
                               _samples(entries, c, p, "cc", sample_rate))
                         for p in range(points)])
    if not channels:
        raise ValueError("ARC X analysis has no channels (no ch0/ch0p0_ir.wav)")
    layout = layout_id(session, info, len(channels))
    return ArcX(version, {k: len(v) for k, v in entries.items()}, info, session, sample_rate,
                channels, speaker_names(session, info, len(channels)), layout, info_text,
                session_text)


def load(path) -> ArcX:
    return read(Path(path).read_bytes())


def layout_id(session: ET.Element | None, info: ET.Element, count: int) -> int | None:
    """The session layout, else the analysis layout name, else Stereo for 2 channels.

    None if not known or its speaker count is not ``count``.
    """
    layout = None
    if session is not None and session.get("Layout", "").strip().lstrip("-").isdigit():
        layout = int(session.get("Layout"))
        layout = layout if layout in LAYOUTS else None
    if layout is None:
        layout = next((i for i, (name, _) in LAYOUTS.items() if name == info.get("Layout")),
                      None)
    if layout is None and count == 2:
        layout = 1
    if layout is None or len(LAYOUTS[layout][1]) != count:
        return None
    return layout


def speaker_names(session: ET.Element | None, info: ET.Element, count: int) -> list[str]:
    """Channel names by ``layout_id``; ``Channel <c>`` if the layout is not known."""
    layout = layout_id(session, info, count)
    if layout is None:
        return [f"Channel {c}" for c in range(count)]
    return [POSITIONS[p] for p in LAYOUTS[layout][1]]


def _wav_name(channel: int, point: int, kind: str) -> str:
    return f"ch{channel}/ch{channel}p{point}_{kind}.wav"


def _samples(entries: dict[str, bytes], channel: int, point: int, kind: str,
             sample_rate: float) -> list[float]:
    name = _wav_name(channel, point, kind)
    if name not in entries:
        raise ValueError(f"ARC X analysis has no {name}")
    rate, samples = read_wav(entries[name], name)
    if rate != sample_rate:
        raise ValueError(f"{name}: {rate:g} Hz, the analysis is {sample_rate:g} Hz")
    return samples


def _text(data: bytes, name: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name} is not UTF-8: {exc}") from None


def _root(text: str, name: str, tag: str) -> ET.Element:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"cannot parse {name}: {exc}") from None
    if root.tag != tag:
        raise ValueError(f"{name}: root is {root.tag!r}, not {tag!r}")
    return root


def _number(element: ET.Element, key: str, where: str) -> float:
    try:
        return float(element.get(key, ""))
    except ValueError:
        raise ValueError(f"{where}: {key} {element.get(key)!r} is not a number") from None


# --------------------------------------------------------------------------
# WAV
# --------------------------------------------------------------------------
def read_wav(data: bytes, name: str = "WAV") -> tuple[float, list[float]]:
    """Sample rate and samples of a mono PCM or float WAV; PCM is scaled to +-1."""
    wav = fir.read(data, name)
    if len(wav.channels) != 1:
        raise ValueError(f"{name}: {len(wav.channels)} channels, expected mono")
    return wav.sample_rate, wav.channels[0]


# --------------------------------------------------------------------------
# response
# --------------------------------------------------------------------------
def log_grid(sample_rate: float) -> list[float]:
    """1/48 octave from 20 Hz up to 20 kHz, below Nyquist."""
    top = min(GRID_HIGH_HZ, sample_rate / 2 * 2 ** (-1 / (2 * GRID_STEPS_PER_OCTAVE)))
    steps = math.floor(GRID_STEPS_PER_OCTAVE * math.log2(top / GRID_LOW_HZ) + 1e-9)
    return [GRID_LOW_HZ * 2 ** (k / GRID_STEPS_PER_OCTAVE) for k in range(steps + 1)]


def peak_index(ir: list[float]) -> int:
    return max(range(len(ir)), key=lambda i: abs(ir[i]))


def point_bands(ir: list[float], sample_rate: float,
                frequencies: list[float]) -> list[tuple[float, float]]:
    """(power, group delay s) of one impulse response in the band around each frequency.

    The peak is moved to sample 0 first, so the delay before it (time of
    flight, latency) is not part of the group delay.
    """
    n = 1 << max(1, (len(ir) - 1).bit_length())
    padded = list(ir) + [0.0] * (n - len(ir))
    peak = peak_index(padded)
    spectrum = dsp.fft(padded[peak:] + padded[:peak])[:n // 2 + 1]
    step = sample_rate / n
    power = [abs(x) ** 2 for x in spectrum]
    # Central phase difference; its angle stays in (-pi, pi] without unwrapping.
    delay = [0.0] * len(spectrum)
    for k in range(1, len(spectrum) - 1):
        d = spectrum[k + 1] * spectrum[k - 1].conjugate()
        delay[k] = -math.atan2(d.imag, d.real) / (2 * math.pi * 2 * step) if d else 0.0
    delay[0], delay[-1] = delay[1], delay[-2]
    return spectrum_bands(power, delay, step, frequencies)


def spectrum_bands(power: list[float], delay: list[float], step: float,
                   frequencies: list[float]) -> list[tuple[float, float]]:
    """(mean power, power-weighted delay) of the FFT bins within +-1/96 octave.

    Bin k is at k * ``step`` Hz.  A band with no bin interpolates the two
    nearest bins.
    """
    half = 2 ** (1 / (2 * GRID_STEPS_PER_OCTAVE))
    out = []
    for f in frequencies:
        first = math.ceil(f / half / step)
        last = min(math.ceil(f * half / step) - 1, len(power) - 1)
        if last >= first:
            p = sum(power[first:last + 1])
            gd = (sum(power[k] * delay[k] for k in range(first, last + 1)) / p if p
                  else sum(delay[first:last + 1]) / (last - first + 1))
            out.append((p / (last - first + 1), gd))
        else:
            k = min(int(f / step), len(power) - 2)
            t = f / step - k
            out.append((power[k] + t * (power[k + 1] - power[k]),
                        delay[k] + t * (delay[k + 1] - delay[k])))
    return out


def response(arcx: ArcX, channel: int, point: int | None = None,
             frequencies: list[float] | None = None) -> tuple[list[float], list[float], list[float]]:
    """(frequencies, dB, group delay s) of one channel: power average of the points."""
    points = arcx.channels[channel]
    if point is not None:
        if not 0 <= point < len(points):
            raise ValueError(f"point {point} does not exist; points: 0-{len(points) - 1}")
        points = [points[point]]
    if frequencies is None:
        frequencies = log_grid(arcx.sample_rate)
    bands = [point_bands(p.ir, arcx.sample_rate, frequencies) for p in points]
    db, gd = [], []
    for i in range(len(frequencies)):
        powers = [b[i][0] for b in bands]
        total = sum(powers)
        db.append(10 * math.log10(max(total / len(bands), 1e-30)))
        gd.append(sum(p * b[i][1] for p, b in zip(powers, bands)) / total if total
                  else sum(b[i][1] for b in bands) / len(bands))
    return frequencies, db, gd


def measurement(arcx: ArcX, speaker: str, point: int | None = None,
                name: str = "") -> Measurement:
    c = arcx.channel(speaker)
    frequencies, db, gd = response(arcx, c, point)
    speaker = arcx.speakers[c]
    return Measurement(speaker, c, frequencies, db, gd, sample_rate=int(arcx.sample_rate),
                       name=f"{speaker} {name}".strip())


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
def _xml_sections(element: ET.Element, path: str, raw: str | None = None) -> list[Section]:
    title = path
    if element.tag.startswith("Speaker_") and element.tag[8:].isdigit():
        title += f" ({POSITIONS.get(int(element.tag[8:]), 'unknown position')})"
    sections = [Section(title, list(element.attrib.items()), raw=raw)]
    for child in element:
        sections += _xml_sections(child, f"{path}/{child.tag}")
    return sections


class ArcxInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        a = read(data)
        kind = "session" if a.session is not None else "analysis"
        sections = [file_section(path, data, ("content", kind), ("pak version", a.pak_version),
                                 ("entries", len(a.sizes)))]
        sections[0].table = Table(["entry", "size"], sorted(a.sizes.items()))
        if a.session is not None:
            sections += _xml_sections(a.session, "Session", a.session_text)
        sections += _xml_sections(a.info, "SerializedMeasure", a.info_text)
        for c, (speaker, points) in enumerate(zip(a.speakers, a.channels)):
            frequencies, db, gd = response(a, c)
            peaks = ", ".join(f"{peak_index(p.ir) / a.sample_rate * 1000:.2f}" for p in points)
            sections.append(Section(f"channel {c} {speaker}", [
                ("points", len(points)),
                ("IR samples", len(points[0].ir)),
                ("peak delay ms", peaks),
                ("range", frequency_range(list(zip(frequencies, db)))),
            ], Table(["frequency Hz", "dB", "group delay s"], list(zip(frequencies, db, gd)))))
        return sections


FORMAT = Format("arcx", (".arcxs", ".arcxa"), "IK Multimedia ARC X session / analysis",
                ArcxInspector)
