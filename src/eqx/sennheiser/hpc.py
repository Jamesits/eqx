"""Reader for the dearVR MIX headphone compensation filters (``hpc.dat``).

A FlatBuffers buffer, file identifier ``HPIR``.  Inferred schema (names are
eqx's; FlatBuffers stores no names)::

    table Library   { version: uint; headphones: [Headphone]; }
    table Headphone { id: uint; name: string; filters: [Filters]; }
    table Filters   { phase: ubyte;          // 0 minimum, 1 linear
                      rates: [Rate]; }
    table Rate      { sample_rate: uint; channels: [Channel]; }
    table Channel   { samples: [float]; }

Each impulse response is the correction filter itself; the plug-in's gain
trim and shelving filters are applied separately.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .. import impulse
from ..fileformat import Format, Inspector, file_section
from ..options import Option
from ..report import Section, Table
from ..wav import fir

IDENTIFIER = b"HPIR"
# Phase byte -> name; FlatBuffers leaves out the default, 0.
PHASES = ("minimum", "linear")

HEADPHONE_OPTION = Option(
    "--headphone",
    help="headphone model, case-insensitive, as listed by inspect --full, "
    "e.g. 'Sennheiser HD 600'",
)


@dataclass
class Filter:
    phase: str  # minimum, linear, or "type <n>"
    sample_rate: int
    taps: int
    _data: bytes = field(repr=False)
    _positions: list[int] = field(repr=False)  # start of each channel's floats

    @property
    def channel_count(self) -> int:
        return len(self._positions)

    def channels(self) -> list[list[float]]:
        """One impulse response per channel."""
        return [
            list(struct.unpack_from(f"<{self.taps}f", self._data, p))
            for p in self._positions
        ]


@dataclass
class Headphone:
    id: int
    name: str
    filters: list[Filter]

    @property
    def phases(self) -> list[str]:
        known = {p: i for i, p in enumerate(PHASES)}
        return sorted(
            {f.phase for f in self.filters}, key=lambda p: (known.get(p, len(known)), p)
        )

    @property
    def rates(self) -> list[int]:
        return sorted({f.sample_rate for f in self.filters})

    def filter(self, phase: str, rate: float) -> Filter:
        for f in self.filters:
            if f.phase == phase and f.sample_rate == rate:
                return f
        if phase not in self.phases:
            raise ValueError(
                f"{self.name}: no {phase} phase filter; "
                f"available: {', '.join(self.phases)}"
            )
        raise ValueError(
            f"{self.name}: no {phase} phase filter at {rate:g} Hz; available: "
            f"{', '.join(str(r) for r in self.rates)}"
        )


@dataclass
class Library:
    version: int
    headphones: list[Headphone]

    def headphone(self, name: str | None) -> Headphone:
        """The headphone named ``name``, case-insensitive."""
        names = ", ".join(sorted((h.name for h in self.headphones), key=str.lower))
        if not name:
            raise ValueError(f"specify --headphone, one of: {names}")
        for h in self.headphones:
            if h.name.lower() == name.strip().lower():
                return h
        raise ValueError(f"no headphone {name!r}; available: {names}")


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
class _Buffer:
    """Bounds-checked FlatBuffers access."""

    def __init__(self, data: bytes, name: str):
        self.data, self.name = data, name

    def _unpack(self, fmt: str, pos: int):
        if pos < 0 or pos + struct.calcsize(fmt) > len(self.data):
            raise ValueError(f"{self.name}: offset {pos} outside the file")
        return struct.unpack_from(fmt, self.data, pos)[0]

    def u8(self, pos: int) -> int:
        return self._unpack("<B", pos)

    def u32(self, pos: int) -> int:
        return self._unpack("<I", pos)

    def target(self, pos: int) -> int:
        """The object a uoffset at ``pos`` points to."""
        return pos + self.u32(pos)

    def fields(self, table: int) -> list[int]:
        """Absolute position of each field of ``table``; 0 when absent."""
        vtable = table - self._unpack("<i", table)
        size = self._unpack("<H", vtable)
        offsets = [
            self._unpack("<H", vtable + 4 + 2 * i) for i in range(max(0, size - 4) // 2)
        ]
        return [table + o if o else 0 for o in offsets]

    def field(self, table: int, index: int) -> int:
        fields = self.fields(table)
        return fields[index] if index < len(fields) else 0

    def vector(self, pos: int, width: int = 4) -> tuple[int, int]:
        """(start, length) of the vector a field at ``pos`` points to; empty if absent."""
        if not pos:
            return 0, 0
        start = self.target(pos)
        count = self.u32(start)
        if start + 4 + count * width > len(self.data):
            raise ValueError(f"{self.name}: vector at {start} outside the file")
        return start + 4, count

    def tables(self, pos: int) -> list[int]:
        start, count = self.vector(pos)
        return [self.target(start + 4 * i) for i in range(count)]

    def string(self, pos: int) -> str:
        start, count = self.vector(pos, 1)
        return self.data[start : start + count].decode("utf-8", errors="replace")


def read(data: bytes, name: str = "hpc.dat") -> Library:
    if len(data) < 8 or data[4:8] != IDENTIFIER:
        raise ValueError(
            f"{name} is not a dearVR headphone compensation file "
            f"(no {IDENTIFIER.decode()} identifier)"
        )
    b = _Buffer(data, name)
    root = b.target(0)
    version = b.field(root, 0)
    headphones = []
    for h in b.tables(b.field(root, 1)):
        hid = b.field(h, 0)
        filters = []
        for group in b.tables(b.field(h, 2)):
            kind = b.field(group, 0)
            kind = b.u8(kind) if kind else 0
            phase = PHASES[kind] if kind < len(PHASES) else f"type {kind}"
            for r in b.tables(b.field(group, 1)):
                rate = b.field(r, 0)
                positions, taps = [], set()
                for channel in b.tables(b.field(r, 1)):
                    start, count = b.vector(b.field(channel, 0))
                    positions.append(start)
                    taps.add(count)
                if len(taps) > 1:
                    raise ValueError(f"{name}: channels of different lengths")
                filters.append(
                    Filter(
                        phase,
                        b.u32(rate) if rate else 0,
                        taps.pop() if taps else 0,
                        data,
                        positions,
                    )
                )
        headphones.append(
            Headphone(b.u32(hid) if hid else 0, b.string(b.field(h, 1)), filters)
        )
    return Library(b.u32(version) if version else 0, headphones)


def load(path) -> Library:
    path = Path(path)
    return read(path.read_bytes(), path.name)


# --------------------------------------------------------------------------
# response
# --------------------------------------------------------------------------
def as_fir(f: Filter) -> fir.Fir:
    return fir.Fir(float(f.sample_rate), f.channels())


def peak(f: Filter) -> int:
    """The peak sample of the first channel: the latency."""
    channels = f.channels()
    return impulse.peak_index(channels[0]) if channels and f.taps else 0


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
def _rates(rates: list[int]) -> str:
    return ", ".join(f"{r / 1000:g}" for r in rates) + " kHz"


class HpcInspector(Inspector):
    options = (
        HEADPHONE_OPTION,
        Option("--rate", type=float, help="sample rate, Hz (default: 48000)"),
    )

    def __init__(self, headphone: str | None = None, rate: float = 48000.0):
        self.headphone, self.rate = headphone, rate

    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        library = read(data, path.name)
        rows = [
            (f"{h.id:08x}", h.name, ", ".join(h.phases), _rates(h.rates))
            for h in sorted(library.headphones, key=lambda h: h.name.lower())
        ]
        sections = [
            file_section(path, data),
            Section(
                "library",
                [
                    ("identifier", IDENTIFIER.decode()),
                    ("version", library.version),
                    ("headphones", len(library.headphones)),
                ],
            ),
            Section(
                "headphones", [], Table(["id", "name", "phases", "sample rates"], rows)
            ),
        ]
        if self.headphone is not None:
            sections.append(self._headphone(library.headphone(self.headphone)))
        return sections

    def _headphone(self, h: Headphone) -> Section:
        fields: list = [("id", f"{h.id:08x}"), ("name", h.name)]
        for phase in h.phases:
            for f in sorted(
                (f for f in h.filters if f.phase == phase), key=lambda f: f.sample_rate
            ):
                fields.append(
                    (
                        f"{phase} {f.sample_rate} Hz",
                        (
                            f"{f.taps} taps, {f.channel_count} channel(s), peak sample "
                            f"{peak(f)} ({1000 * peak(f) / f.sample_rate:.2f} ms)"
                        ),
                    )
                )
        columns, gains = ["frequency Hz"], []
        frequencies = fir.grid(self.rate)
        for phase in h.phases:
            f = h.filter(phase, self.rate)
            columns.append(f"{phase} dB")
            gains.append([g for _, g in fir.response(as_fir(f), 0, frequencies)])
        band = [
            g
            for gain in gains
            for fr, g in zip(frequencies, gain)
            if fir.BAND_HZ[0] <= fr <= fir.BAND_HZ[1]
        ] or [math.nan]
        fields += [
            ("gain at", f"{self.rate:g} Hz, first channel"),
            ("gain min dB", min(band)),
            ("gain max dB", max(band)),
        ]
        return Section(
            f"headphone {h.name}",
            fields,
            Table(columns, [tuple(row) for row in zip(frequencies, *gains)]),
        )


def _sniff(data: bytes) -> bool:
    return data[4:8] == IDENTIFIER


FORMAT = Format(
    "dearvr-hpc",
    (".dat",),
    "dearVR MIX headphone compensation filters (hpc.dat)",
    HpcInspector,
    _sniff,
)
