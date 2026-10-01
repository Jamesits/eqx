"""Reader and writer for AutoEq frequency response CSV files (``*.csv``).

AutoEq writes ``frequency,raw[,error,smoothed,...]`` with a header line.  The
reader also takes what AutoEq itself accepts: other column and decimal
separators, no header, and REW text exports (``* Freq(Hz), SPL(dB), ...``).
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from pathlib import Path

from ..fileformat import Format, Inspector, file_section, fixed, frequency_range
from ..report import Section, Table

FREQUENCY = "frequency"
RAW = "raw"
# The column separators AutoEq detects; whitespace is the fallback.
SEPARATORS = (",", ";", "\t", "|")
_NUMERIC = re.compile(r"^[-+]?(\d|\.\d)")
_FREQUENCY_NAME = re.compile(r"^freq", re.IGNORECASE)
_VALUE_NAME = re.compile(r"^(raw|spl|gain|ampl)", re.IGNORECASE)


@dataclass
class Response:
    """Every column of one file; missing values are NaN."""

    name: str
    columns: dict[str, list[float]]  # "frequency" first

    def curve(self, column: str = RAW) -> list[tuple[float, float]]:
        """Sorted (frequency, value) points of ``column``; NaN values are left out."""
        if column not in self.columns:
            raise ValueError(
                f"{self.name}: no {column!r} column; "
                f"available: {', '.join(list(self.columns)[1:])}"
            )
        points = sorted(
            (f, v)
            for f, v in zip(self.columns[FREQUENCY], self.columns[column])
            if math.isfinite(f) and math.isfinite(v)
        )
        if len(points) < 2:
            raise ValueError(f"{self.name}: {column!r} has fewer than two values")
        for (f0, _), (f1, _) in itertools.pairwise(points):
            if f0 == f1:
                raise ValueError(f"{self.name}: duplicate frequency {f0:g} Hz")
        return points


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
def _separators(rows: list[str]) -> tuple[str | None, str]:
    """(column separator, decimal mark); None splits on whitespace."""
    found = [s for s in SEPARATORS if all(s in row for row in rows)]
    others = [s for s in found if s != ","]
    if len(others) > 1:
        raise ValueError(f"ambiguous column separators: {' '.join(map(repr, others))}")
    if others:
        # A comma next to another separator is the decimal mark.  Rows with
        # integer values have none, so one row with a comma is enough.
        return others[0], "," if any("," in row for row in rows) else "."
    return ("," if found else None), "."


def _split(line: str, separator: str | None) -> list[str]:
    return [
        cell.strip() for cell in (line.split(separator) if separator else line.split())
    ]


def _number(cell: str, decimal: str, where: str) -> float:
    try:
        return float(cell.replace(decimal, ".") if decimal != "." else cell)
    except ValueError:
        raise ValueError(f"{where}: not a number: {cell!r}") from None


def read(text: str, name: str = "") -> Response:
    lines = [
        (i + 1, line.strip())
        for i, line in enumerate(text.splitlines())
        if line.strip()
    ]
    rows = [(n, line) for n, line in lines if _NUMERIC.match(line)]
    if not rows:
        raise ValueError(f"{name}: no numeric rows")
    separator, decimal = _separators([line for _, line in rows])
    cells = [(n, _split(line, separator)) for n, line in rows]
    width = len(cells[0][1])
    if width < 2:
        raise ValueError(f"{name}: fewer than two columns")
    for n, row in cells:
        if len(row) != width:
            raise ValueError(f"{name}:{n}: {len(row)} columns, expected {width}")

    # The header is the last text line before the data, with as many columns.
    header = None
    for n, line in lines:
        if n >= rows[0][0]:
            break
        names = _split(line.lstrip("*").strip(), separator)
        if len(names) == width:
            header = [c.lower() for c in names]
    if header is None:
        header = [FREQUENCY, RAW] + [f"column {i + 1}" for i in range(2, width)]
    else:
        freq = next((i for i, c in enumerate(header) if _FREQUENCY_NAME.match(c)), 0)
        value = next((i for i, c in enumerate(header) if c == RAW), None)
        if value is None:
            value = next(
                (i for i, c in enumerate(header) if i != freq and _VALUE_NAME.match(c)),
                1 if freq != 1 else 0,
            )
        header[freq], header[value] = FREQUENCY, RAW
        order = [freq] + [i for i in range(width) if i != freq]
        header = [header[i] for i in order]
        cells = [(n, [row[i] for i in order]) for n, row in cells]
    if len(set(header)) != width:
        raise ValueError(f"{name}: duplicate column names")

    columns: dict[str, list[float]] = {c: [] for c in header}
    for n, row in cells:
        for column, cell in zip(header, row):
            columns[column].append(_number(cell, decimal, f"{name}:{n}"))
    return Response(name, columns)


def load(path) -> Response:
    path = Path(path)
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # AutoEq's fallback encoding.
        text = data.decode("windows-1252", errors="replace")
    return read(text, path.stem)


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def write(points, column: str = RAW) -> str:
    """``frequency,<column>`` CSV text of sorted (frequency, value) points.

    AutoEq's number pattern has no exponent; it writes two decimals.
    """
    lines = [f"{FREQUENCY},{column}"]
    previous = None
    for frequency, value in points:
        if not (math.isfinite(frequency) and math.isfinite(value)) or frequency <= 0:
            raise ValueError(f"cannot write the point ({frequency}, {value})")
        f = fixed(frequency)
        if f == previous:
            raise ValueError(f"frequencies too close for two decimals at {f} Hz")
        lines.append(f"{f},{fixed(value)}")
        previous = f
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class AutoeqInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        response = load(path)
        columns = list(response.columns)
        rows = list(zip(*response.columns.values()))
        return [
            file_section(path, data),
            Section(
                "frequency response",
                [
                    ("columns", ", ".join(columns)),
                    ("points", len(rows)),
                    ("range", frequency_range(sorted(rows))),
                ],
                Table(columns, rows),
            ),
        ]


FORMAT = Format("autoeq", (".csv",), "AutoEq frequency response CSV", AutoeqInspector)
