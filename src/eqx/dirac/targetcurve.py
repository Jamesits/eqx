"""Reader and writer for Dirac Live target curves (``*.targetcurve``).

UTF-8 text, one value per line::

    NAME
    My target
    DEVICENAME
    Dirac Live Processor
    BREAKPOINTS
    20 3
    1000 0
    20000 -3
    LOWLIMITHZ
    20
    HIGHLIMITHZ
    20000

Breakpoints are ``frequency gain`` pairs (Hz, dB) separated by spaces.  The
reader follows Dirac Live's own: it ignores NAME and DEVICENAME, takes the
breakpoints from the line after ``BREAKPOINTS`` up to the first line that is
not two numbers, then the limits after ``LOWLIMITHZ`` and ``HIGHLIMITHZ``.
Header lines match exactly, without trimming.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..curve import log_resample
from ..fileformat import Format, Inspector, file_section, frequency_range
from ..report import Section, Table

EXTENSION = ".targetcurve"
# Dirac Live's limits when the file has none.
DEFAULT_LOW_HZ, DEFAULT_HIGH_HZ = 10.0, 24000.0
# Dirac Live extends the breakpoints to these ends and clamps the high limit.
MIN_HZ, MAX_HZ = 0.0, 24000.0
NAME, DEVICE, BREAKPOINTS, LOW, HIGH = "NAME", "DEVICENAME", "BREAKPOINTS", "LOWLIMITHZ", \
    "HIGHLIMITHZ"


@dataclass
class TargetCurve:
    name: str = ""
    device_name: str = ""
    breakpoints: list[tuple[float, float]] = field(default_factory=list)   # (Hz, dB)
    low_hz: float = DEFAULT_LOW_HZ          # correction range
    high_hz: float = DEFAULT_HIGH_HZ

    def validate(self) -> None:
        """Dirac Live's checks when it loads the file."""
        frequencies = [f for f, _ in self.breakpoints]
        if not frequencies:
            raise ValueError("no breakpoints")
        if frequencies[0] < MIN_HZ:
            raise ValueError(f"negative frequency {frequencies[0]:g} Hz")
        for f0, f1 in zip(frequencies, frequencies[1:]):
            if not f0 < f1:
                raise ValueError(f"frequencies not increasing at {f1:g} Hz")
        if not MIN_HZ <= self.low_hz < min(self.high_hz, MAX_HZ):
            raise ValueError(f"limits {self.low_hz:g}-{self.high_hz:g} Hz: "
                             f"need {MIN_HZ:g} <= low < high")
        if not any(MIN_HZ < f < MAX_HZ for f in frequencies):
            raise ValueError(f"no breakpoint inside {MIN_HZ:g}-{MAX_HZ:g} Hz")

    def extended(self) -> list[tuple[float, float]]:
        """The breakpoints as Dirac Live loads them: extended to 0 Hz and 24 kHz
        with the end gains."""
        points = list(self.breakpoints)
        if points[0][0] > MIN_HZ:
            points.insert(0, (MIN_HZ, points[0][1]))
        if points[-1][0] < MAX_HZ:
            points.append((MAX_HZ, points[-1][1]))
        return points

    def response(self, frequencies) -> list[float]:
        """Gain at each frequency, dB: linear in log frequency between
        breakpoints, the end gain beyond them."""
        points = [(f, g) for f, g in self.breakpoints if f > 0]
        if not points:
            raise ValueError("no breakpoint above 0 Hz")
        return log_resample(points, frequencies)


def _float(text: str) -> float | None:
    """Qt's ``QString::toFloat``: surrounding whitespace allowed, None on failure."""
    try:
        value = float(text)
    except ValueError:
        return None
    # Out of float range fails in Qt.
    return value if not math.isfinite(value) or abs(value) <= 3.4028234663852886e38 else None


def _after(lines: list[str], i: int, key: str) -> int | None:
    """The index after the first line from ``i`` on that equals ``key``."""
    for j in range(i, len(lines)):
        if lines[j] == key:
            return j + 1
    return None


def read(text: str) -> TargetCurve:
    """The curve as written; ``validate`` applies Dirac Live's load checks."""
    text = text.lstrip("﻿")
    # QTextStream::readLine strips "\n" and "\r\n" only.
    lines = text.split("\n")
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    if lines and lines[-1] == "":
        lines.pop()
    curve = TargetCurve()
    i = _after(lines, 0, NAME)
    if i is not None and i < len(lines):
        curve.name = lines[i]
    i = _after(lines, 0, DEVICE)
    if i is not None and i < len(lines):
        curve.device_name = lines[i]
    i = _after(lines, 0, BREAKPOINTS)
    if i is None:
        raise ValueError(f"no {BREAKPOINTS} line: not a Dirac Live target curve")
    stop = None
    while i < len(lines):
        parts = [p for p in lines[i].split(" ") if p]
        values = [_float(p) for p in parts] if len(parts) == 2 else [None]
        if None in values:
            stop = i
            break
        curve.breakpoints.append((values[0], values[1]))
        i += 1
    # The search for the limits starts at the line that ended the breakpoints.
    j = None if stop is None else _after(lines, stop, LOW)
    if j is not None and j < len(lines):
        low = _float(lines[j])
        k = _after(lines, j + 1, HIGH) if low is not None else None
        high = _float(lines[k]) if k is not None and k < len(lines) else None
        if low is not None and high is not None:
            curve.low_hz, curve.high_hz = low, high
    return curve


def load(path) -> TargetCurve:
    return read(Path(path).read_bytes().decode("utf-8"))


def sniff(data: bytes) -> bool:
    return BREAKPOINTS.encode() in data[:4096]


def _number(value: float) -> str:
    """``QTextStream << float``: 6 significant digits."""
    text = f"{value:g}"
    return "0" if text == "-0" else text


def write(curve: TargetCurve) -> str:
    """The file as Dirac Live writes it, with LF line ends (Dirac Live on
    Windows writes CRLF; both read the same)."""
    curve.validate()
    for text in (curve.name, curve.device_name):
        if "\n" in text or "\r" in text:
            raise ValueError("a name cannot contain a line break")
    lines = [NAME, curve.name, DEVICE, curve.device_name, BREAKPOINTS]
    lines += [f"{_number(f)} {_number(g)}" for f, g in curve.breakpoints]
    lines += [LOW, _number(curve.low_hz), HIGH, _number(curve.high_hz)]
    return "\n".join(lines) + "\n"


def simplify(points: list[tuple[float, float]], tolerance_db: float) -> list[tuple[float, float]]:
    """The fewest points whose log-frequency interpolation stays within
    ``tolerance_db`` of ``points`` (Ramer-Douglas-Peucker on dB)."""
    if len(points) <= 2:
        return list(points)
    xs = [math.log(f) for f, _ in points]
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        worst, index = -1.0, None
        for i in range(a + 1, b):
            t = (xs[i] - xs[a]) / (xs[b] - xs[a])
            error = abs(points[a][1] + t * (points[b][1] - points[a][1]) - points[i][1])
            if error > worst:
                worst, index = error, i
        if index is not None and worst > tolerance_db:
            keep.add(index)
            stack += [(a, index), (index, b)]
    return [points[i] for i in sorted(keep)]


class TargetCurveInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        curve = read(data.decode("utf-8"))
        try:
            curve.validate()
            status = "valid"
        except ValueError as exc:
            status = f"rejected by Dirac Live: {exc}"
        return [
            file_section(path, data, ("curve name", curve.name), ("device", curve.device_name),
                         ("correction range", f"{curve.low_hz:g}-{curve.high_hz:g} Hz"),
                         ("status", status)),
            Section("breakpoints", [("points", len(curve.breakpoints)),
                                    ("frequency range", frequency_range(curve.breakpoints))],
                    Table(["frequency Hz", "gain dB"], curve.breakpoints)),
        ]


FORMAT = Format("targetcurve", (EXTENSION,), "Dirac Live target curve", TargetCurveInspector,
                sniff)
