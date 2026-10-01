"""Reader and writer for Smaart ASCII trace tables (``*.txt``).

Smaart's Export to ASCII writes tab-separated tables, CRLF line ends::

    <TAB>Left<TAB><TAB>                                (transfer function)
    Frequency (Hz)<TAB>Magnitude (dB)<TAB>Phase (degrees)<TAB>Coherence
    1.464844<TAB>-0.12<TAB>3.45<TAB>0.98
    2.929688<TAB>*<TAB>*<TAB>*                          (below the coherence threshold)

    Trace Name<TAB>Rta<TAB>                            (spectrum)
    Frequency (Hz)<TAB>Level (dB)<TAB>Peak (dB)

One name and one column group per trace.  A decimal comma replaces the point
where the system uses one.  Import ASCII reads ``frequency<TAB>dB`` rows and
skips lines that start with ``*`` or ``;``; the writer puts the name and
header lines behind ``*``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..fileformat import (
    Format,
    Inspector,
    file_section,
    fixed,
    frequency_range,
    head_text,
)
from ..options import Option
from ..report import Section, Table

FREQUENCY = "Frequency (Hz)"
MAGNITUDE, PHASE, COHERENCE = "Magnitude (dB)", "Phase (degrees)", "Coherence"
LEVEL, PEAK = "Level (dB)", "Peak (dB)"
NO_DATA = "*"
_COMMENT = re.compile(r"^[*;] ?")
_HEADER = re.compile(rf"^(?:[*;] ?)?{re.escape(FREQUENCY)}\t", re.MULTILINE)

TRACE_OPTION = Option(
    "--trace",
    help="trace: its number from 0 as listed by inspect, or its name, "
    "case-insensitive (default: 0)",
)


@dataclass
class AsciiTrace:
    name: str
    columns: list[str]  # value columns, e.g. Magnitude (dB)
    values: list[list[float | None]]  # per column; None: no data

    @property
    def kind(self) -> str:
        return {MAGNITUDE: "transfer function", LEVEL: "spectrum"}.get(
            self.columns[0], "table"
        )


@dataclass
class AsciiTable:
    frequencies: list[float]
    traces: list[AsciiTrace]

    def trace(self, key: str | int | None) -> int:
        """Index of the trace with this number or name (case-insensitive)."""
        if key is None:
            return 0
        text = str(key).strip()
        if text.isdigit():
            if int(text) >= len(self.traces):
                raise ValueError(
                    f"trace {text} does not exist; traces: 0-{len(self.traces) - 1}"
                )
            return int(text)
        for i, t in enumerate(self.traces):
            if t.name.lower() == text.lower():
                return i
        raise ValueError(
            f"no trace {key!r}; available: "
            f"{', '.join(repr(t.name) for t in self.traces)}"
        )

    def curve(self, index: int) -> list[tuple[float, float]]:
        """(frequency, value) of the trace's first column, where it has data."""
        t = self.traces[index]
        points = [
            (f, v)
            for f, v in zip(self.frequencies, t.values[0])
            if v is not None and f > 0
        ]
        if len(points) < 2:
            raise ValueError(
                f"trace {t.name or index}: fewer than two points with data"
            )
        return points


def _cell(text: str, where: str) -> float | None:
    text = text.strip()
    if text == NO_DATA or not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        raise ValueError(f"{where}: not a number: {text!r}") from None


def read(text: str, name: str = "") -> AsciiTable:
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if _HEADER.match(line)), None)
    if index is None:
        raise ValueError(f"{name or 'file'}: no {FREQUENCY!r} header line")
    header = _COMMENT.sub("", lines[index]).rstrip("\r\n").split("\t")
    names = _COMMENT.sub("", lines[index - 1]).split("\t") if index > 0 else []
    width = len(header)
    if width < 2:
        raise ValueError(f"{name or 'file'}: no value column")
    # A trace starts at each repetition of the first value column.
    starts = [i for i in range(1, width) if header[i] == header[1]]
    traces = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else width
        label = names[start].strip() if start < len(names) else ""
        traces.append(
            AsciiTrace(label, header[start:end], [[] for _ in range(start, end)])
        )
    frequencies = []
    for n, line in enumerate(lines[index + 1 :], index + 2):
        if not line.strip() or _COMMENT.match(line):
            continue
        cells = line.split("\t")
        where = f"{name or 'file'}:{n}"
        if len(cells) < width:
            raise ValueError(f"{where}: {len(cells)} columns, expected {width}")
        f = _cell(cells[0], where)
        if f is None:
            raise ValueError(f"{where}: no frequency")
        frequencies.append(f)
        for k, start in enumerate(starts):
            for j, values in enumerate(traces[k].values):
                values.append(_cell(cells[start + j], where))
    if not frequencies:
        raise ValueError(f"{name or 'file'}: no rows")
    return AsciiTable(frequencies, traces)


def load(path) -> AsciiTable:
    path = Path(path)
    return read(path.read_bytes().decode("utf-8", errors="replace"), path.stem)


def write(points, name: str = "") -> str:
    """An Import ASCII file of (frequency, dB) ``points``: the name and header
    lines as comments, then ``frequency<TAB>dB`` rows."""
    lines = [f"*\t{name}", f"* {FREQUENCY}\t{MAGNITUDE}"]
    lines += [f"{f:.6f}\t{fixed(v)}" for f, v in points]
    return "\r\n".join(lines) + "\r\n"


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class SmaartAsciiInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        table = read(data.decode("utf-8", errors="replace"), path.stem)
        sections = [
            file_section(
                path,
                data,
                ("traces", len(table.traces)),
                ("rows", len(table.frequencies)),
            )
        ]
        for i, t in enumerate(table.traces):
            rows = list(zip(table.frequencies, *t.values))
            empty = sum(1 for v in t.values[0] if v is None)
            sections.append(
                Section(
                    f"trace {i} {t.name}".rstrip(),
                    [
                        ("kind", t.kind),
                        ("rows without data", empty or None),
                        (
                            "range",
                            frequency_range([r for r in rows if r[1] is not None]),
                        ),
                    ],
                    Table(["frequency Hz", *t.columns], rows),
                )
            )
        return sections


def sniff(data: bytes) -> bool:
    """A ``Frequency (Hz)<TAB>`` header line."""
    return _HEADER.search(head_text(data)) is not None


FORMAT = Format(
    "smaart-ascii", (".txt",), "Smaart ASCII trace table", SmaartAsciiInspector, sniff
)
