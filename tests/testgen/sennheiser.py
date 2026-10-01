"""dearVR MIX headphone compensation file (``hpc.dat``)."""

from __future__ import annotations

import struct

from eqx import dsp
from eqx.sennheiser import hpc

from .common import bells, impulse_response

HPC_DIR = "sennheiser/dearvr-hpc"
TAPS = {"minimum": 2048, "linear": 2049}
# (id, name, {phase: rates}, channel bells): left, optional right.
HEADPHONES = [
    (
        0x3E22D390,
        "Tilt Studio",
        {"linear": (44100, 48000), "minimum": (44100, 48000)},
        ("Left",),
    ),
    (0x00000001, "Flat Flat", {"linear": (48000,), "minimum": (48000,)}, ()),
    (0xFC9A24C1, "Tilt Stereo", {"minimum": (48000,)}, ("Left", "Right")),
]


def impulse(sides: tuple, phase: str, rate: int) -> list[list[float]]:
    """One impulse response per side (one unit impulse without sides): the
    bells, minimum phase as filtered, linear phase designed from their gain."""
    out = []
    for side in sides or (None,):
        biquads = bells(side, rate) if side else []
        if phase == "minimum":
            out.append(impulse_response(TAPS[phase], 0, 0.0, biquads))
        else:
            grid = [20 * 1000 ** (i / 199) for i in range(200)]
            points = [(f, dsp.cascade_db(biquads, f, rate)) for f in grid]
            out.append(dsp.design_fir_points(points, rate, phase, TAPS[phase]))
    return out


# --------------------------------------------------------------------------
# FlatBuffers writer
# --------------------------------------------------------------------------
class _Builder:
    """Writes objects front to back: a table's children follow it, so every
    uoffset points forward.  Every scalar field takes a 4-byte slot."""

    def __init__(self):
        self.buf = bytearray(8)

    def _align(self) -> None:
        self.buf += bytes(-len(self.buf) % 4)

    def _patch(self, slot: int, target: int) -> None:
        struct.pack_into("<I", self.buf, slot, target - slot)

    def table(self, fields: list) -> int:
        """``fields``: (kind, value) or None (absent) per slot; kinds u8, u32,
        string, floats, tables (a list of field lists)."""
        self._align()
        offsets = [0 if f is None else 4 + 4 * i for i, f in enumerate(fields)]
        vtable = len(self.buf)
        self.buf += struct.pack(
            f"<HH{len(fields)}H", 4 + 2 * len(fields), 4 + 4 * len(fields), *offsets
        )
        self._align()
        table = len(self.buf)
        self.buf += struct.pack("<i", table - vtable) + bytes(4 * len(fields))
        for i, f in enumerate(fields):
            if f is None:
                continue
            kind, value = f
            slot = table + 4 + 4 * i
            if kind == "u8":
                self.buf[slot] = value
            elif kind == "u32":
                struct.pack_into("<I", self.buf, slot, value)
            else:
                self._patch(slot, self._vector(kind, value))
        return table

    def _vector(self, kind: str, value) -> int:
        self._align()
        start = len(self.buf)
        if kind == "string":
            data = value.encode()
            self.buf += struct.pack("<I", len(data)) + data + b"\0"
        elif kind == "floats":
            self.buf += struct.pack(f"<I{len(value)}f", len(value), *value)
        else:
            self.buf += struct.pack("<I", len(value)) + bytes(4 * len(value))
            for i, fields in enumerate(value):
                self._patch(start + 4 + 4 * i, self.table(fields))
        return start

    def finish(self, root: list, identifier: bytes) -> bytes:
        self.buf[4:8] = identifier
        self._patch(0, self.table(root))
        return bytes(self.buf)


def write_hpc(headphones=HEADPHONES) -> bytes:
    """The phase byte is left out for minimum phase, its default, as dearVR does."""
    tables = []
    for hid, name, phases, sides in headphones:
        groups = [
            [
                None if phase == "minimum" else ("u8", hpc.PHASES.index(phase)),
                (
                    "tables",
                    [
                        [
                            ("u32", rate),
                            (
                                "tables",
                                [
                                    [("floats", ir)]
                                    for ir in impulse(sides, phase, rate)
                                ],
                            ),
                        ]
                        for rate in rates
                    ],
                ),
            ]
            for phase, rates in phases.items()
        ]
        tables.append([("u32", hid), ("string", name), ("tables", groups)])
    return _Builder().finish([("u32", 1), ("tables", tables)], hpc.IDENTIFIER)


def files() -> dict[str, bytes]:
    return {f"{HPC_DIR}/hpc.dat": write_hpc()}
