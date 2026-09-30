"""Generate the artificial test data in ``testdata/<source>/<format>/``.

    uv run python tests/gen_testdata.py [ROOT]

Every file is built from analytic curves and fixed values: no machine,
device, user or time metadata.  Files that a converter produces from other
test files are written by running that converter (``CONVERSIONS``).

Writers for formats that ``eqx`` only reads live here, not in the library:
REW ``.mdat``, encrypted PEQb, PEQb 3.0.0.0, ``.swmicpkg``,
Custom Target Presets, a REW ``.cal`` with a sensitivity line and the
SoundID device exports, IK Multimedia paks and ARC X files.
"""

from __future__ import annotations

import base64
import cmath
import json
import math
import struct
import sys
from pathlib import Path

from eqx import convert, dsp, formats
from eqx.convert.mdat_swproj import standard_grid
from eqx.ik import arcx
from eqx.soundid import crypto, peqb, swmicpkg, swproj
from eqx.soundid import export_lvnd, export_partners

ROOT = Path(__file__).resolve().parent.parent / "testdata"

# All-zero computer ID; the encrypted .swhp files open with it.
COMPUTER_ID = "g" + "0" * 40
SWPROJ_PASSWORD = "eqx"
ZERO_IV = bytes(16)
ANGLES = ("degrees_0", "degrees_30", "degrees_90")


# ---------------------------------------------------------------------------
# analytic curves
# ---------------------------------------------------------------------------
# A section is (numerator, denominator): polynomial coefficients in s,
# lowest power first.  Products of sections are minimum phase.
def _poly(c, s):
    return sum(a * s ** i for i, a in enumerate(c))


def _dpoly(c, s):
    return sum(i * a * s ** (i - 1) for i, a in enumerate(c) if i)


def hp2(f0, q):
    w = 2 * math.pi * f0
    return [0, 0, 1], [w * w, w / q, 1]


def lp2(f0, q):
    w = 2 * math.pi * f0
    return [w * w], [w * w, w / q, 1]


def hp1(f0):
    w = 2 * math.pi * f0
    return [0, 1], [w, 1]


def peak(f0, gain_db, q):
    w, a = 2 * math.pi * f0, 10 ** (gain_db / 40)
    return [w * w, w * a / q, 1], [w * w, w / (a * q), 1]


def high_cut(f0, gain_db):
    """First order: 0 dB at low frequencies, ``gain_db`` at high frequencies."""
    w, g = 2 * math.pi * f0, 10 ** (gain_db / 20)
    return [w, g], [w, 1]


def response(sections, f):
    """(dB, phase degrees, group delay seconds) of the sections at ``f`` Hz."""
    s = 2j * math.pi * f
    h, gd = 1 + 0j, 0.0
    for num, den in sections:
        h *= _poly(num, s) / _poly(den, s)
        # d(arg H)/dw = Re(H'(s)/H(s)); group delay is its negative.
        gd -= (_dpoly(num, s) / _poly(num, s) - _dpoly(den, s) / _poly(den, s)).real
    return 20 * math.log10(abs(h)), math.degrees(cmath.phase(h)), gd


# ---------------------------------------------------------------------------
# Java Object Serialization (REW .mdat)
# ---------------------------------------------------------------------------
# REW's MeasData field sets.  Order is the serialized order (primitives, then
# objects, each by name).  "Lname" fields list their class; "[name" is float[].
MEASDATA_UID = -1234567890123456789
_COMMON_PRIMITIVES = (
    "DalignSPLOffsetCumulative DalignSPLOffsetLast DasdOffset IaudioDataLength "
    "IaudioDataOffset DcalR DdBcOffset IdataLength FendFreq DfdwEndFreq DfdwFrac IfdwPPO "
    "DfdwStartFreq FfreqStep DhfFallSlope IhfFallStart {hpApplied}DimpedanceCal DinputR "
    "ZisFromRTA {isFsaf}ZisLogSpaced FlastUnwrapOffsetFreq DleadsR IlfRiseEnd DlfRiseSlope "
    "IlfRiseStart DlogStep DlogStepLog InumSweeps DoctaveFrac {overlaySelected}"
    "ZphaseIsUnwrapped Ippo {rawGDOctaveFrac}DrefR DroomHeight DroomLength DroomWidth "
    "IsampleRate JsourceFileDate IsourceType IspeakerBassMgmtShape IspeakerCutoff FstartFreq "
    "ZuseBars FvalidEndFreq FvalidStartFreq IxOverHPCutoff IxOverLPCutoff"
)
_OBJECTS_531 = (
    "LaddRoomCurve:Ljava/lang/Boolean; LcalDataLimitApplied:Ljava/lang/Boolean; "
    "LceaData:Lroomeqwizard/CEA2010Data; LdBVInputOffset:Ljava/lang/Double; "
    "LdBVOutputOffset:Ljava/lang/Double; LdistortionData:Lroomeqwizard/DistortionData; "
    "Leightc:Lroomeqwizard/Eightc; LeightcPosnsMeasured:Ljava/lang/String; "
    "LeightcProfilePosnIDs:Ljava/util/HashSet; LeightcProfileSt:Ljava/lang/String; "
    "LeightcRoomID:Ljava/lang/String; LeightcRoomName:Ljava/lang/String; "
    "Lenable:Ljava/lang/Boolean; LeqConfig:Lroomeqwizard/EQConfig; LeqName:Ljava/lang/String; "
    "[fdwImag [fdwReal LfilterSet:Lroomeqwizard/FilterSet; "
    "LfilterSetWhenMeasured:Lroomeqwizard/FilterSet; [gdValues "
    "LimpedanceCalType:Lroomeqwizard/ImpedanceCal; LinputVolume:Ljava/lang/Double; "
    "Lir:Lroomeqwizard/IRFloat; LirData:Lroomeqwizard/IRData; Ljre:Ljava/lang/String; "
    "Llocked:Ljava/lang/Boolean; LmeasNotes:Ljava/lang/String; LmeterCal:Lroomeqwizard/CalData; "
    "LmicSensitivity:Ljava/lang/Double; LnoiseFilter:Lroomeqwizard/NoiseFilter; "
    "LosName:Ljava/lang/String; LosVersion:Ljava/lang/String; LoutputName:Ljava/lang/String; "
    "LoutputVolume:Ljava/lang/Double; Lpeaks:Ljava/util/ArrayList; [phaseValues "
    "[rawPhaseValues [rawSourceSet [rawUnwPhaseValues [rawValues "
    "LrefResistancedB:Ljava/lang/Double; LrewSubVersion:Ljava/lang/Integer; "
    "LrewVersion:Ljava/lang/Integer; LroomData:Lroomeqwizard/RoomData; "
    "LrtaDist:Lroomeqwizard/RTADistortionData; LsavedColor:Ljava/awt/Color; "
    "LscCal:Lroomeqwizard/CalData; LshortDesc:Ljava/lang/String; LsigGenLevelSt:Ljava/lang/String; "
    "[sourceCalOffsets LsourceFileFormat:Ljava/lang/String; LsourceFileName:Ljava/lang/String; "
    "[sourceMax [sourceMaxdBFS [sourceMin [sourceMindBFS [sourceSet [sourceWeighting "
    "[sourcedBOffsets LspeakerBassMgmtSlope:Ljava/lang/Integer; "
    "LspeakerBassMgmtXOverSlope:Lroomeqwizard/XOverSlope; LspeakerLocn:Lroomeqwizard/Location; "
    "LsplCalOffset:Ljava/lang/Double; [splValues [steppedSineResults "
    "LsubLFCutoff:Ljava/lang/Integer; LsubLFSlope:Ljava/lang/Integer; "
    "LsubLFXOverSlope:Lroomeqwizard/XOverSlope; Lsweep:Lroomeqwizard/MeasSweepFunction; "
    "LsweepLevel:Ljava/lang/Double; Ltails:Lroomeqwizard/MinPhaseTails; "
    "LtargetLevel:Ljava/lang/Double; LtargetShape:Lroomeqwizard/TargetShape; "
    "LtimingRefInputName:Ljava/lang/String; LtimingRefName:Ljava/lang/String; "
    "LtimingReferenceMode:Lroomeqwizard/TimingReference; Lts:Lroomeqwizard/TSParams; "
    "[unwPhaseValues LusesEmbeddedCalData:Ljava/lang/Boolean; LversionSt:Ljava/lang/String; "
    "LxOverHPChoice:Lroomeqwizard/XOverChoice; LxOverLPChoice:Lroomeqwizard/XOverChoice;"
)
# Object fields added by REW 5.40, inserted before the named 5.31 field.
_ADDED_540 = {
    "fdwReal": ["LfdwPeakTime:Ljava/lang/Double;"],
    "impedanceCalType": ["LgroupColor:Ljava/awt/Color;", "LgroupName:Ljava/lang/String;",
                         "LgroupNotes:Ljava/lang/String;", "LgroupUuid:Ljava/lang/String;"],
    "inputVolume": ["Lindex3D:Ljava/lang/Double;"],
    "locked": ["Llabel3D:Ljava/lang/String;"],
    "rawPhaseValues": ["[rawGDValues"],
    "savedColor": ["LrtaLevelAdjustApplied:Ljava/lang/Boolean;",
                   "LrtaOctaveFrac:Ljava/lang/Integer;"],
    "shortDesc": ["LselectedOnOverlay:Ljava/lang/Boolean;"],
    "unwPhaseValues": ["Lunit3D:Ljava/lang/String;", "[unwPhaseRefPeak"],
    "versionSt": ["Luuid:Ljava/util/UUID;", "Luuid2:Lroomeqwizard/UUID;"],
}
_ARRAY_TYPES = {"rawSourceSet": "[[F", "sourceSet": "[[F",
                "steppedSineResults": "[Lroomeqwizard/SteppedSineDataPoint;"}


def _parse_fields(spec: str) -> list[tuple[str, str, str | None]]:
    fields = []
    for item in spec.split():
        code, rest = item[0], item[1:]
        if code == "L":
            name, cls = rest.split(":")
        elif code == "[":
            name, cls = rest, _ARRAY_TYPES.get(rest, "[F")
        else:
            name, cls = rest, None
        fields.append((code, name, cls))
    return fields


def measdata_fields(rew: tuple[int, int]) -> list:
    new = rew >= (5, 40)
    primitives = _COMMON_PRIMITIVES.format(
        hpApplied="ZhpApplied " if new else "",
        isFsaf="ZisFsafFileMeasurement ZisGroupPlaceHolder " if new else "",
        overlaySelected="ZoverlaySelected " if new else "",
        rawGDOctaveFrac="DrawGDOctaveFrac " if new else "")
    objects = _OBJECTS_531.split()
    if new:
        out = []
        for item in objects:
            name = item[1:].split(":")[0]
            out += _ADDED_540.get(name, [])
            out.append(item)
        objects = out
    return _parse_fields(primitives) + _parse_fields(" ".join(objects))


class JavaWriter:
    """Enough of ObjectOutputStream for REW's measurement file."""

    def __init__(self):
        self.out = bytearray(b"\xAC\xED\x00\x05")
        self.next_handle = 0x7E0000
        self.classes: dict[str, int] = {}
        self.strings: dict[str, int] = {}

    def _assign(self) -> int:
        self.next_handle += 1
        return self.next_handle - 1

    def string(self, value: str) -> None:
        if value in self.strings:
            self.out += b"\x71" + struct.pack(">I", self.strings[value])
            return
        data = value.encode("utf-8")        # ASCII here: same as modified UTF-8
        self.out += b"\x74" + struct.pack(">H", len(data)) + data
        self.strings[value] = self._assign()

    def block(self, data: bytes) -> None:
        self.out += b"\x77" + bytes([len(data)]) + data

    def null(self) -> None:
        self.out += b"\x70"

    def classdesc(self, name, uid, flags, fields=(), super_class=None) -> None:
        if name in self.classes:
            self.out += b"\x71" + struct.pack(">I", self.classes[name])
            return
        data = name.encode()
        self.out += b"\x72" + struct.pack(">H", len(data)) + data
        self.out += struct.pack(">qBH", uid, flags, len(fields))
        self.classes[name] = self._assign()
        for code, field, cls in fields:
            data = field.encode()
            self.out += code.encode() + struct.pack(">H", len(data)) + data
            if code in "L[":
                self.string(cls)
        self.out += b"\x78"
        if super_class is None:
            self.null()
        else:
            self.classdesc(*super_class)

    def array(self, signature: str, uid: int, fmt: str, values) -> None:
        self.out += b"\x75"
        self.classdesc(signature, uid, 0x02)
        self._assign()
        self.out += struct.pack(">i", len(values)) + struct.pack(f">{len(values)}{fmt}", *values)

    def float_array(self, values) -> None:
        self.array("[F", 836686056779680834, "f", values)

    def boxed(self, kind: str, value) -> None:
        number = ("java.lang.Number", -8742448824652078965, 0x02)
        desc = {
            "Boolean": ("java.lang.Boolean", -3665804199014368530, 0x02, [("Z", "value", None)]),
            "Integer": ("java.lang.Integer", 1360826667806852920, 0x02, [("I", "value", None)],
                        number),
            "Double": ("java.lang.Double", -9172774392245257468, 0x02, [("D", "value", None)],
                       number),
        }[kind]
        self.out += b"\x73"
        self.classdesc(*desc)
        self._assign()
        self.out += struct.pack({"Boolean": ">?", "Integer": ">i", "Double": ">d"}[kind], value)

    def object(self, desc, values: dict) -> None:
        """A plain Serializable object; missing values are 0, false or null."""
        self.out += b"\x73"
        self.classdesc(*desc)
        self._assign()
        for code, name, _cls in desc[3]:
            value = values.get(name)
            if code in "L[":
                if value is None:
                    self.null()
                else:
                    value(self)             # a callable writes the object
            else:
                fmt = {"B": ">b", "Z": ">?", "C": ">H", "S": ">h", "I": ">i", "J": ">q",
                       "F": ">f", "D": ">d"}[code]
                self.out += struct.pack(fmt, value or 0)

    def image_icon(self, width: int, height: int, argb: int, rew: tuple[int, int]) -> None:
        context = ("Lroomeqwizard/IconStub$_A;" if rew >= (5, 40)
                   else "Ljavax/swing/ImageIcon$AccessibleImageIcon;")
        fields = [("I", "height", None), ("I", "width", None),
                  ("L", "accessibleContext", context), ("L", "description", "Ljava/lang/String;"),
                  ("L", "imageObserver", "Ljava/awt/image/ImageObserver;")]
        self.out += b"\x73"
        self.classdesc("javax.swing.ImageIcon", -962022720109015502, 0x03, fields)
        self._assign()
        self.out += struct.pack(">ii", height, width) + b"\x70\x70\x70"
        self.block(struct.pack(">ii", width, height))
        self.array("[I", 5600894804908749477, "i", [argb] * (width * height))
        self.out += b"\x78"


def _f32(x: float) -> float:
    # Rounding first keeps the float32 values independent of libm last bits.
    return struct.unpack(">f", struct.pack(">f", round(x, 5)))[0]


def write_mdat(channels, sample_rate: int, fft_length: int, rew: tuple[int, int]) -> bytes:
    """``channels``: [(short description, level dB SPL, sections)]."""
    n = fft_length // 2 - 1
    step = sample_rate / fft_length
    freqs = [step * (i + 1) for i in range(n)]
    curves = []
    for desc, level, sections in channels:
        points = [response(sections, f) for f in freqs]
        curves.append((desc,
                       [_f32(level + p[0]) for p in points],
                       [_f32(p[1]) for p in points],
                       [_f32(p[2] * 1000) for p in points]))      # REW: ms

    w = JavaWriter()
    w.string("REW Measurement Data File V2")
    w.block(struct.pack(">ii", *rew))
    w.string("Notes:")
    w.block(struct.pack(">i", len(curves)))
    for _desc, spl, _phase, _gd in curves:
        w.image_icon(130, 70, -12566464, rew)                    # 0xFF404040
        w.string('<HTML><style type="text/css">body { margin-left: 3; }</style><BODY>'
                 f"Jan 1, 1970<BR>12:00:00 AM<BR>0 to {sample_rate // 2:,} Hz<BR>"
                 f"{math.floor(min(spl))} to {math.ceil(max(spl))} dB SPL</HTML>")
    fields = measdata_fields(rew)
    desc = ("roomeqwizard.MeasData", MEASDATA_UID, 0x02, fields)
    version = ".3" if rew < (5, 40) else " beta 1"
    for short_desc, spl, phase, gd in curves:
        w.block(struct.pack(">i", 3))
        values = {
            "dataLength": n, "startFreq": step, "freqStep": step,
            "endFreq": freqs[-1], "validStartFreq": step, "validEndFreq": freqs[-1],
            "sampleRate": sample_rate, "numSweeps": 1, "sourceType": 5,
            "impedanceCal": 1.0, "hfFallSlope": 0.5, "hfFallStart": 1000,
            "lfRiseEnd": 20, "lfRiseSlope": 1.0, "lfRiseStart": 200,
            "roomHeight": 2.4, "roomLength": 5.0, "roomWidth": 4.0,
            "speakerCutoff": 80, "xOverHPCutoff": 100, "xOverLPCutoff": 1000,
            "enable": lambda w: w.boxed("Boolean", True),
            "locked": lambda w: w.boxed("Boolean", False),
            "eqName": lambda w: w.string("Generic"),
            "measNotes": lambda w: w.string(""),
            "shortDesc": lambda w, s=short_desc: w.string(s),
            "sourceFileFormat": lambda w: w.string("Synthetic"),
            "sourceFileName": lambda w: w.string(""),
            "rewVersion": lambda w: w.boxed("Integer", rew[0]),
            "rewSubVersion": lambda w: w.boxed("Integer", rew[1]),
            "versionSt": lambda w: w.string(version),
            "splValues": lambda w, v=spl: w.float_array(v),
            "phaseValues": lambda w, v=phase: w.float_array(v),
            "gdValues": lambda w, v=gd: w.float_array(v),
        }
        w.object(desc, values)
    w.block(struct.pack(">i", 0))
    return bytes(w.out)


MDAT = {
    # name: (channels, sample rate, FFT length, REW version)
    "Flat": ([("L Flat", 85.0, []), ("R Flat", 85.0, [])], 48000, 16384, (5, 31)),
    "Bandpass": ([
        ("L Bandpass", 88.0, [hp2(55, 0.71), lp2(18000, 0.71), peak(2500, 3, 2)]),
        ("R Bandpass", 87.0, [hp2(55, 0.71), lp2(18000, 0.71), peak(2500, 3, 2)]),
    ], 48000, 16384, (5, 40)),
    "Room": ([
        ("L Room", 83.0, [hp2(35, 0.8), peak(45, 8, 5), peak(120, -15, 8), peak(240, -6, 6),
                          high_cut(4000, -6)]),
        ("R Room", 81.0, [hp2(35, 0.8), peak(52, 6, 4), peak(150, -16, 6),
                          high_cut(4000, -6)]),
    ], 44100, 16384, (5, 31)),
    "Left only": ([("L Left only", 90.0, [hp2(45, 0.71), high_cut(2000, -3)])],
                  48000, 16384, (5, 40)),
}


# ---------------------------------------------------------------------------
# SoundID microphone package, REW calibration
# ---------------------------------------------------------------------------
def mic_grid() -> list[float]:
    return [20.0 * 1000.0 ** (i / 299) for i in range(300)]


MICS = {
    "FLAT01": {a: [] for a in ANGLES},
    "TILT01": {
        "degrees_0": [hp1(10), peak(12000, 3, 0.7)],
        "degrees_30": [hp1(10), peak(12000, 1.5, 0.7)],
        "degrees_90": [hp1(10), high_cut(8000, -3)],
    },
}


def mic_table(sections) -> str:
    return "".join(f"{f:.1f}\t{round(response(sections, f)[0], 2) + 0.0:.2f}\n"
                   for f in mic_grid())


def write_swmicpkg(tables: dict) -> bytes:
    package = {}
    for angle, sections in tables.items():
        blob = mic_table(sections).encode()
        if angle != swmicpkg.PLAIN_ANGLE:
            blob = crypto.encrypt(swmicpkg.PACKAGE_KEY, blob, ZERO_IV)
        package[angle] = base64.b64encode(blob).decode()
    return json.dumps(package, separators=(",", ":")).encode()


def write_cal_with_sensitivity(sections) -> bytes:
    """REW cal variant: quoted sensitivity line, three columns (phase 0)."""
    lines = ['"Sens Factor =-1.5dB, SERNO: TILT01"']
    lines += [f"{f:.3f} {response(sections, f)[0]:.3f} 0.000" for f in mic_grid()]
    return ("\r\n".join(lines) + "\r\n").encode()


# ---------------------------------------------------------------------------
# PEQb headphone profiles
# ---------------------------------------------------------------------------
def write_peqb_v1(curves, parameters: dict, version: tuple, computer_id: str | None) -> bytes:
    """PEQb 3.0.0.0/3.0.0.1, encrypted with ``computer_id`` unless it is None."""
    body = peqb.body_v1(curves, parameters)
    header = peqb.MAGIC + bytes(version)
    if version == (3, 0, 0, 0):
        return header + body
    if computer_id is None:
        return header + b"\x00" + body
    return header + b"\x01" + crypto.encrypt(crypto.swhp_key(computer_id),
                                             peqb.BODY_HEADER + body, ZERO_IV)


def headphone_curves(sections, band_db: float):
    correction = []
    for f in standard_grid():
        db, _phase, gd = response(sections, f)
        correction.append((f, round(db, 6), round(gd, 9)))
    return peqb.headphone_curves(correction, correction, band_db)


TILT_HEADPHONE = [peak(60, 4, 0.7), peak(3000, -3, 2), peak(9000, 5, 3)]
SWHP = {
    # name: (sections, make, model, average, error band, version, computer ID)
    "Flat Flat Wired": ([], "Flat", "Flat", False, 0.9, (3, 0, 0, 1), COMPUTER_ID),
    "Flat Flat Wired 3.0.0.1 plain": ([], "Flat", "Flat", False, 0.9, (3, 0, 0, 1), None),
    "Flat Flat Wired 3.0.0.0": ([], "Flat", "Flat", False, 0.9, (3, 0, 0, 0), None),
    "Tilt Tilt Wired Average": (TILT_HEADPHONE, "Tilt", "Tilt", True, 3.0, (3, 0, 0, 1),
                                COMPUTER_ID),
}


# ---------------------------------------------------------------------------
# Custom Target Presets
# ---------------------------------------------------------------------------
def _filters(*specs):
    return [{"colorId": i, "enabled": enabled, "frequency": float(f), "gain": float(g),
             "id": i + 1, "q": float(q), "type": t}
            for i, (t, f, g, q, enabled) in enumerate(specs)]


FLAT_FILTERS = _filters(("low-shelf", 50, 0, 1, True), ("bell", 700, 0, 1, True),
                        ("high-shelf", 8000, 0, 1, True))


def _preset(name, enabled, flip, low, high, filters):
    return {"filterGroups": [{"cutoff": {"enabled": enabled, "flipState": flip,
                                         "leftFreq": float(low), "rightFreq": float(high)},
                              "filters": filters}],
            "name": name}


PRESETS = [
    _preset("Flat", True, "inner", 20, 20000, FLAT_FILTERS),
    _preset("Bass and treble", True, "inner", 30, 16000, _filters(
        ("low-shelf", 120, 4, 0.7, True), ("bell", 2000, -1.5, 1.4, True),
        ("bell", 5000, 2, 3, False), ("high-shelf", 8000, -2, 1, True))),
    _preset("Mid band outer", True, "outer", 1500, 4000, FLAT_FILTERS),
    _preset("Correction off", False, "inner", 20, 20000, FLAT_FILTERS),
]


# ---------------------------------------------------------------------------
# SoundID device exports
# ---------------------------------------------------------------------------
# Bells (frequency, gain dB, Q) of each channel's correction.
SPEAKER = {
    "Left": [(60, 6, 0.7), (1000, -3, 1.4), (8000, 4, 2)],
    "Right": [(80, 5, 0.9), (1500, -2, 2), (9000, 3, 1.5)],
}
EXPORT_NAME = "Tilt"
MERGING_SERIAL = "A000042"
# SoundID writes this truncated GUID.
EXPORT_GUID = "00000000-0000-0000-0000-0000"
# The bands of SoundID's graphic EQ export.
THIRD_OCTAVES = (40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250,
                 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000)
LVND_SIZE = 4096


def bells(side: str, rate: float = dsp.PEQ_SAMPLE_RATE) -> list:
    return [dsp.bell(f, g, q, rate) for f, g, q in SPEAKER[side]]


def _normalized(bq) -> list[float]:
    return [bq.b0 / bq.a0, bq.b1 / bq.a0, bq.b2 / bq.a0, 1.0, bq.a1 / bq.a0, bq.a2 / bq.a0]


def _partner_key(partner_id: str) -> str:
    return next(p for p in export_partners.PARTNERS if p.id == partner_id).key


def _export_json(obj) -> bytes:
    return json.dumps(obj, indent=4, sort_keys=True).encode()


def write_biquad_json(rates, key: str) -> bytes:
    root = {
        "channel_config": [
            {"channels": [{"balance_gain": 0.0, "coefs": [_normalized(b) for b in bells(side, r)],
                           "delay": 0.0, "post_gain": 0.0, "pre_gain": 0.0, "type": side}
                          for side in SPEAKER],
             "profile_id": i + 1}
            for i, r in enumerate(rates)],
        "name": EXPORT_NAME,
        "profile_configs": [{"dsp_type": "Biquad", "id": i + 1, "sample_rate": str(r)}
                            for i, r in enumerate(rates)],
        "safe_headroom": -12.0,
        "target_mode": "Flat",
    }
    return export_partners.encrypt(key, _export_json(root), ZERO_IV)


def write_peq_json(filter_type: str, key: str) -> bytes:
    root = {
        "channels": [
            {"guid": EXPORT_GUID, "listeningSpotCompensationData": {"delay": 0.0, "gain": 0.0},
             "name": side[0],
             "peqFilters": [{"frequency": f, "gain": float(g), "qFactor": float(q),
                             "type": filter_type} for f, g, q in SPEAKER[side]]}
            for side in SPEAKER],
        "layoutType": "2.0 (Stereo)",
        "name": EXPORT_NAME,
        "safeHeadroomDb": -12.0,
        "targetMode": "Flat",
    }
    return export_partners.encrypt(key, _export_json(root), ZERO_IV)


def write_biquad_xml(rates) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<roomCorrection>"]
    for i, r in enumerate(rates):
        lines += [f'\t<profile id="{i}" type="biquad" sampleRate="{r}" '
                  f'friendlyName="{EXPORT_NAME}">', "\t\t<tags/>",
                  '\t\t<listeningSpotOptimisation enabled="1"/>']
        for side in SPEAKER:
            lines += [f'\t\t<channel friendlyName="{side}">', '\t\t\t<delay ms="0"/>',
                      '\t\t\t<makeupGain dB="0"/>']
            for n, bq in enumerate(bells(side, r)):
                b0, b1, b2, a0, a1, a2 = _normalized(bq)
                lines.append(f'\t\t\t<biquad idx="{n}" a0="{a0:.17g}" a1="{a1:.17g}" '
                             f'a2="{a2:.17g}" b0="{b0:.17g}" b1="{b1:.17g}" b2="{b2:.17g}"/>')
            lines.append("\t\t</channel>")
        lines.append("\t</profile>")
    lines.append("</roomCorrection>")
    return export_partners.encrypt(_partner_key("adam"), ("\n".join(lines) + "\n\n").encode(),
                                   ZERO_IV)


def _f32_bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _lvnd_message(index: int, fields: list[int]) -> bytes:
    check = 0
    for v in fields:
        check ^= v
    body = b"".join(export_lvnd.encode_field(v) for v in [*fields, check])
    return bytes([export_lvnd.STX, ord("0") + index % 10]) + body + bytes([export_lvnd.ETX])


def write_lvnd(side: str, gain_db: float, delay_ms: float) -> bytes:
    """Parameter addresses as SoundID writes them."""
    messages = []
    for n, bq in enumerate(bells(side, export_lvnd.SAMPLE_RATE)):
        b0, b1, b2, _a0, a1, a2 = _normalized(bq)
        messages.append(_lvnd_message(n, [export_lvnd.BIQUAD, ((30000 + n) << 12) | 0xB, 0, 2048, 5,
                                          *map(_f32_bits, (b0, b1, b2, a1, a2))]))
    n = len(messages)
    messages.append(_lvnd_message(n, [export_lvnd.GAIN, (31000 << 12) | 8, _f32_bits(gain_db),
                                      256, 0]))
    samples = round(delay_ms * export_lvnd.SAMPLE_RATE / 1000)
    messages.append(_lvnd_message(n + 1, [export_lvnd.DELAY, (30500 << 12) | 9, samples, 512, 0]))
    body = b"".join(messages)
    data = export_lvnd.HEADER.pack(export_lvnd.MAGIC, 0, 0, len(body)) + body
    return data + bytes(LVND_SIZE - len(data))


def _text_export(tables: dict[str, list[str]]) -> bytes:
    lines = [f"Preset name: {EXPORT_NAME}", f"Profile name: {EXPORT_NAME}", "Target mode: Flat",
             "Audio setup: 2.0 (Stereo)", ""]
    for side, rows in tables.items():
        lines += [f"{side[0]} channel calibration:", "Delay: 0 ms", "Gain: 0 dB", "", *rows, ""]
    return "\n".join(lines).encode()


def _cells(*cells: tuple[str, int]) -> str:
    return "|" + "|".join(f"{text:<{width}}" for text, width in cells) + "|"


def write_graphic_text() -> bytes:
    tables = {}
    for side in SPEAKER:
        rows = [_cells(("Freq", 10), ("Gain", 10)),
                _cells((":" + "-" * 9, 10), (":" + "-" * 9, 10))]
        for f in THIRD_OCTAVES:
            g = round(dsp.cascade_db(bells(side), f, dsp.PEQ_SAMPLE_RATE), 1) + 0.0
            rows.append(_cells((f"{f:<8g}Hz", 10), (f"{g:<8g}dB", 10)))
        tables[side] = rows
    return _text_export(tables)


def write_peq_text() -> bytes:
    tables = {}
    for side in SPEAKER:
        rows = [_cells(("Type", 20), ("Freq", 10), ("Gain", 10), ("Q", 10)),
                _cells((":" + "-" * 19, 20), *[(":" + "-" * 9, 10)] * 3)]
        for n, (f, g, q) in enumerate(SPEAKER[side], 1):
            rows.append(_cells((f"Parametric Eq {n}", 20), (f"{f:<8g}Hz", 10),
                               (f"{g:<8g}dB", 10), (f"{q:g}", 10)))
        tables[side] = rows
    return _text_export(tables)


def write_tmreq() -> bytes:
    lines = ["<Preset>"]
    for side, filters in SPEAKER.items():
        # The unused bands are flat.
        bands = filters + [(1000 * (n + 1), 0, 1) for n in range(9 - len(filters))]
        lines += [f"\t<Room EQ {side[0]}>", "\t\t<Params>", '\t\t\t<val e="REQ Delay" v="0.00,"/>']
        for n, (f, g, q) in enumerate(bands, 1):
            lines += [f'\t\t\t<val e="REQ Band{n} Freq" v="{f},"/>',
                      f'\t\t\t<val e="REQ Band{n} Q" v="{q:.2f},"/>',
                      f'\t\t\t<val e="REQ Band{n} Gain" v="{g:.2f},"/>']
        lines += ['\t\t\t<val e="REQ Band1Type" v="0.00,"/>',
                  '\t\t\t<val e="REQ Band8 Type" v="0.00,"/>',
                  '\t\t\t<val e="REQ Band9 Type" v="0.00,"/>',
                  '\t\t\t<val e="Chan Gain" v="0.00,"/>', "\t\t</Params>",
                  f"\t</Room EQ {side[0]}>"]
    lines.append("</Preset>")
    return ("\n".join(lines) + "\n").encode()


# ---------------------------------------------------------------------------
# IK Multimedia ARC X
# ---------------------------------------------------------------------------
ARCX_DIR = "ik/arcx"
# ARC X writes and processes 32768 samples.
ARCX_IR_LENGTH = 32768
ARCX_GUID = "00000000-0000-0000-0000-000000000000"
# Speaker device: IK product | serial | two a.b.c versions | 0 or 1.  ARC X
# loads no session whose speakers lack a product and a serial.  183: ARC Studio.
ARCX_DEVICE = "183|000000000|1.0.0|1.0.0|0"
# Level offset dB of each measurement point.
ARCX_POINT_GAINS = (0.0, -1.0, 2.0)
# Time of flight, samples, per speaker.
ARCX_DELAY = {"Left": 150, "Right": 160, "Subwoofer": 170}
# name: (layout id, layout name, speakers, points, sample rate, with session)
ARCX = {
    "Arc.arcXs": (1, "Stereo", ("Left", "Right"), 3, 48000, True),
    "Arc.arcXa": (1, "Stereo", ("Left", "Right"), 3, 48000, False),
    "Arc Sub.arcXs": (2, "Stereo + Sub", ("Left", "Right", "Subwoofer"), 1, 44100, True),
}
ARCX_SETTINGS = (
    [("HpfFrequency", "20.0"), ("DelayMs", "0.0"), ("GainDb", "0.0"), ("DelayEnable", "1"),
     ("GainEnable", "1"), ("CalEnable", "1"), ("PhaseInvert", "0"), ("PhaseInvertEnable", "1"),
     ("FilterLowType", "0"), ("FilterHighType", "0"), ("VoiceIndex", "0"), ("FilterEnable", "0"),
     ("VoiceEnable", "0"), ("DimAttenuationDb", "0.0"), ("ActivePreset", "0"),
     ("FilterLowFrequency", "100.0"), ("FilterLowGainDb", "0.0"), ("FilterLowQ", "0.7")]
    + [(f"PeakFilters{n}{k}", v) for n in range(4)
       for k, v in (("Frequency", "1000.0"), ("GainDb", "0.0"), ("Q", "1.0"))]
    + [("FilterHighFrequency", "10000.0"), ("FilterHighGainDb", "0.0"), ("FilterHighQ", "0.7")]
)


def arcx_filters(speaker: str, rate: float) -> list:
    if speaker == "Subwoofer":
        return [dsp.pass_filter(False, 100, 0.7071, rate)]
    return bells(speaker, rate)


def arcx_ir(speaker: str, gain_db: float, rate: float) -> list[float]:
    """Unit impulse at the time of flight, through the speaker's biquads."""
    x = [0.0] * ARCX_IR_LENGTH
    x[ARCX_DELAY[speaker]] = 10 ** (gain_db / 20)
    for bq in arcx_filters(speaker, rate):
        y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
        for v in x:
            out = (bq.b0 * v + bq.b1 * x1 + bq.b2 * x2 - bq.a1 * y1 - bq.a2 * y2) / bq.a0
            y.append(out)
            x1, x2, y1, y2 = v, x1, out, y1
        x = y
    return x


def write_float_wav(samples: list[float], rate: int) -> bytes:
    data = struct.pack(f"<{len(samples)}f", *samples)
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)


def write_pak(entries: dict[str, bytes]) -> bytes:
    """Version 3, entries sorted by name, as ARC X writes them."""
    names = sorted(entries)
    table_size = sum(len(n.encode()) + 17 for n in names)
    offset = 26 + table_size
    table, body = b"", b""
    for name in names:
        table += name.encode() + b"\0" + struct.pack("<QQ", offset + len(body), len(entries[name]))
        body += entries[name]
    return b"IKMPAK" + struct.pack("<IQQ", 3, len(names), table_size) + table + body


def _value_tree(tag: str, attributes, children=(), indent: str = "") -> list[str]:
    attrs = "".join(f' {k}="{v}"' for k, v in attributes)
    if not children:
        return [f"{indent}<{tag}{attrs}/>"]
    lines = [f"{indent}<{tag}{attrs}>"]
    for child in children:
        lines += _value_tree(*child, indent=indent + "  ")
    return lines + [f"{indent}</{tag}>"]


def _juce_xml(lines: list[str]) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n\n' + "\n".join(lines) + "\n").encode()


def write_arcx(layout: int, layout_name: str, speakers, points: int, rate: int,
               session: bool) -> bytes:
    entries = {"info.xml": _juce_xml(_value_tree("SerializedMeasure", [
        ("Version", "5.0.0"), ("SampleRate", f"{rate:.1f}"), ("SelectedMicType", "MEMS"),
        ("CorrectionSpeaker", ARCX_GUID), ("NumMeasurementPoints", points),
        ("Layout", layout_name), ("ListeningArea", "Project Studio"), ("FastMode", "0"),
        ("Countdown", "5")]))}
    positions = {name: n for n, name in arcx.POSITIONS.items()}
    for c, speaker in enumerate(speakers):
        cc = [0.0] * ARCX_IR_LENGTH
        cc[ARCX_DELAY[speaker]] = 1.0
        for p in range(points):
            ir = arcx_ir(speaker, ARCX_POINT_GAINS[p], rate)
            entries[f"ch{c}/ch{c}p{p}_ir.wav"] = write_float_wav(ir, rate)
            entries[f"ch{c}/ch{c}p{p}_cc.wav"] = write_float_wav(cc, rate)
    if session:
        entries["session.xml"] = _juce_xml(_value_tree("Session", [
            ("Version", 1), ("AppVersion", "2.0.2 (26D30)"), ("GUID", ARCX_GUID),
            ("Layout", layout), ("CorrectionType", 1), ("CorrectionPhase", 1),
            ("MasterRemoteSpeakerIndex", -1), ("BassManaged", 0), ("TargetRequest", 0),
            ("Notes", ""), ("AudioDeviceName", ""), ("AudioDeviceSampleRate", rate),
            ("AudioDeviceBufferSize", 512)], [
            (f"Speaker_{positions[s]}", [
                ("Device", ARCX_DEVICE), ("CalRangeLow", 20), ("CalRangeHigh", 20000),
                ("CorrectionAssetGUID", ARCX_GUID), ("OutputChannelIndex", c)],
             [("Settings", ARCX_SETTINGS)])
            for c, s in enumerate(speakers)]))
    return write_pak(entries)


def export_files() -> dict[str, bytes]:
    return {
        f"{BIQUAD_JSON_DIR}/Tilt Fluid.bin": write_biquad_json((96000, 192000),
                                                             _partner_key("fluid")),
        f"{BIQUAD_JSON_DIR}/Tilt MERGING.bin": write_biquad_json((44100, 48000), MERGING_SERIAL),
        f"{PEQ_JSON_DIR}/Tilt Grace.bin": write_peq_json("Peak", _partner_key("grace-design")),
        f"{PEQ_JSON_DIR}/Tilt Lynx.bin": write_peq_json("Parametric", _partner_key("lynx-aurora")),
        f"{BIQUAD_XML_DIR}/Tilt.adam": write_biquad_xml((44100, 48000)),
        f"{LVND_DIR}/Tilt_192000_Left.bin": write_lvnd("Left", -1.5, 0.5),
        f"{LVND_DIR}/Tilt_192000_Right.bin": write_lvnd("Right", 0.0, 0.0),
        f"{EXPORT_TXT_DIR}/Tilt - Flat.txt": write_graphic_text(),
        f"{EXPORT_TXT_DIR}/Tilt - Flat - 8 - PEQ.txt": write_peq_text(),
        f"{TMREQ_DIR}/Tilt - Flat.tmreq": write_tmreq(),
    }


# ---------------------------------------------------------------------------
# conversions
# ---------------------------------------------------------------------------
MDAT_DIR, CAL_DIR, CSV_DIR = "rew/mdat", "rew/rewcal", "autoeq/csv"
PROJ_DIR, MIC_DIR, PEQB_DIR, PRESET_DIR = ("soundid/swproj", "soundid/swmicpkg",
                                           "soundid/peqb", "soundid/targetpreset")
BIQUAD_JSON_DIR = "soundid/soundid-export-biquad-json"
PEQ_JSON_DIR = "soundid/soundid-export-peq-json"
BIQUAD_XML_DIR = "soundid/soundid-export-biquad-xml"
LVND_DIR = "soundid/soundid-export-lvnd"
EXPORT_TXT_DIR = "soundid/soundid-export-txt"
TMREQ_DIR = "rme/tmreq"

# (input, output, converter options); paths relative to the root.  In
# dependency order: a later input may be an earlier output.
CONVERSIONS = [
    *((f"{MIC_DIR}/{serial}.swmicpkg", f"{CAL_DIR}/{serial} {angle}.txt", {"angle": angle})
      for serial in MICS for angle in ANGLES),
    (f"{MDAT_DIR}/Flat.mdat", f"{PROJ_DIR}/Flat.swproj",
     {"mic_profile": f"{MIC_DIR}/FLAT01.swmicpkg"}),
    (f"{MDAT_DIR}/Bandpass.mdat", f"{PROJ_DIR}/Bandpass.swproj",
     {"mic_profile": f"{MIC_DIR}/TILT01.swmicpkg", "mic_angle": "degrees_30"}),
    (f"{MDAT_DIR}/Room.mdat", f"{PROJ_DIR}/Room.swproj",
     {"mic_profile": f"{MIC_DIR}/TILT01.swmicpkg", "max_boost_db": 6.0,
      "low_cutoff_hz": 40.0, "high_cutoff_hz": 16000.0}),
    (f"{MDAT_DIR}/Left only.mdat", f"{PROJ_DIR}/Left only.swproj",
     {"mic_profile": f"{PROJ_DIR}/Flat.swproj", "reference_spl": 80.0}),
    (f"{MDAT_DIR}/Room.mdat", f"{CSV_DIR}/Room Left.csv", {}),
    (f"{MDAT_DIR}/Room.mdat", f"{CSV_DIR}/Room Right.csv", {"channel": "right"}),
    (f"{PROJ_DIR}/Bandpass.swproj", f"{CSV_DIR}/Bandpass Left.csv", {}),
    (f"{PEQB_DIR}/Tilt Tilt Wired Average.swhp", f"{CSV_DIR}/Tilt Tilt Wired Average Left.csv",
     {"computer_id": COMPUTER_ID}),
    (f"{PRESET_DIR}/Bass and treble.json", f"{CSV_DIR}/Bass and treble.csv", {}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{CAL_DIR}/Bass and treble.txt", {}),
    (f"{CSV_DIR}/Room Left.csv", f"{PROJ_DIR}/Room Left.swproj",
     {"right": f"{CSV_DIR}/Room Right.csv", "mic_profile": f"{MIC_DIR}/TILT01.swmicpkg"}),
    (f"{CSV_DIR}/Tilt Tilt Wired Average Left.csv", f"{PEQB_DIR}/Tilt Tilt Wired Average Left.swhp",
     {"make": "Tilt", "model": "Tilt", "reference_db": 0.0}),
    (f"{BIQUAD_JSON_DIR}/Tilt Fluid.bin", f"{CSV_DIR}/Tilt Fluid Left.csv", {}),
    (f"{EXPORT_TXT_DIR}/Tilt - Flat.txt", f"{CSV_DIR}/Tilt - Flat Right.csv", {"channel": "right"}),
    (f"{LVND_DIR}/Tilt_192000_Left.bin", f"{CSV_DIR}/Tilt_192000_Left.csv", {}),
    (f"{ARCX_DIR}/Arc.arcXs", f"{CSV_DIR}/Arc Left.csv", {}),
    (f"{ARCX_DIR}/Arc Sub.arcXs", f"{CSV_DIR}/Arc Sub Subwoofer.csv", {"speaker": "Subwoofer"}),
    (f"{ARCX_DIR}/Arc.arcXs", f"{PROJ_DIR}/Arc.swproj",
     {"mic_profile": f"{MIC_DIR}/FLAT01.swmicpkg"}),
]
PATH_OPTIONS = ("mic_profile", "right", "target_curve")


def run_conversion(root: Path, source: str, target: str, options: dict) -> convert.Result:
    options = {k: root / v if k in PATH_OPTIONS else v for k, v in options.items()}
    kind = formats.detect(root / source)
    pair = convert.find(kind, formats.detect(Path(target), (t for s, t in convert.CONVERTERS
                                                             if s == kind)))
    return pair(**options).convert(root / source)


def generate(root: Path = ROOT) -> list[Path]:
    """Write every file below ``root``; return the paths."""
    files: dict[str, bytes] = {}
    for name, (channels, rate, length, rew) in MDAT.items():
        files[f"{MDAT_DIR}/{name}.mdat"] = write_mdat(channels, rate, length, rew)
    for serial, tables in MICS.items():
        files[f"{MIC_DIR}/{serial}.swmicpkg"] = write_swmicpkg(tables)
    files[f"{CAL_DIR}/TILT01 sensitivity.cal"] = write_cal_with_sensitivity(
        MICS["TILT01"]["degrees_0"])
    for name, (sections, make, model, average, band, version, cid) in SWHP.items():
        files[f"{PEQB_DIR}/{name}.swhp"] = write_peqb_v1(
            headphone_curves(sections, band), peqb.headphone_parameters(make, model, average, band),
            version, cid)
    for preset in PRESETS:
        files[f"{PRESET_DIR}/{preset['name']}.json"] = json.dumps(
            preset, sort_keys=True, separators=(",", ":")).encode()
    files.update(export_files())
    for name, spec in ARCX.items():
        files[f"{ARCX_DIR}/{name}"] = write_arcx(*spec)

    written = []
    for rel, data in files.items():
        written.append(_write(root / rel, data))
    for source, target, options in CONVERSIONS:
        written.append(_write(root / target, run_conversion(root, source, target, options).data))

    flat = swproj.SwProj.open(root / PROJ_DIR / "Flat.swproj")
    written.append(_write(root / PEQB_DIR / "Flat.eqb", flat.part("eqb")))
    written.append(_write(root / PROJ_DIR / "Flat password.swproj",
                          swproj.write(flat.xml, flat.part("eqb"), SWPROJ_PASSWORD.encode())))
    return written


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT
    for path in generate(root):
        print(f"{path.stat().st_size:>9,}  {path.relative_to(root).as_posix()}")
