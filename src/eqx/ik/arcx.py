"""Reader and writer for IK Multimedia ARC X sessions (``*.arcXs``) and analyses (``*.arcXa``).

Both are ``IKMPAK`` containers (``eqx.ik.pak``)::

    session.xml               the session (.arcXs only)
    info.xml                  analysis settings, root SerializedMeasure
    ch<c>/ch<c>p<p>_ir.wav    impulse response of channel c at point p
    ch<c>/ch<c>p<p>_cc.wav    cross correlation of channel c at point p

The XML files are JUCE ValueTree XML.  The WAVs are mono 32-bit float.
IK publishes no specification; the layout is taken from ARC X 2.0.2.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .. import impulse
from ..fileformat import Format, Inspector, response_section
from ..model import Measurement
from ..options import Option
from ..report import Section
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
SAMPLE_RATES = (44100.0, 48000.0)
# ARC X writes and processes 32768 samples.
IR_LENGTH = 32768
INFO_VERSION = "5.0.0"
APP_VERSION = "2.0.2 (26D30)"
NULL_GUID = "00000000-0000-0000-0000-000000000000"
# Speaker device: IK product | serial | two a.b.c versions | 0 or 1.  ARC X
# loads no session whose speakers lack a product and a serial.  183: ARC Studio.
DEVICE = "183|000000000|1.0.0|1.0.0|0"
# The device state of a speaker: flat, no tuning filter.
SETTINGS = (
    [("HpfFrequency", "20.0"), ("DelayMs", "0.0"), ("GainDb", "0.0"), ("DelayEnable", "1"),
     ("GainEnable", "1"), ("CalEnable", "1"), ("PhaseInvert", "0"), ("PhaseInvertEnable", "1"),
     ("FilterLowType", "0"), ("FilterHighType", "0"), ("VoiceIndex", "0"), ("FilterEnable", "0"),
     ("VoiceEnable", "0"), ("DimAttenuationDb", "0.0"), ("ActivePreset", "0"),
     ("FilterLowFrequency", "100.0"), ("FilterLowGainDb", "0.0"), ("FilterLowQ", "0.7")]
    + [(f"PeakFilters{n}{k}", v) for n in range(4)
       for k, v in (("Frequency", "1000.0"), ("GainDb", "0.0"), ("Q", "1.0"))]
    + [("FilterHighFrequency", "10000.0"), ("FilterHighGainDb", "0.0"), ("FilterHighQ", "0.7")]
)

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
    info_text = xml_text(entries[INFO], INFO)
    info = xml_root(info_text, INFO, "SerializedMeasure")
    major = info.get("Version", "").split(".")[0]
    if major != str(INFO_VERSION_MAJOR):
        raise ValueError(f"unsupported ARC X analysis version {info.get('Version')!r}")
    sample_rate = number_attribute(info, "SampleRate", INFO)
    points = int(number_attribute(info, "NumMeasurementPoints", INFO))
    if points <= 0:
        raise ValueError("ARC X analysis has no measurement points")

    session, session_text = None, ""
    if SESSION in entries:
        session_text = xml_text(entries[SESSION], SESSION)
        session = xml_root(session_text, SESSION, "Session")

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


def xml_text(data: bytes, name: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name} is not UTF-8: {exc}") from None


def xml_root(text: str, name: str, tag: str) -> ET.Element:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"cannot parse {name}: {exc}") from None
    if root.tag != tag:
        raise ValueError(f"{name}: root is {root.tag!r}, not {tag!r}")
    return root


def number_attribute(element: ET.Element, key: str, where: str) -> float:
    try:
        return float(element.get(key, ""))
    except ValueError:
        raise ValueError(f"{where}: {key} {element.get(key)!r} is not a number") from None


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def value_tree(tag: str, attributes, children=(), indent: str = "") -> list[str]:
    attrs = "".join(f' {k}="{v}"' for k, v in attributes)
    if not children:
        return [f"{indent}<{tag}{attrs}/>"]
    lines = [f"{indent}<{tag}{attrs}>"]
    for child in children:
        lines += value_tree(*child, indent=indent + "  ")
    return lines + [f"{indent}</{tag}>"]


def juce_xml(lines: list[str]) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n\n' + "\n".join(lines) + "\n").encode()


def write(sample_rate: float, channels: list[list[Point]], layout: int,
          session: bool = True, mic_type: str = "MEMS") -> bytes:
    """A session (``session``) or an analysis; ``channels`` in the layout's speaker order."""
    if layout not in LAYOUTS:
        raise ValueError(f"unknown ARC X layout {layout}")
    name, positions = LAYOUTS[layout]
    if len(channels) != len(positions):
        raise ValueError(f"layout {name} has {len(positions)} speakers, got {len(channels)} "
                         "channels")
    points = len(channels[0])
    if not points or any(len(c) != points for c in channels):
        raise ValueError("every channel needs the same number of points, at least one")
    entries = {INFO: juce_xml(value_tree("SerializedMeasure", [
        ("Version", INFO_VERSION), ("SampleRate", f"{sample_rate:.1f}"),
        ("SelectedMicType", mic_type), ("CorrectionSpeaker", NULL_GUID),
        ("NumMeasurementPoints", points), ("Layout", name), ("ListeningArea", "Project Studio"),
        ("FastMode", "0"), ("Countdown", "5")]))}
    for c, channel in enumerate(channels):
        for p, point in enumerate(channel):
            entries[_wav_name(c, p, "ir")] = fir.write(fir.Fir(sample_rate, [point.ir]))
            entries[_wav_name(c, p, "cc")] = fir.write(fir.Fir(sample_rate, [point.cc]))
    if session:
        entries[SESSION] = juce_xml(value_tree("Session", [
            ("Version", 1), ("AppVersion", APP_VERSION), ("GUID", NULL_GUID),
            ("Layout", layout), ("CorrectionType", 1), ("CorrectionPhase", 1),
            ("MasterRemoteSpeakerIndex", -1), ("BassManaged", 0), ("TargetRequest", 0),
            ("Notes", ""), ("AudioDeviceName", ""), ("AudioDeviceSampleRate", round(sample_rate)),
            ("AudioDeviceBufferSize", 512)], [
            (f"Speaker_{position}", [
                ("Device", DEVICE), ("CalRangeLow", 20), ("CalRangeHigh", 20000),
                ("CorrectionAssetGUID", NULL_GUID), ("OutputChannelIndex", c)],
             [("Settings", SETTINGS)])
            for c, position in enumerate(positions)]))
    return pak.write(entries)


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
def response(arcx: ArcX, channel: int, point: int | None = None,
             frequencies: list[float] | None = None) -> tuple[list[float], list[float], list[float]]:
    """(frequencies, dB, group delay s) of one channel: power average of the points."""
    points = arcx.channels[channel]
    if point is not None:
        if not 0 <= point < len(points):
            raise ValueError(f"point {point} does not exist; points: 0-{len(points) - 1}")
        points = [points[point]]
    if frequencies is None:
        frequencies = impulse.log_grid(arcx.sample_rate)
    return (frequencies, *impulse.average([p.ir for p in points], arcx.sample_rate, frequencies))


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
        sections = [pak.file_section(path, data, a.pak_version, a.sizes, ("content", kind))]
        if a.session is not None:
            sections += _xml_sections(a.session, "Session", a.session_text)
        sections += _xml_sections(a.info, "SerializedMeasure", a.info_text)
        for c, (speaker, points) in enumerate(zip(a.speakers, a.channels)):
            peaks = ", ".join(f"{impulse.peak_index(p.ir) / a.sample_rate * 1000:.2f}"
                              for p in points)
            sections.append(response_section(f"channel {c} {speaker}", [
                ("points", len(points)),
                ("IR samples", len(points[0].ir)),
                ("peak delay ms", peaks),
            ], lambda: response(a, c)))
        return sections


FORMAT = Format("arcx", (".arcxs", ".arcxa"), "IK Multimedia ARC X session / analysis",
                ArcxInspector)
