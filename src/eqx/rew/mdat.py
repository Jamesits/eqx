"""Reader and writer for Room EQ Wizard measurement files (``*.mdat``).

An ``.mdat`` file is Java Object Serialization data.  This module contains a
small stream parser and writer so that no Java or REW installation is needed.
"""

from __future__ import annotations

import datetime as _datetime
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..fileformat import Format, Inspector, file_section, frequency_range
from ..model import EPOCH, Measurement
from ..report import Section, Table

TC_NULL = 0x70
TC_REFERENCE = 0x71
TC_CLASSDESC = 0x72
TC_OBJECT = 0x73
TC_STRING = 0x74
TC_ARRAY = 0x75
TC_CLASS = 0x76
TC_BLOCKDATA = 0x77
TC_ENDBLOCKDATA = 0x78
TC_RESET = 0x79
TC_BLOCKDATALONG = 0x7A
TC_EXCEPTION = 0x7B
TC_LONGSTRING = 0x7C
TC_PROXYCLASSDESC = 0x7D
TC_ENUM = 0x7E
SC_WRITE_METHOD = 0x01
SC_EXTERNALIZABLE = 0x04
BASE_HANDLE = 0x7E0000


@dataclass
class _JavaRef:
    handle: int


@dataclass
class _JavaToken:
    kind: str
    value: Any = None


@dataclass
class _JavaClass:
    name: str
    serial_uid: int
    flags: int
    fields: list[tuple[str, str, Any]]
    super_class: _JavaClass | None = None
    handle: int = 0


@dataclass
class _JavaObject:
    java_class: _JavaClass
    fields: dict[str, Any] = field(default_factory=dict)
    custom_data: list[Any] = field(default_factory=list)
    handle: int = 0


class _JavaReader:
    """Enough of ObjectInputStream's protocol for REW's MeasData graph."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.handles: list[Any] = []

    def _need(self, n: int) -> None:
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("truncated Java serialization stream")

    def u8(self) -> int:
        self._need(1)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        self._need(2)
        value = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def i32(self) -> int:
        self._need(4)
        value = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u32(self) -> int:
        self._need(4)
        value = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def i64(self) -> int:
        self._need(8)
        value = struct.unpack_from(">q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def raw(self, n: int) -> bytes:
        self._need(n)
        value = self.data[self.pos : self.pos + n]
        self.pos += n
        return value

    @staticmethod
    def _decode_modified_utf8(data: bytes) -> str:
        # Java's modified UTF-8 differs from UTF-8 for NUL and supplementary
        # characters.  REW metadata is normally ASCII, but decoding the full
        # form costs little and avoids surprising failures on a renamed file.
        chars: list[str] = []
        i = 0
        while i < len(data):
            b = data[i]
            if b <= 0x7F:
                chars.append(chr(b))
                i += 1
            elif (b & 0xE0) == 0xC0 and i + 1 < len(data):
                chars.append(chr(((b & 0x1F) << 6) | (data[i + 1] & 0x3F)))
                i += 2
            elif (b & 0xF0) == 0xE0 and i + 2 < len(data):
                chars.append(
                    chr(
                        ((b & 0x0F) << 12)
                        | ((data[i + 1] & 0x3F) << 6)
                        | (data[i + 2] & 0x3F)
                    )
                )
                i += 3
            else:
                raise ValueError("invalid modified UTF-8 in Java stream")
        # A supplementary code point is represented by two UTF-16 surrogate
        # code units in modified UTF-8.  Join those units for Python callers.
        text = "".join(chars)
        try:
            return text.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeError:
            return text

    def java_utf(self, long: bool = False) -> str:
        n = self.u32() if long else self.u16()
        return self._decode_modified_utf8(self.raw(n))

    def assign(self, value: Any) -> int:
        handle = BASE_HANDLE + len(self.handles)
        self.handles.append(value)
        return handle

    def by_handle(self, handle: int) -> Any:
        index = handle - BASE_HANDLE
        if index < 0 or index >= len(self.handles):
            raise ValueError(f"invalid Java stream handle {handle:#x}")
        return self.handles[index]

    def read_classdesc(self) -> _JavaClass | None:
        token = self.u8()
        if token == TC_NULL:
            return None
        if token == TC_REFERENCE:
            value = self.by_handle(self.u32())
            if not isinstance(value, _JavaClass):
                raise ValueError("Java class descriptor reference is not a class")
            return value
        if token == TC_PROXYCLASSDESC:
            raise ValueError("proxy class descriptors are not supported")
        if token != TC_CLASSDESC:
            raise ValueError(f"expected class descriptor at {self.pos - 1:#x}")

        name = self.java_utf()
        serial_uid = self.i64()
        flags = self.u8()
        field_count = self.u16()

        # ObjectInputStream assigns the class descriptor handle before reading
        # field type strings.  This ordering matters for later TC_REFERENCEs.
        desc = _JavaClass(name, serial_uid, flags, [])
        desc.handle = self.assign(desc)
        for _ in range(field_count):
            type_code = chr(self.u8())
            field_name = self.java_utf()
            class_name = self.read_any() if type_code in "L[" else None
            desc.fields.append((type_code, field_name, class_name))

        # Class annotations are a block terminated by TC_ENDBLOCKDATA.
        while True:
            value = self.read_any()
            if isinstance(value, _JavaToken) and value.kind == "end":
                break
        desc.super_class = self.read_classdesc()
        return desc

    def read_any(self) -> Any:
        token_pos = self.pos
        token = self.u8()
        if token == TC_NULL:
            return None
        if token == TC_REFERENCE:
            return _JavaRef(self.u32())
        if token == TC_STRING:
            value = self.java_utf()
            self.assign(value)
            return value
        if token == TC_LONGSTRING:
            value = self.java_utf(long=True)
            self.assign(value)
            return value
        if token == TC_CLASSDESC:
            self.pos -= 1
            return self.read_classdesc()
        if token == TC_OBJECT:
            desc = self.read_classdesc()
            if desc is None:
                raise ValueError("object with null class descriptor")
            obj = _JavaObject(desc)
            obj.handle = self.assign(obj)
            self.read_class_data(desc, obj)
            return obj
        if token == TC_ARRAY:
            desc = self.read_classdesc()
            if desc is None or not desc.name.startswith("["):
                raise ValueError("array without an array class descriptor")
            count = self.i32()
            if count < 0:
                raise ValueError("negative Java array length")
            result: list[Any] = []
            handle = self.assign(result)
            component = desc.name[1:]
            if component in ("B", "Z"):
                result.extend(self.raw(count))
            elif component == "C":
                result.extend(struct.unpack(f">{count}H", self.raw(2 * count)))
            elif component == "S":
                result.extend(struct.unpack(f">{count}h", self.raw(2 * count)))
            elif component == "I":
                result.extend(struct.unpack(f">{count}i", self.raw(4 * count)))
            elif component == "J":
                result.extend(struct.unpack(f">{count}q", self.raw(8 * count)))
            elif component == "F":
                result.extend(struct.unpack(f">{count}f", self.raw(4 * count)))
            elif component == "D":
                result.extend(struct.unpack(f">{count}d", self.raw(8 * count)))
            elif component.startswith(("L", "[")):
                result.extend(self.read_any() for _ in range(count))
            else:
                raise ValueError(f"unsupported Java array type {desc.name!r}")
            self.handles[handle - BASE_HANDLE] = result
            return result
        if token == TC_CLASS:
            desc = self.read_classdesc()
            self.assign(desc)
            return desc
        if token == TC_BLOCKDATA:
            count = self.u8()
            return _JavaToken("block", self.raw(count))
        if token == TC_BLOCKDATALONG:
            count = self.u32()
            return _JavaToken("block", self.raw(count))
        if token == TC_ENDBLOCKDATA:
            return _JavaToken("end")
        if token == TC_RESET:
            self.handles.clear()
            return _JavaToken("reset")
        if token == TC_ENUM:
            desc = self.read_classdesc()
            value = self.read_any()
            enum_value = (desc, value)
            self.assign(enum_value)
            return enum_value
        if token == TC_EXCEPTION:
            raise ValueError(f"exception in Java stream at {token_pos:#x}")
        raise ValueError(
            f"unsupported Java serialization token {token:#x} at {token_pos:#x}"
        )

    def read_class_data(self, desc: _JavaClass, obj: _JavaObject) -> None:
        if desc.super_class is not None:
            self.read_class_data(desc.super_class, obj)
        for type_code, name, _class_name in desc.fields:
            if type_code == "B":
                value = self.u8()
                value = value - 256 if value >= 128 else value
            elif type_code == "Z":
                value = bool(self.u8())
            elif type_code == "C":
                value = chr(self.u16())
            elif type_code == "S":
                value = struct.unpack(">h", self.raw(2))[0]
            elif type_code == "I":
                value = self.i32()
            elif type_code == "J":
                value = self.i64()
            elif type_code == "F":
                value = struct.unpack(">f", self.raw(4))[0]
            elif type_code == "D":
                value = struct.unpack(">d", self.raw(8))[0]
            elif type_code in "L[":
                value = self.read_any()
            else:
                raise ValueError(f"unsupported Java field type {type_code!r}")
            obj.fields[name] = value
        if desc.flags & (SC_WRITE_METHOD | SC_EXTERNALIZABLE):
            while True:
                value = self.read_any()
                if isinstance(value, _JavaToken) and value.kind == "end":
                    break
                obj.custom_data.append(value)

    def read_stream(self) -> list[Any]:
        if self.raw(2) != b"\xac\xed" or self.raw(2) != b"\x00\x05":
            raise ValueError("not a Java serialization stream")
        values: list[Any] = []
        while self.pos < len(self.data):
            values.append(self.read_any())
        return values


def _deref(value: Any, reader: _JavaReader) -> Any:
    seen: set[int] = set()
    while isinstance(value, _JavaRef):
        if value.handle in seen:
            raise ValueError("cyclic Java reference while resolving scalar")
        seen.add(value.handle)
        value = reader.by_handle(value.handle)
    if isinstance(value, _JavaObject) and "value" in value.fields:
        return _deref(value.fields["value"], reader)
    return value


def _java_value(
    obj: _JavaObject, name: str, reader: _JavaReader, default: Any = None
) -> Any:
    return _deref(obj.fields.get(name, default), reader)


def _plain(value: Any) -> Any:
    """A readable stand-in for a Java value: objects by class name, arrays by length."""
    if isinstance(value, _JavaObject):
        return f"<{value.java_class.name}>"
    if isinstance(value, _JavaClass):
        return f"<class {value.name}>"
    if isinstance(value, tuple) and value and isinstance(value[0], _JavaClass):
        return value[1]  # enum constant name
    if isinstance(value, list):
        return f"<array of {len(value)}>"
    return value


def read(data: bytes) -> list[Measurement]:
    """Return every ``roomeqwizard.MeasData`` in the stream, left channel first."""
    return [m for m, _ in read_detailed(data)]


def read_detailed(data: bytes) -> list[tuple[Measurement, dict[str, Any]]]:
    """Like ``read``; each measurement comes with its serialized MeasData fields.

    Objects are shown by class name and arrays by length.
    """
    reader = _JavaReader(data)
    roots = reader.read_stream()
    measurements: list[tuple[Measurement, dict[str, Any]]] = []
    for root in roots:
        if (
            not isinstance(root, _JavaObject)
            or root.java_class.name != "roomeqwizard.MeasData"
        ):
            continue
        spl = _deref(root.fields.get("splValues"), reader)
        gd = _deref(root.fields.get("gdValues"), reader)
        if not isinstance(spl, list) or not isinstance(gd, list) or not spl:
            raise ValueError("REW MeasData has no SPL/group-delay arrays")
        n = min(
            len(spl), len(gd), int(_java_value(root, "dataLength", reader, len(spl)))
        )
        if n < 2:
            raise ValueError("REW measurement contains fewer than two points")
        start = float(_java_value(root, "startFreq", reader, 0.0))
        step = float(_java_value(root, "freqStep", reader, 0.0))
        if not math.isfinite(start) or not math.isfinite(step) or step <= 0:
            raise ValueError("REW measurement has invalid frequency metadata")
        short_desc = str(_java_value(root, "shortDesc", reader, "Measurement"))
        channel = "Right" if short_desc[:1].upper() == "R" else "Left"
        source_ms = _java_value(root, "sourceFileDate", reader, 0)
        try:
            timestamp = _datetime.datetime.fromtimestamp(
                float(source_ms) / 1000.0, tz=_datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        except (TypeError, ValueError, OverflowError, OSError):
            timestamp = EPOCH
        frequencies = [start + i * step for i in range(n)]
        fields = {
            name: _plain(_deref(value, reader))
            for name, value in sorted(root.fields.items())
        }
        measurements.append(
            (
                Measurement(
                    channel=channel,
                    index=1 if channel == "Right" else 0,
                    frequencies=frequencies,
                    response=[float(x) for x in spl[:n]],
                    # REW stores group delay in milliseconds; SoundID stores it in
                    # seconds in AflPoint and PEQb.
                    group_delay=[float(x) / 1000.0 for x in gd[:n]],
                    timestamp=timestamp,
                    sample_rate=int(_java_value(root, "sampleRate", reader, 48000)),
                    name=short_desc,
                    source_file=str(_java_value(root, "sourceFileName", reader, "")),
                    source_format=str(
                        _java_value(root, "sourceFileFormat", reader, "")
                    ),
                ),
                fields,
            )
        )
    if not measurements:
        raise ValueError("no roomeqwizard.MeasData objects found")
    measurements.sort(key=lambda item: item[0].index)
    return measurements


def load(path) -> list[Measurement]:
    return read(Path(path).read_bytes())


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
# REW's own serialVersionUID of roomeqwizard.MeasData (REW 5.31.3).
MEASDATA_UID = -1234567890123456789
REW_VERSIONS = ((5, 31), (5, 40))
# REW's MeasData field sets.  Order is the serialized order (primitives, then
# objects, each by name).  "Lname" fields list their class; "[name" is float[].
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
    "impedanceCalType": [
        "LgroupColor:Ljava/awt/Color;",
        "LgroupName:Ljava/lang/String;",
        "LgroupNotes:Ljava/lang/String;",
        "LgroupUuid:Ljava/lang/String;",
    ],
    "inputVolume": ["Lindex3D:Ljava/lang/Double;"],
    "locked": ["Llabel3D:Ljava/lang/String;"],
    "rawPhaseValues": ["[rawGDValues"],
    "savedColor": [
        "LrtaLevelAdjustApplied:Ljava/lang/Boolean;",
        "LrtaOctaveFrac:Ljava/lang/Integer;",
    ],
    "shortDesc": ["LselectedOnOverlay:Ljava/lang/Boolean;"],
    "unwPhaseValues": ["Lunit3D:Ljava/lang/String;", "[unwPhaseRefPeak"],
    "versionSt": ["Luuid:Ljava/util/UUID;", "Luuid2:Lroomeqwizard/UUID;"],
}
_ARRAY_TYPES = {
    "rawSourceSet": "[[F",
    "sourceSet": "[[F",
    "steppedSineResults": "[Lroomeqwizard/SteppedSineDataPoint;",
}


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


def measdata_fields(rew: tuple[int, int]) -> list[tuple[str, str, str | None]]:
    new = rew >= (5, 40)
    primitives = _COMMON_PRIMITIVES.format(
        hpApplied="ZhpApplied " if new else "",
        isFsaf="ZisFsafFileMeasurement ZisGroupPlaceHolder " if new else "",
        overlaySelected="ZoverlaySelected " if new else "",
        rawGDOctaveFrac="DrawGDOctaveFrac " if new else "",
    )
    objects = _OBJECTS_531.split()
    if new:
        out = []
        for item in objects:
            name = item[1:].split(":")[0]
            out += _ADDED_540.get(name, [])
            out.append(item)
        objects = out
    return _parse_fields(primitives) + _parse_fields(" ".join(objects))


class _JavaWriter:
    """Enough of ObjectOutputStream for REW's measurement file."""

    def __init__(self):
        self.out = bytearray(b"\xac\xed\x00\x05")
        self.next_handle = BASE_HANDLE
        self.classes: dict[str, int] = {}
        self.strings: dict[str, int] = {}

    def _assign(self) -> int:
        self.next_handle += 1
        return self.next_handle - 1

    def string(self, value: str) -> None:
        if value in self.strings:
            self.out += bytes([TC_REFERENCE]) + struct.pack(">I", self.strings[value])
            return
        data = _modified_utf8(value)
        if len(data) > 0xFFFF:
            raise ValueError("string too long for a REW measurement file")
        self.out += bytes([TC_STRING]) + struct.pack(">H", len(data)) + data
        self.strings[value] = self._assign()

    def block(self, data: bytes) -> None:
        self.out += bytes([TC_BLOCKDATA, len(data)]) + data

    def null(self) -> None:
        self.out += bytes([TC_NULL])

    def classdesc(self, name, uid, flags, fields=(), super_class=None) -> None:
        if name in self.classes:
            self.out += bytes([TC_REFERENCE]) + struct.pack(">I", self.classes[name])
            return
        data = name.encode()
        self.out += bytes([TC_CLASSDESC]) + struct.pack(">H", len(data)) + data
        self.out += struct.pack(">qBH", uid, flags, len(fields))
        self.classes[name] = self._assign()
        for code, field_name, cls in fields:
            data = field_name.encode()
            self.out += code.encode() + struct.pack(">H", len(data)) + data
            if code in "L[":
                self.string(cls)
        self.out += bytes([TC_ENDBLOCKDATA])
        if super_class is None:
            self.null()
        else:
            self.classdesc(*super_class)

    def array(self, signature: str, uid: int, fmt: str, values) -> None:
        self.out += bytes([TC_ARRAY])
        self.classdesc(signature, uid, 0x02)
        self._assign()
        self.out += struct.pack(">i", len(values)) + struct.pack(
            f">{len(values)}{fmt}", *values
        )

    def float_array(self, values) -> None:
        self.array("[F", 836686056779680834, "f", values)

    def boxed(self, kind: str, value) -> None:
        number = ("java.lang.Number", -8742448824652078965, 0x02)
        desc = {
            "Boolean": (
                "java.lang.Boolean",
                -3665804199014368530,
                0x02,
                [("Z", "value", None)],
            ),
            "Integer": (
                "java.lang.Integer",
                1360826667806852920,
                0x02,
                [("I", "value", None)],
                number,
            ),
            "Double": (
                "java.lang.Double",
                -9172774392245257468,
                0x02,
                [("D", "value", None)],
                number,
            ),
        }[kind]
        self.out += bytes([TC_OBJECT])
        self.classdesc(*desc)
        self._assign()
        self.out += struct.pack(
            {"Boolean": ">?", "Integer": ">i", "Double": ">d"}[kind], value
        )

    def object(self, desc, values: dict) -> None:
        """A plain Serializable object; missing values are 0, false or null."""
        self.out += bytes([TC_OBJECT])
        self.classdesc(*desc)
        self._assign()
        for code, name, _cls in desc[3]:
            value = values.get(name)
            if code in "L[":
                if value is None:
                    self.null()
                else:
                    value(self)  # a callable writes the object
            else:
                fmt = {
                    "B": ">b",
                    "Z": ">?",
                    "C": ">H",
                    "S": ">h",
                    "I": ">i",
                    "J": ">q",
                    "F": ">f",
                    "D": ">d",
                }[code]
                self.out += struct.pack(fmt, value or 0)

    def image_icon(
        self, width: int, height: int, argb: int, rew: tuple[int, int]
    ) -> None:
        context = (
            "Lroomeqwizard/IconStub$_A;"
            if rew >= (5, 40)
            else "Ljavax/swing/ImageIcon$AccessibleImageIcon;"
        )
        fields = [
            ("I", "height", None),
            ("I", "width", None),
            ("L", "accessibleContext", context),
            ("L", "description", "Ljava/lang/String;"),
            ("L", "imageObserver", "Ljava/awt/image/ImageObserver;"),
        ]
        self.out += bytes([TC_OBJECT])
        self.classdesc(
            "javax.swing.ImageIcon", -962022720109015502, SC_WRITE_METHOD | 0x02, fields
        )
        self._assign()
        self.out += struct.pack(">ii", height, width) + bytes([TC_NULL] * 3)
        self.block(struct.pack(">ii", width, height))
        self.array("[I", 5600894804908749477, "i", [argb] * (width * height))
        self.out += bytes([TC_ENDBLOCKDATA])


def _modified_utf8(text: str) -> bytes:
    utf16 = text.encode("utf-16-be", "surrogatepass")
    out = bytearray()
    for unit in struct.unpack(f">{len(utf16) // 2}H", utf16):
        if 0 < unit <= 0x7F:
            out.append(unit)
        elif unit <= 0x7FF:
            out += bytes([0xC0 | unit >> 6, 0x80 | unit & 0x3F])
        else:
            out += bytes(
                [0xE0 | unit >> 12, 0x80 | unit >> 6 & 0x3F, 0x80 | unit & 0x3F]
            )
    return bytes(out)


def _f32(x: float) -> float:
    # Rounding first keeps the float32 values independent of libm last bits.
    return struct.unpack(">f", struct.pack(">f", round(x, 5)))[0]


def _linear_grid(m: Measurement) -> tuple[float, float]:
    """(start, step) of the measurement's frequencies; REW stores no other grid."""
    f = m.frequencies
    if len(f) < 2 or not len(f) == len(m.response) == len(m.group_delay):
        raise ValueError(f"{m.name}: needs two or more points with SPL and group delay")
    start, step = f[0], f[1] - f[0]
    if not (start > 0 and step > 0) or any(
        abs(x - (start + i * step)) > 1e-6 * step for i, x in enumerate(f)
    ):
        raise ValueError(f"{m.name}: frequencies are not a positive linear grid")
    return start, step


_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _date(timestamp: str) -> _datetime.datetime:
    try:
        return _datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=_datetime.timezone.utc
        )
    except ValueError:
        return _datetime.datetime.fromtimestamp(0, _datetime.timezone.utc)


def write(
    measurements: list[Measurement],
    phases: list[list[float]] | None = None,
    rew: tuple[int, int] = REW_VERSIONS[0],
) -> bytes:
    """REW measurement file; ``phases`` in degrees, default 0.

    Each measurement is on a linear frequency grid; its name is REW's short
    description, its group delay is in seconds.  Every other MeasData field
    is 0, false or null: no impulse response, no audio data.
    """
    if not measurements:
        raise ValueError("no measurement to write")
    if rew not in REW_VERSIONS:
        raise ValueError(
            f"REW version must be one of: "
            f"{', '.join(f'{a}.{b}' for a, b in REW_VERSIONS)}"
        )
    if phases is None:
        phases = [[0.0] * len(m.frequencies) for m in measurements]
    curves = []
    for m, phase in zip(measurements, phases, strict=True):
        start, step = _linear_grid(m)
        if len(phase) != len(m.frequencies):
            raise ValueError(
                f"{m.name}: {len(phase)} phase values, {len(m.frequencies)} points"
            )
        curves.append(
            (
                m,
                start,
                step,
                [_f32(v) for v in m.response],
                [_f32(v) for v in phase],
                [_f32(v * 1000) for v in m.group_delay],
            )
        )  # REW: ms

    w = _JavaWriter()
    w.string("REW Measurement Data File V2")
    w.block(struct.pack(">ii", *rew))
    w.string("Notes:")
    w.block(struct.pack(">i", len(curves)))
    for m, _start, _step, spl, _phase, _gd in curves:
        date = _date(m.timestamp)
        w.image_icon(130, 70, -12566464, rew)  # 0xFF404040
        w.string(
            '<HTML><style type="text/css">body { margin-left: 3; }</style><BODY>'
            f"{_MONTHS[date.month - 1]} {date.day}, {date.year}<BR>"
            f"{date.hour % 12 or 12}:{date.minute:02}:{date.second:02} "
            f"{'AM' if date.hour < 12 else 'PM'}<BR>"
            f"0 to {m.sample_rate // 2:,} Hz<BR>"
            f"{math.floor(min(spl))} to {math.ceil(max(spl))} dB SPL</HTML>"
        )
    desc = ("roomeqwizard.MeasData", MEASDATA_UID, 0x02, measdata_fields(rew))
    version = ".3" if rew < (5, 40) else " beta 1"
    for m, start, step, spl, phase, gd in curves:
        end = m.frequencies[-1]
        w.block(struct.pack(">i", 3))
        w.object(
            desc,
            {
                "dataLength": len(spl),
                "startFreq": start,
                "freqStep": step,
                "endFreq": end,
                "validStartFreq": start,
                "validEndFreq": end,
                "sampleRate": m.sample_rate,
                "numSweeps": 1,
                "sourceType": 5,
                "sourceFileDate": round(_date(m.timestamp).timestamp() * 1000),
                "impedanceCal": 1.0,
                "hfFallSlope": 0.5,
                "hfFallStart": 1000,
                "lfRiseEnd": 20,
                "lfRiseSlope": 1.0,
                "lfRiseStart": 200,
                "roomHeight": 2.4,
                "roomLength": 5.0,
                "roomWidth": 4.0,
                "speakerCutoff": 80,
                "xOverHPCutoff": 100,
                "xOverLPCutoff": 1000,
                "enable": lambda w: w.boxed("Boolean", True),
                "locked": lambda w: w.boxed("Boolean", False),
                "eqName": lambda w: w.string("Generic"),
                "measNotes": lambda w: w.string(""),
                "shortDesc": lambda w, s=m.name: w.string(s),
                "sourceFileFormat": lambda w, s=m.source_format: w.string(s),
                "sourceFileName": lambda w, s=m.source_file: w.string(s),
                "rewVersion": lambda w: w.boxed("Integer", rew[0]),
                "rewSubVersion": lambda w: w.boxed("Integer", rew[1]),
                "versionSt": lambda w: w.string(version),
                "splValues": lambda w, v=spl: w.float_array(v),
                "phaseValues": lambda w, v=phase: w.float_array(v),
                "gdValues": lambda w, v=gd: w.float_array(v),
            },
        )
    w.block(struct.pack(">i", 0))
    return bytes(w.out)


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class MdatInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        measurements = read_detailed(data)
        sections = [file_section(path, data, ("measurements", len(measurements)))]
        for i, (m, java) in enumerate(measurements):
            points = list(zip(m.frequencies, m.response, m.group_delay))
            sections.append(
                Section(
                    f"measurement [{i}] {m.name}",
                    [
                        ("channel", f"{m.channel} (index {m.index})"),
                        ("sample rate", m.sample_rate),
                        ("time", m.timestamp),
                        ("source file", m.source_file),
                        ("source format", m.source_format),
                        ("points", len(points)),
                        ("range", frequency_range(points)),
                    ],
                    Table(["frequency Hz", "SPL dB", "group delay s"], points),
                    raw="\n".join(f"{k} = {v}" for k, v in java.items()) or None,
                )
            )
        return sections


FORMAT = Format("mdat", (".mdat",), "REW measurement", MdatInspector)
