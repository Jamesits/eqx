"""Reader for Room EQ Wizard measurement files (``*.mdat``).

An ``.mdat`` file is Java Object Serialization data.  This module contains a
small read-only stream parser so that no Java or REW installation is needed.
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
                result.extend(struct.unpack(">%dH" % count, self.raw(2 * count)))
            elif component == "S":
                result.extend(struct.unpack(">%dh" % count, self.raw(2 * count)))
            elif component == "I":
                result.extend(struct.unpack(">%di" % count, self.raw(4 * count)))
            elif component == "J":
                result.extend(struct.unpack(">%dq" % count, self.raw(8 * count)))
            elif component == "F":
                result.extend(struct.unpack(">%df" % count, self.raw(4 * count)))
            elif component == "D":
                result.extend(struct.unpack(">%dd" % count, self.raw(8 * count)))
            elif component.startswith("L") or component.startswith("["):
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
        raise ValueError(f"unsupported Java serialization token {token:#x} at {token_pos:#x}")

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
        if self.raw(2) != b"\xAC\xED" or self.raw(2) != b"\x00\x05":
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


def _java_value(obj: _JavaObject, name: str, reader: _JavaReader, default: Any = None) -> Any:
    return _deref(obj.fields.get(name, default), reader)


def _plain(value: Any) -> Any:
    """A readable stand-in for a Java value: objects by class name, arrays by length."""
    if isinstance(value, _JavaObject):
        return f"<{value.java_class.name}>"
    if isinstance(value, _JavaClass):
        return f"<class {value.name}>"
    if isinstance(value, tuple) and value and isinstance(value[0], _JavaClass):
        return value[1]                                 # enum constant name
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
        if not isinstance(root, _JavaObject) or root.java_class.name != "roomeqwizard.MeasData":
            continue
        spl = _deref(root.fields.get("splValues"), reader)
        gd = _deref(root.fields.get("gdValues"), reader)
        if not isinstance(spl, list) or not isinstance(gd, list) or not spl:
            raise ValueError("REW MeasData has no SPL/group-delay arrays")
        n = min(len(spl), len(gd), int(_java_value(root, "dataLength", reader, len(spl))))
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
        fields = {name: _plain(_deref(value, reader))
                  for name, value in sorted(root.fields.items())}
        measurements.append((
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
                source_format=str(_java_value(root, "sourceFileFormat", reader, "")),
            ),
            fields,
        ))
    if not measurements:
        raise ValueError("no roomeqwizard.MeasData objects found")
    measurements.sort(key=lambda item: item[0].index)
    return measurements


def load(path) -> list[Measurement]:
    return read(Path(path).read_bytes())


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
            sections.append(Section(
                f"measurement [{i}] {m.name}",
                [("channel", f"{m.channel} (index {m.index})"),
                 ("sample rate", m.sample_rate),
                 ("time", m.timestamp),
                 ("source file", m.source_file),
                 ("source format", m.source_format),
                 ("points", len(points)),
                 ("range", frequency_range(points))],
                Table(["frequency Hz", "SPL dB", "group delay s"], points),
                raw="\n".join(f"{k} = {v}" for k, v in java.items()) or None,
            ))
        return sections


FORMAT = Format("mdat", (".mdat",), "REW measurement", MdatInspector)
