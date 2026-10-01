"""Minimal schema-driven Protocol Buffers (proto3) codec.

A schema maps a message name to its fields: ``{name: (number, type, label)}``.
``type`` is a scalar type name or another message name; ``label`` is ``""``,
``"repeated"`` or ``"map:<key type>"``.  Decoded messages are dicts holding
the fields present in the data; repeated fields are lists, maps are dicts.
Unknown fields are skipped.  The encoder writes fields in field-number order,
packs repeated scalars and leaves out proto3 default values, as protobuf's
C++ serializer does.
"""

from __future__ import annotations

import struct
from typing import Any

Schema = dict[str, dict[str, tuple[int, str, str]]]

VARINT_TYPES = ("int32", "int64", "uint32", "uint64", "bool", "enum", "sint32", "sint64")
FIXED32 = {"float": "<f", "fixed32": "<I", "sfixed32": "<i"}
FIXED64 = {"double": "<d", "fixed64": "<Q", "sfixed64": "<q"}
SCALARS = (*VARINT_TYPES, *FIXED32, *FIXED64, "string", "bytes")
DEFAULTS = {"string": "", "bytes": b"", "bool": False}


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _encode_varint(value: int) -> bytes:
    value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _from_varint(kind: str, value: int):
    if kind == "bool":
        return value != 0
    if kind in ("sint32", "sint64"):
        return (value >> 1) ^ -(value & 1)
    if kind in ("int32", "enum"):
        value &= 0xFFFFFFFF
        return value - (1 << 32) if value >= 1 << 31 else value
    if kind == "int64":
        return value - (1 << 64) if value >= 1 << 63 else value
    return value


def _to_varint(kind: str, value) -> int:
    if kind in ("sint32", "sint64"):
        return (value << 1) ^ (value >> 63)
    return int(value)


def _wire_type(kind: str) -> int:
    if kind in VARINT_TYPES:
        return 0
    if kind in FIXED64:
        return 1
    if kind in FIXED32:
        return 5
    return 2


def _map_entry(label: str, kind: str) -> dict[str, tuple[int, str, str]]:
    return {"key": (1, label[4:], ""), "value": (2, kind, "")}


def decode(schema: Schema, name: str, data: bytes) -> dict[str, Any]:
    return _decode(schema, schema[name], data)


def _decode(schema: Schema, fields, data: bytes) -> dict[str, Any]:
    by_number = {number: (field, kind, label) for field, (number, kind, label) in fields.items()}
    out: dict[str, Any] = {}
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            raw, pos = _varint(data, pos)
        elif wire == 1:
            raw, pos = data[pos:pos + 8], pos + 8
        elif wire == 5:
            raw, pos = data[pos:pos + 4], pos + 4
        elif wire == 2:
            size, pos = _varint(data, pos)
            raw, pos = data[pos:pos + size], pos + size
        else:
            raise ValueError(f"unsupported wire type {wire}")
        if pos > len(data):
            raise ValueError("truncated message")
        if number not in by_number:
            continue
        field, kind, label = by_number[number]
        if label.startswith("map:"):
            entry = _decode(schema, _map_entry(label, kind), raw)
            value_default = {} if kind not in SCALARS else DEFAULTS.get(kind, 0)
            out.setdefault(field, {})[entry.get("key", DEFAULTS.get(label[4:], 0))] = \
                entry.get("value", value_default)
            continue
        if label == "repeated" and wire == 2 and kind in SCALARS and kind not in ("string",
                                                                                   "bytes"):
            out.setdefault(field, []).extend(_packed(kind, raw))
            continue
        value = _scalar(kind, wire, raw) if kind in SCALARS else _decode(schema, schema[kind], raw)
        if label == "repeated":
            out.setdefault(field, []).append(value)
        elif kind not in SCALARS and field in out:
            out[field] = _merge(out[field], value)     # a repeated message field merges
        else:
            out[field] = value
    return out


def _merge(old: dict, new: dict) -> dict:
    merged = dict(old)
    for k, v in new.items():
        if isinstance(v, list):
            merged[k] = merged.get(k, []) + v
        elif isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _scalar(kind: str, wire: int, raw):
    if kind in VARINT_TYPES:
        if wire != 0:
            raise ValueError(f"{kind}: wire type {wire}")
        return _from_varint(kind, raw)
    if kind in FIXED32:
        return struct.unpack(FIXED32[kind], raw)[0]
    if kind in FIXED64:
        return struct.unpack(FIXED64[kind], raw)[0]
    if kind == "string":
        return raw.decode("utf-8")
    return bytes(raw)


def _packed(kind: str, raw: bytes) -> list:
    if kind in FIXED32:
        fmt = FIXED32[kind]
        return list(struct.unpack(f"<{len(raw) // 4}{fmt[1]}", raw))
    if kind in FIXED64:
        fmt = FIXED64[kind]
        return list(struct.unpack(f"<{len(raw) // 8}{fmt[1]}", raw))
    values, pos = [], 0
    while pos < len(raw):
        value, pos = _varint(raw, pos)
        values.append(_from_varint(kind, value))
    return values


def encode(schema: Schema, name: str, message: dict[str, Any]) -> bytes:
    return _encode(schema, schema[name], message)


def _encode(schema: Schema, fields, message: dict[str, Any]) -> bytes:
    unknown = set(message) - set(fields)
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
    out = bytearray()
    for field, (number, kind, label) in sorted(fields.items(), key=lambda item: item[1][0]):
        if field not in message:
            continue
        value = message[field]
        if label.startswith("map:"):
            # Map entries always hold both the key and the value.
            for k, v in value.items():
                body = _field(schema, 1, label[4:], k) + _field(schema, 2, kind, v)
                out += _encode_varint(number << 3 | 2) + _encode_varint(len(body)) + body
        elif label == "repeated":
            if kind in SCALARS and kind not in ("string", "bytes"):
                if value:
                    body = b"".join(_scalar_bytes(kind, v) for v in value)
                    out += _encode_varint(number << 3 | 2) + _encode_varint(len(body)) + body
            else:
                for v in value:
                    out += _field(schema, number, kind, v)
        elif kind in SCALARS and _is_default(kind, value):
            continue
        else:
            out += _field(schema, number, kind, value)
    return bytes(out)


def _is_default(kind: str, value) -> bool:
    """proto3 leaves out default values; -0.0 is not one."""
    if kind in ("float", "double"):
        return value == 0 and str(float(value))[0] != "-"
    return value == DEFAULTS.get(kind, 0)


def _field(schema: Schema, number: int, kind: str, value) -> bytes:
    if kind not in SCALARS:
        body = _encode(schema, schema[kind], value)
        return _encode_varint(number << 3 | 2) + _encode_varint(len(body)) + body
    if kind in ("string", "bytes"):
        body = value.encode("utf-8") if kind == "string" else bytes(value)
        return _encode_varint(number << 3 | 2) + _encode_varint(len(body)) + body
    return _encode_varint(number << 3 | _wire_type(kind)) + _scalar_bytes(kind, value)


def _scalar_bytes(kind: str, value) -> bytes:
    if kind in FIXED32:
        return struct.pack(FIXED32[kind], value)
    if kind in FIXED64:
        return struct.pack(FIXED64[kind], value)
    return _encode_varint(_to_varint(kind, value))
