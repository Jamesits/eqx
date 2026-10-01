"""Reader and writer for Smaart curve files (``*.crv``).

Target curves: ``Key: value`` header lines, then ``frequency<TAB>dB`` rows::

    Line: 3                 line width, pixels; required
    Color: FF808080         ARGB, hex
    Show: 1
    Type: TF                transfer function curve; without it a spectrum curve
    Tolerance: 3            transfer function: shaded band, +-dB
    Offset: 0               transfer function: level offset, dB
    Band: 3                 spectrum: the curve's banding, 1/n octave

    100	55
    500	62

Smaart takes the name from the file name, skips lines that start with ``*``
or ``;``, splits rows at tabs and commas, ignores frequencies <= 0 and
repeated frequencies, and sorts the points.  A microphone correction curve
(``MicCorrectionCurves/*.crv``) is the imported text file: rows without a
header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..fileformat import Format, Inspector, file_section, frequency_range
from ..report import Section, Table

KEYS = ("Line", "Color", "Show", "Type", "Band", "Offset", "Tolerance", "UseTolerance")
BANDS = (1, 3, 6, 12, 24, 48)
# Written curves: line width and color as Smaart's documentation suggests.
LINE, COLOR = 2, "FF808080"
_KEY = re.compile(rf"^({'|'.join(KEYS)}):(.*)$")
_ROW = re.compile(r"[\t,]")


@dataclass
class Curve:
    name: str
    points: list[tuple[float, float]]
    header: dict[str, str] = field(default_factory=dict)   # KEYS -> value text
    other: list[str] = field(default_factory=list)         # comments and other text lines

    @property
    def kind(self) -> str:
        if not self.header:
            return "table"
        return "transfer function" if self.header.get("Type") == "TF" else "spectrum"

    def number(self, key: str) -> float:
        """A header value as Smaart reads it; 0 if absent or not a number."""
        match = re.match(r"[-+]?(?:\d+\.?\d*|\.\d+)", self.header.get(key, ""))
        return float(match[0]) if match else 0.0

    def problems(self) -> list[str]:
        """Why Smaart would reject the curve as a target curve."""
        out = []
        if len(self.points) < 2:
            out.append("fewer than two points")
        if not self.number("Line"):
            out.append("no Line width")
        if not re.fullmatch(r"\s*[0-9A-Fa-f]{1,8}\s*", self.header.get("Color", "")):
            out.append("no hex Color")
        if self.kind == "transfer function":
            if self.number("Band"):
                out.append("Band in a transfer function curve")
        else:
            if not self.number("Band"):
                out.append("no Band")
            if self.number("Offset") or self.number("Tolerance"):
                out.append("Offset or Tolerance in a spectrum curve")
        return out


def read(text: str, name: str = "") -> Curve:
    points, header, other, seen = [], {}, [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _KEY.match(line)
        if match:
            header[match[1]] = match[2].strip()
            continue
        cells = _ROW.split(line)
        try:
            f, v = float(cells[0]), float(cells[1])
        except (IndexError, ValueError):
            other.append(line)
            continue
        if f > 0 and f not in seen:
            seen.add(f)
            points.append((f, v))
    if len(points) < 2:
        raise ValueError(f"{name or 'file'}: fewer than two curve points")
    return Curve(name, sorted(points), header, other)


def load(path) -> Curve:
    path = Path(path)
    return read(path.read_bytes().decode("utf-8", errors="replace"), path.stem)


def _number(value: float) -> str:
    return format(value + 0.0, ".6g")       # + 0.0 turns -0.0 into 0.0


def write(points, band: int | None = None) -> str:
    """A target curve of (frequency, dB) ``points``: transfer function, or
    spectrum with ``band`` (1/n octave)."""
    if band is not None and band not in BANDS:
        raise ValueError(f"band must be one of: {', '.join(map(str, BANDS))}")
    lines = [f"Line: {LINE}", f"Color: {COLOR}", "Show: 1"]
    lines += [f"Band: {band}"] if band else ["Type: TF", "Tolerance: 0", "Offset: 0"]
    lines += [""] + [f"{_number(f)}\t{_number(v)}" for f, v in points]
    return "\r\n".join(lines) + "\r\n"


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class SmaartCurveInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        c = read(data.decode("utf-8", errors="replace"), path.stem)
        status = None
        if c.header:
            problems = c.problems()
            status = "valid" if not problems else f"rejected by Smaart: {'; '.join(problems)}"
        kind = c.kind if c.header else "table without header (microphone correction curve)"
        return [
            file_section(path, data, ("curve name", c.name), ("kind", kind), ("status", status),
                         *((key, value) for key, value in c.header.items()),
                         *(("text", line) for line in c.other)),
            Section("points", [("points", len(c.points)), ("range", frequency_range(c.points))],
                    Table(["frequency Hz", "dB"], c.points)),
        ]


FORMAT = Format("smaart-curve", (".crv",), "Smaart target / microphone correction curve",
                SmaartCurveInspector)
