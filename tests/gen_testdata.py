"""Generate the artificial test data in ``testdata/<source>/<format>/``.

    uv run python tests/gen_testdata.py [ROOT]

Every file is built from analytic curves and fixed values: no machine,
device, user or time metadata.  Files that a converter produces from other
test files are written by running that converter (``CONVERSIONS``).

Writers for formats that ``eqx`` only reads live here, not in the library:
REW ``.mdat``, encrypted PEQb, PEQb 3.0.0.0, ``.swmicpkg``,
Custom Target Presets and a REW ``.cal`` with a sensitivity line.
"""

from __future__ import annotations

import base64
import cmath
import json
import math
import struct
import sys
from pathlib import Path

from eqx import convert, formats
from eqx.convert.mdat_swproj import standard_grid
from eqx.soundid import crypto, peqb, swmicpkg, swproj

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
# conversions
# ---------------------------------------------------------------------------
MDAT_DIR, CAL_DIR, CSV_DIR = "rew/mdat", "rew/rewcal", "autoeq/csv"
PROJ_DIR, MIC_DIR, PEQB_DIR, PRESET_DIR = ("soundid/swproj", "soundid/swmicpkg",
                                           "soundid/peqb", "soundid/targetpreset")

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
]
PATH_OPTIONS = ("mic_profile", "right", "target_curve")


def run_conversion(root: Path, source: str, target: str, options: dict) -> convert.Result:
    options = {k: root / v if k in PATH_OPTIONS else v for k, v in options.items()}
    kind = formats.detect(Path(source))
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
