"""Apple ``NSKeyedArchiver`` archives.

An archive is a binary property list::

    $archiver   "NSKeyedArchiver"
    $version    100000
    $top        root keys -> values
    $objects    "$null", then every archived object; objects refer to each
                other by UID (index into $objects)

An object is a dict with ``$class`` (UID of a ``{$classname, $classes}``
dict) and its keyed values.  ``encodeObject:`` stores a UID; ``encodeInt:``,
``encodeFloat:``, ``encodeBool:`` and ``encodeBytes:`` store the value in the
object itself.

Reading resolves every UID.  Foundation collections and values become
Python ones (list, dict, str, bytes, ``datetime``); objects of other classes
become ``Instance``.  Writing takes the same shapes; ``Ref`` stores a number
or bytes as an object (``NSNumber``, ``NSData``) instead of in place.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

ARCHIVER = "NSKeyedArchiver"
VERSION = 100000
# NSDate counts seconds from 2001-01-01 UTC.
REFERENCE_DATE = datetime(2001, 1, 1, tzinfo=timezone.utc)

_LISTS = (
    "NSArray",
    "NSMutableArray",
    "NSSet",
    "NSMutableSet",
    "NSOrderedSet",
    "NSMutableOrderedSet",
)
_DICTS = ("NSDictionary", "NSMutableDictionary")
_STRINGS = ("NSString", "NSMutableString")
_DATA = ("NSData", "NSMutableData")
_DATES = ("NSDate",)


@dataclass
class Instance:
    """An archived object of a class that is not a Foundation collection or value."""

    classname: str
    fields: dict[str, Any] = field(default_factory=dict)
    classes: list[str] | None = None  # class chain; None: [classname, "NSObject"]

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)


@dataclass(frozen=True)
class Ref:
    """A number or bytes stored as an object (``NSNumber``, ``NSData``)."""

    value: Any


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def unarchive(data: bytes) -> dict[str, Any]:
    """The resolved ``$top`` dict."""
    try:
        plist = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, OverflowError) as exc:
        raise ValueError(f"not a keyed archive: {exc}") from None
    if (
        not isinstance(plist, dict)
        or plist.get("$archiver") != ARCHIVER
        or not isinstance(plist.get("$objects"), list)
        or not isinstance(plist.get("$top"), dict)
    ):
        raise ValueError("not a keyed archive")
    return _Reader(plist["$objects"]).fields(plist["$top"])


class _Reader:
    def __init__(self, objects: list):
        self.objects = objects
        self.done: dict[int, Any] = {}

    def value(self, v: Any) -> Any:
        return self.object(v.data) if isinstance(v, plistlib.UID) else v

    def fields(self, d: dict) -> dict[str, Any]:
        return {k: self.value(v) for k, v in d.items() if k != "$class"}

    def object(self, uid: int) -> Any:
        if uid in self.done:
            return self.done[uid]
        if not 0 <= uid < len(self.objects):
            raise ValueError(f"keyed archive: object reference {uid} out of range")
        raw = self.objects[uid]
        if uid == 0 or raw == "$null":
            return None
        if not isinstance(raw, dict) or "$class" not in raw:
            self.done[uid] = raw
            return raw
        cls = self.value(raw["$class"])
        name = cls.get("$classname") if isinstance(cls, dict) else None
        if not isinstance(name, str):
            raise ValueError(f"keyed archive: object {uid} has no class name")  # noqa: TRY004
        # Store containers before filling them: objects may refer back.
        if name in _LISTS:
            out = self.done[uid] = []
            out.extend(self.value(v) for v in raw.get("NS.objects", []))
            return out
        if name in _DICTS:
            out = self.done[uid] = {}
            for k, v in zip(raw.get("NS.keys", []), raw.get("NS.objects", [])):
                out[self.value(k)] = self.value(v)
            return out
        if name in _STRINGS:
            out = raw.get("NS.string", "")
        elif name in _DATA:
            out = raw.get("NS.bytes", b"")
        elif name in _DATES:
            out = REFERENCE_DATE + timedelta(seconds=raw.get("NS.time", 0.0))
        else:
            out = self.done[uid] = Instance(
                name, {}, list(cls.get("$classes") or [name])
            )
            out.fields.update(self.fields(raw))
            return out
        self.done[uid] = out
        return out


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def archive(top: dict[str, Any]) -> bytes:
    """A binary keyed archive with the root keys ``top``.

    Field values: ``str``, list, dict, ``datetime``, ``Instance``, ``Ref`` and
    None are objects; int, float, bool and bytes are stored in place.
    Collection members are always objects.  Lists and dicts are archived
    as the mutable classes, which decode wherever the immutable ones do.
    """
    writer = _Writer()
    root = writer.fields(top)
    return plistlib.dumps(
        {
            "$archiver": ARCHIVER,
            "$objects": writer.objects,
            "$top": root,
            "$version": VERSION,
        },
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )


class _Writer:
    def __init__(self):
        self.objects: list[Any] = ["$null"]
        self.classes: dict[tuple, plistlib.UID] = {}

    def fields(self, d: dict[str, Any]) -> dict[str, Any]:
        return {
            k: (v if isinstance(v, (bool, int, float, bytes)) else self.ref(v))
            for k, v in d.items()
        }

    def add(self, obj: Any) -> plistlib.UID:
        self.objects.append(obj)
        return plistlib.UID(len(self.objects) - 1)

    def cls(self, *chain: str) -> plistlib.UID:
        if chain not in self.classes:
            self.classes[chain] = self.add(
                {"$classes": list(chain), "$classname": chain[0]}
            )
        return self.classes[chain]

    def ref(self, v: Any) -> plistlib.UID:
        if v is None:
            return plistlib.UID(0)
        if isinstance(v, Ref):
            v = v.value
            if not isinstance(v, (bool, int, float, bytes)):
                raise TypeError(f"Ref holds a number or bytes, not {type(v).__name__}")
        # Collection members and Ref values are objects.
        if isinstance(v, (str, bool, int, float, bytes)):
            return self.add(v)
        # Reserve the slot first, so the object comes before its members.
        uid = self.add(None)
        if isinstance(v, (list, tuple)):
            obj = {
                "NS.objects": [self.ref(x) for x in v],
                "$class": self.cls("NSMutableArray", "NSArray", "NSObject"),
            }
        elif isinstance(v, dict):
            obj = {
                "NS.keys": [self.ref(k) for k in v],
                "NS.objects": [self.ref(x) for x in v.values()],
                "$class": self.cls("NSMutableDictionary", "NSDictionary", "NSObject"),
            }
        elif isinstance(v, datetime):
            obj = {
                "NS.time": (v - REFERENCE_DATE).total_seconds(),
                "$class": self.cls("NSDate", "NSObject"),
            }
        elif isinstance(v, Instance):
            obj = self.fields(v.fields)
            obj["$class"] = self.cls(*(v.classes or (v.classname, "NSObject")))
        else:
            raise TypeError(f"cannot archive a {type(v).__name__}")
        self.objects[uid.data] = obj
        return uid


# --------------------------------------------------------------------------
# description
# --------------------------------------------------------------------------
def describe(value: Any, indent: str = "", _open: frozenset = frozenset()) -> str:
    """Readable text of resolved values; bytes are shown by their size."""
    inner = indent + "  "
    if isinstance(value, (Instance, dict, list)):
        if id(value) in _open:
            return "<cycle>"
        _open = _open | {id(value)}
    if isinstance(value, Instance):
        if not value.fields:
            return f"{value.classname} {{}}"
        body = "\n".join(
            f"{inner}{k}: {describe(v, inner, _open)}" for k, v in value.fields.items()
        )
        return f"{value.classname} {{\n{body}\n{indent}}}"
    if isinstance(value, dict):
        if not value:
            return "{}"
        body = "\n".join(
            f"{inner}{k}: {describe(v, inner, _open)}" for k, v in value.items()
        )
        return f"{{\n{body}\n{indent}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return (
            "[\n"
            + "\n".join(f"{inner}{describe(v, inner, _open)}" for v in value)
            + f"\n{indent}]"
        )
    if isinstance(value, bytes):
        return f"<{len(value):,} bytes>"
    if isinstance(value, datetime):
        return value.isoformat()
    return repr(value)
