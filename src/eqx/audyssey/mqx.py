"""Reader and writer for Audyssey MultEQ-X projects (``*.mqx``).

A project is Newtonsoft JSON::

    _measurements        one impulse response per channel and position
    _channelDataMap      channel GUID -> designation, calibration, EQ limits
    OrderedChannelGuids  channel order
    CalibrationSettings  automatic trims, distances, bass management
    TargetCurveSet       target curve components

A measurement's ``Data`` is base64 of little-endian float32 samples at
48 kHz, not compensated for the microphone.  Audyssey publishes no
specification; the layout is taken from MultEQ-X 1.8.873.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import impulse
from ..fileformat import Format, Inspector, file_section, response_section
from ..model import Measurement
from ..options import Option
from ..report import Section, Table

SAMPLE_RATE = 48000.0
IR_LENGTH = 16384
# Samples from the start of a measurement to the sound leaving the speaker:
# the peak is at this delay plus the time of flight.  The Sample AVR's value.
SYSTEM_DELAY = 124
# Mic input settings of the Sample AVR.
ADC_LINEUP, PREAMP_GAIN = 2.115, 35.5
SPEED_OF_SOUND = 343.0                      # m/s
MIC = "ACM1H"
APP_VERSION = "1.8.873.0"
NULL_GUID = "00000000-0000-0000-0000-000000000000"
SUBWOOFER = "SW1"

# Target curve component values, as MultEQ-X names them.
BASE_CURVES = {0: "Theater High Frequency Rolloff 1", 1: "Theater High Frequency Rolloff 2",
               2: "SMPTE 202M"}
MODIFIERS = {0: "Midrange Compensation", 1: "Dolby Elevation"}
BIQUAD_TYPES = {
    1: "2nd order Low Shelf", 2: "2nd order High Shelf",
    3: "2nd order Low Shelf w/ Q", 4: "2nd order High Shelf w/ Q",
    6: "Butterworth High Pass", 7: "Butterworth Low Pass",
    8: "Linkwitz-Riley High Pass 2", 9: "Linkwitz-Riley Low Pass 2",
    12: "2nd order High Pass w/ Q", 13: "2nd order Low Pass w/ Q",
    18: "Peaking Filter (Audio)", 19: "Parametric Peaking Filter",
    22: "1st order High Pass", 23: "1st order Low Pass",
    24: "1st order Low Shelf", 25: "1st order High Shelf",
}
# _itemType class -> (kind, assembly)
ITEM_TYPES = {
    "MultEQData.BaseTargetCurveType": ("base", "MultEQData"),
    "MultEQData.TargetCurveModifierType": ("modifier", "MultEQData"),
    "Audyssey.CoreData.BiquadData": ("biquad", "Audyssey.CoreData"),
    "Audyssey.CoreData.TiltData": ("tilt", "Audyssey.CoreData"),
    "Audyssey.CoreData.CustomCurveData": ("custom", "Audyssey.CoreData"),
}
_ITEM_CLASS = {kind: (cls, assembly) for cls, (kind, assembly) in ITEM_TYPES.items()}

# Channels the writer knows: designation -> (display name, pair designation,
# pair display name, location), as the Sample AVR names them.
WRITABLE = {
    "FL": ("Front Left", "F_", "Front", "FL"),
    "FR": ("Front Right", "F_", "Front", "FR"),
    "C": ("Center", "C", "Center", "Center, Front"),
    "SLA": ("Surround Left A", "S_A", "Surround A", "Left, Surround, Position1"),
    "SRA": ("Surround Right A", "S_A", "Surround A", "Right, Surround, Position1"),
    SUBWOOFER: ("Subwoofer 1", "SW1", "Subwoofer 1", "Subwoofer, Position1"),
}
# --speaker names of the front pair.
ALIASES = {"left": "FL", "right": "FR"}
# Written GUIDs are UUID 5 of a name: reproducible.
GUID_NAMESPACE = uuid.UUID("6f0f3b1e-6d8c-5b8e-9a39-2f3c6b1d7a10")

MIC_RESPONSE_OPTION = Option(
    "--mic-response", type=Path,
    help="REW calibration file of the measuring microphone (MultEQ-X compensates the "
         "measurements for its ACM1H; default: none)")
POSITION_OPTION = Option("--position", type=int,
                         help="measurement position, from 0 as listed by inspect "
                              "(default: the power average of the enabled measurements)")


@dataclass
class Channel:
    guid: str
    designation: str                        # AvrOriginatingDesignation, e.g. FL
    name: str                               # DisplayName, e.g. Front Left
    data: dict                              # the _channelDataMap entry


@dataclass
class Recording:
    """One entry of ``_measurements``."""

    channel: str                            # channel GUID
    position: str                           # position GUID
    enabled: bool                           # false: excluded from the aggregate
    ir: list[float]
    data: dict                              # the entry without Data


@dataclass
class TargetItem:
    """One target curve component (a tab of the Design Target Curve page)."""

    kind: str                               # base, modifier, biquad, tilt, custom
    name: str
    value: Any                              # int (base, modifier), else a dict
    reference: bool = True                  # applies to the Reference preset
    flat: bool = True                       # applies to the Flat preset
    all: bool = True                        # applies to every channel ...
    channels: list[str] = field(default_factory=list)   # ... else to these GUIDs
    excluded: list[str] = field(default_factory=list)   # GUIDs

    def describe(self) -> str:
        v = self.value
        if self.kind == "base":
            return BASE_CURVES.get(v, f"base curve {v}")
        if self.kind == "modifier":
            return MODIFIERS.get(v, f"modifier {v}")
        if self.kind == "biquad":
            kind = BIQUAD_TYPES.get(v.get("Type"), f"type {v.get('Type')}")
            return f"{kind} {v.get('Frequency', 0):g} Hz, {v.get('Gain', 0):g} dB, Q {v.get('Q', 0):g}"
        if self.kind == "tilt":
            return (f"tilt {v.get('DecibelsPerOctave', 0):g} dB/octave "
                    f"at {v.get('PivotFrequency', 0):g} Hz")
        if self.kind == "custom":
            return f"custom curve, {len(v.get('Points') or [])} points"
        return self.kind

    def points(self) -> list[tuple[float, float]]:
        """(Hz, dB) of a custom curve; its Y values are linear gains."""
        return [(p["X"], 20 * math.log10(p["Y"]) if p["Y"] > 0 else -math.inf)
                for p in self.value.get("Points") or []]


@dataclass
class Mqx:
    data: dict                              # the JSON, Data blobs included
    channels: list[Channel]                 # OrderedChannelGuids order, then the others
    recordings: list[Recording]
    positions: list[str]                    # GUIDs, in order of first measurement
    targets: list[TargetItem]
    trailing: int = 0                       # characters after the JSON

    def channel(self, speaker: str) -> int:
        """Index of the channel with this designation or display name (case-insensitive).

        Left and Right are FL and FR.
        """
        key = ALIASES.get(speaker.lower(), speaker).lower()
        for i, c in enumerate(self.channels):
            if key in (c.designation.lower(), c.name.lower()):
                return i
        raise ValueError(f"no {speaker} speaker; available: "
                         f"{', '.join(c.designation for c in self.channels) or 'none'}")

    def designation(self, guid: str) -> str:
        return next((c.designation for c in self.channels if c.guid == guid), guid)


def read(data: bytes) -> Mqx:
    try:
        text = data.decode("utf-8-sig").lstrip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"not a MultEQ-X project: not UTF-8: {exc}") from None
    # MultEQ-X saves over the old file without truncating it: a shorter
    # project is followed by the tail of the previous one.
    try:
        obj, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not a MultEQ-X project: {exc}") from None
    if (not isinstance(obj, dict) or not isinstance(obj.get("_measurements"), list)
            or not isinstance(obj.get("_channelDataMap"), dict)):
        raise ValueError("not a MultEQ-X project: no _measurements or _channelDataMap")

    entries = obj["_channelDataMap"]
    order = [g for g in obj.get("OrderedChannelGuids") or [] if g in entries]
    order += [g for g in entries if g not in order]
    channels = []
    for guid in order:
        metadata = entries[guid].get("Metadata") or {}
        designation = metadata.get("AvrOriginatingDesignation") or guid
        channels.append(Channel(guid, designation, metadata.get("DisplayName") or designation,
                                entries[guid]))

    recordings, positions = [], []
    for i, m in enumerate(obj["_measurements"]):
        if not isinstance(m, dict):
            raise ValueError(f"measurement {i} is not an object")
        recordings.append(Recording(str(m.get("ChannelGuid")), str(m.get("PositionGuid")),
                                    m.get("Enabled", True) is not False, _samples(m, i),
                                    {k: v for k, v in m.items() if k != "Data"}))
        if recordings[-1].position not in positions:
            positions.append(recordings[-1].position)
    targets = [_target(item, i) for i, item in enumerate(obj.get("TargetCurveSet") or [])]
    return Mqx(obj, channels, recordings, positions, targets, len(text[end:].strip()))


def load(path) -> Mqx:
    return read(Path(path).read_bytes())


def _samples(m: dict, index: int) -> list[float]:
    try:
        raw = base64.b64decode(m.get("Data") or "", validate=True)
    except (binascii.Error, TypeError):
        raise ValueError(f"measurement {index}: Data is not base64") from None
    if not raw or len(raw) % 4:
        raise ValueError(f"measurement {index}: {len(raw)} bytes of Data, not float32 samples")
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def _target(item: dict, index: int) -> TargetItem:
    cls = str(item.get("_itemType", "")).split(",")[0].strip()
    kind = ITEM_TYPES.get(cls, (cls or "unknown", ""))[0]
    raw = item.get("_itemString")
    try:
        value = int(raw) if kind in ("base", "modifier") else json.loads(raw or "{}")
    except ValueError:
        raise ValueError(f"target curve item {index}: cannot parse _itemString {raw!r}") from None
    return TargetItem(kind, item.get("Name") or "", value, item.get("ApplyToReference", True),
                      item.get("ApplyToFlat", True), item.get("All", False),
                      list(item.get("Channels") or []), list(item.get("ExcludedChannels") or []))


# --------------------------------------------------------------------------
# response
# --------------------------------------------------------------------------
def channel_recordings(mqx: Mqx, channel: int, position: int | None = None) -> list[Recording]:
    """The enabled measurements of a channel, or its measurement at ``position``."""
    c = mqx.channels[channel]
    recordings = [r for r in mqx.recordings if r.channel == c.guid]
    if position is not None:
        if not 0 <= position < len(mqx.positions):
            raise ValueError(f"position {position} does not exist; positions: "
                             f"0-{len(mqx.positions) - 1}")
        recordings = [r for r in recordings if r.position == mqx.positions[position]]
        if not recordings:
            raise ValueError(f"{c.designation} has no measurement at position {position}")
        return recordings[:1]
    recordings = [r for r in recordings if r.enabled]
    if not recordings:
        raise ValueError(f"{c.designation} has no enabled measurement")
    return recordings


def response(mqx: Mqx, channel: int, position: int | None = None,
             frequencies: list[float] | None = None) -> tuple[list[float], list[float], list[float]]:
    """(frequencies, dB, group delay s) of one channel: power average over positions."""
    if frequencies is None:
        frequencies = impulse.log_grid(SAMPLE_RATE)
    irs = [r.ir for r in channel_recordings(mqx, channel, position)]
    return (frequencies, *impulse.average(irs, SAMPLE_RATE, frequencies))


def measurement(mqx: Mqx, speaker: str, position: int | None = None,
                name: str = "") -> Measurement:
    c = mqx.channel(speaker)
    frequencies, db, gd = response(mqx, c, position)
    designation = mqx.channels[c].designation
    return Measurement(designation, c, frequencies, db, gd, sample_rate=int(SAMPLE_RATE),
                       name=f"{designation} {name}".strip())


def delay_ms(recording: Recording) -> float:
    """Peak time after the system delay, ms: the time of flight."""
    system = recording.data.get("SystemDelay", SYSTEM_DELAY)
    return (impulse.peak_index(recording.ir) - system) / SAMPLE_RATE * 1000


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def guid(kind: str, name: str) -> str:
    return str(uuid.uuid5(GUID_NAMESPACE, f"{kind}/{name}"))


def channel_guid(designation: str) -> str:
    return guid("channel", designation)


DEFAULT_TARGETS = (
    TargetItem("base", BASE_CURVES[0], 0, reference=True, flat=False),
    TargetItem("modifier", MODIFIERS[0], 0),
)


def write(channels: list[tuple[str, list[list[float]]]],
          targets: tuple[TargetItem, ...] | list[TargetItem] = DEFAULT_TARGETS) -> bytes:
    """A project of ``channels``: (designation, impulse response per position).

    Every channel has the same number of positions; an impulse response
    holds ``IR_LENGTH`` samples at 48 kHz and starts ``SYSTEM_DELAY`` samples
    before the sound leaves the speaker.  Designations: ``WRITABLE``.
    """
    names = [d for d, _ in channels]
    unknown = [d for d in names if d not in WRITABLE]
    if unknown or len(set(names)) != len(names):
        raise ValueError(f"channels must be distinct, of: {', '.join(WRITABLE)}; got "
                         f"{', '.join(names)}")
    if not {"FL", "FR"} <= set(names):
        raise ValueError("MultEQ-X needs the FL and FR channels")
    positions = len(channels[0][1])
    if not positions or any(len(irs) != positions for _, irs in channels):
        raise ValueError("every channel needs the same number of positions, at least one")
    if any(len(ir) != IR_LENGTH for _, irs in channels for ir in irs):
        raise ValueError(f"an impulse response holds {IR_LENGTH} samples")
    channels = sorted(channels, key=lambda c: list(WRITABLE).index(c[0]))
    has_sub = SUBWOOFER in names

    measurements, entries = [], {}
    for p in range(positions):
        for designation, irs in channels:
            recording = Recording(channel_guid(designation), guid("position", str(p)), True,
                                  irs[p], {"SystemDelay": SYSTEM_DELAY})
            measurements.append({
                "Enabled": True,
                "Data": base64.b64encode(struct.pack(f"<{IR_LENGTH}f", *irs[p])).decode(),
                "SystemDelay": SYSTEM_DELAY,
                "AdcLineup": ADC_LINEUP,
                "PreampGain": PREAMP_GAIN,
                "AvrSpeakerType": "Subwoofer" if designation == SUBWOOFER else "Small",
                "AvrPolarityError": False,
                "AvrDistanceMeters": round(delay_ms(recording) * SPEED_OF_SOUND / 1000, 2),
                "MicCorrectionName": MIC,
                "AvrTrim": "NaN",
                "HTData": None,
                "ChannelGuid": recording.channel,
                "PositionGuid": recording.position,
            })
    for designation, irs in channels:
        name, pair, pair_name, location = WRITABLE[designation]
        sub = designation == SUBWOOFER
        recording = Recording("", "", True, irs[0], {"SystemDelay": SYSTEM_DELAY})
        limit = {"LimitValue": 24000.0, "SmoothingUpperFactor": 0, "LimitMode": "hfLimitModeAll"}
        entries[channel_guid(designation)] = {
            "Metadata": {"AvrOriginatingDesignation": designation, "DisplayName": name,
                         "PairDesignation": pair, "PairDisplayName": pair_name,
                         "IsSubEQHT": False, "Location": location},
            "Calibration": {"IsEnabled": True, "Trim": 0.0, "FilterHeadroomTrim": 0.0,
                            "DistanceMilliseconds": round(delay_ms(recording), 6),
                            "PolarityError": False,
                            "SpeakerSize": "Subwoofer" if sub else "Small" if has_sub else "Large",
                            "CrossoverFrequency": 80.0},
            "LowFrequencyLimit": {"ManualValue": 80.0, "Mode": "Auto"},
            "LowFrequencyLimitFlat": {"ManualValue": 80.0, "Mode": "Auto"},
            "HighFrequencyLimit": 0.0,
            "HighFrequencyLimitRef": dict(limit),
            "HighFrequencyLimitFlat": dict(limit),
            "TargetCurveCutoff": {
                "CutoffData": {"Frequency": 80.0, "Gain": 1.0, "Order": 2,
                               "Type": "Butterworth_High_Pass",
                               "ItemGuid": guid("cutoff", designation)},
                "Mode": "Disabled" if sub else "Auto"},
            "DisbaleTargetCurveLevelAlign": False,
            "TrimHeadroomExtension": 0.0,
        }
    obj = {
        "_measurements": measurements,
        "_channelDataMap": entries,
        "OrderedChannelGuids": [channel_guid(d) for d, _ in channels],
        "ProjectNotes": None,
        "CalibrationSettings": {"AutoTrims": True, "AutoDistance": True, "AutoEnable": True,
                                "AutoBassManagement": True, "TrimPositionGuids": [],
                                "DistancePoisitionGuid": NULL_GUID},
        "HTCalibrationData": None,
        "UndetectedChannels": [],
        "TargetCurveSet": [_target_json(t) for t in targets],
        "CrossoverAnalyzerResults": [],
        "PositionNames": {},
        "UsedLocalMicrophones": {},
        "UsedSoundCardCalibrations": {},
        "PersistantProjectDatas": {},
    }
    return dumps(obj)


def dumps(obj: dict) -> bytes:
    """Project JSON as MultEQ-X writes it: two-space indent, CRLF."""
    return json.dumps(obj, indent=2).replace("\n", "\r\n").encode()


def _target_json(t: TargetItem) -> dict:
    cls, assembly = _ITEM_CLASS[t.kind]
    value = str(t.value) if t.kind in ("base", "modifier") else json.dumps(
        t.value, separators=(",", ":"))
    return {
        "_itemString": value,
        "_itemType": f"{cls}, {assembly}, Version={APP_VERSION}, Culture=neutral, "
                     "PublicKeyToken=null",
        "Channels": list(t.channels),
        "ExcludedChannels": list(t.excluded),
        "All": t.all,
        "Name": t.name,
        "ApplyToReference": t.reference,
        "ApplyToFlat": t.flat,
    }


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
def _elided(obj: dict) -> str:
    """The JSON with each measurement's Data replaced by its size."""
    copy = dict(obj)
    copy["_measurements"] = [
        {k: (f"<{len(v) * 3 // 4:,} bytes>" if k == "Data" and isinstance(v, str) else v)
         for k, v in m.items()} if isinstance(m, dict) else m
        for m in obj["_measurements"]]
    return json.dumps(copy, indent=2)


def _flat(prefix: str, obj) -> list[tuple[str, Any]]:
    """Leaf values of nested objects as ``a.b`` keys."""
    if isinstance(obj, dict):
        return [kv for k, v in obj.items() for kv in _flat(f"{prefix}.{k}" if prefix else k, v)]
    return [(prefix, json.dumps(obj) if isinstance(obj, list) else obj)]


def _presets(t: TargetItem) -> str:
    return "+".join(n for n, on in (("Reference", t.reference), ("Flat", t.flat)) if on) or "none"


class MqxInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        m = read(data)
        fields = [("channels", len(m.channels)), ("positions", len(m.positions)),
                  ("measurements", len(m.recordings))]
        if m.trailing:
            fields.append(("trailing characters", f"{m.trailing:,} (ignored)"))
        sections = [file_section(path, data, *fields)]

        settings = m.data.get("CalibrationSettings") or {}
        rows = [(m.positions.index(r.position), m.designation(r.channel), r.enabled,
                 r.data.get("MicCorrectionName"), r.data.get("SystemDelay"),
                 r.data.get("AvrDistanceMeters"), r.data.get("AvrSpeakerType"),
                 len(r.ir), round(delay_ms(r), 3))
                for r in m.recordings]
        project = Section("project", [
            *_flat("", {k: v for k, v in settings.items()}),
            ("notes", m.data.get("ProjectNotes")),
            ("undetected channels", ", ".join(map(str, m.data.get("UndetectedChannels") or []))),
            ("position names", json.dumps(m.data.get("PositionNames")) if m.data.get(
                "PositionNames") else None),
            ("local microphones", ", ".join(m.data.get("UsedLocalMicrophones") or {})),
        ], Table(["position", "channel", "enabled", "mic", "system delay", "AVR distance m",
                  "AVR speaker type", "samples", "peak delay ms"], rows), raw=_elided(m.data))
        sections.append(project)

        for i, c in enumerate(m.channels):
            fields = [("guid", c.guid),
                      *_flat("", {k: v for k, v in c.data.items() if k != "Metadata"}),
                      *_flat("", {k: v for k, v in (c.data.get("Metadata") or {}).items()
                                  if k not in ("AvrOriginatingDesignation", "DisplayName")})]
            sections.append(response_section(f"channel {i} {c.designation} ({c.name})", fields,
                                             lambda: response(m, i)))

        if m.targets:
            rows = [(i, t.name, t.kind, t.describe(), _presets(t),
                     "all" if t.all else ", ".join(m.designation(g) for g in t.channels) or "none",
                     ", ".join(m.designation(g) for g in t.excluded))
                    for i, t in enumerate(m.targets)]
            sections.append(Section("target curves", [("components", len(m.targets))],
                                    Table(["#", "name", "kind", "value", "presets", "channels",
                                           "excluded"], rows)))
        for i, t in enumerate(m.targets):
            if t.kind == "custom":
                sections.append(Section(f"target curve {i} {t.name}", [
                    ("display name", t.value.get("DisplayName"))],
                    Table(["frequency Hz", "dB"], t.points())))
        return sections


FORMAT = Format("mqx", (".mqx",), "Audyssey MultEQ-X project", MqxInspector)
