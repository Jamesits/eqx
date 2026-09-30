"""Reader for SoundID text exports (Dolby Atmos Renderer, SPQ DSP ``*.txt``).

``Key: value`` header lines, then one block per channel:
``<name> channel calibration:``, ``Delay: <ms> ms``, ``Gain: <dB> dB`` and a
Markdown table.  A ``Freq|Gain`` table is a graphic EQ; a
``Type|Freq|Gain|Q`` table lists bell filters (``Parametric Eq <n>``).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..correction import Export, ExportInspector, number
from ..fileformat import Format
from ..model import Correction, Peq

FIRST_KEY = "Preset name:"
_CHANNEL = re.compile(r"^(\S+) channel calibration:$")
_VALUE = re.compile(r"^(Delay|Gain):\s*(\S+)\s*(ms|dB)$")
_UNIT = re.compile(r"\s*(Hz|dB)$")


def _cells(line: str) -> list[str]:
    return [_UNIT.sub("", c.strip()) for c in line.strip().strip("|").split("|")]


def read(text: str) -> Export:
    fields, corrections = [], []
    columns: list[str] = []
    current: Correction | None = None
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        where = f"line {n}"
        if not line:
            continue
        if match := _CHANNEL.match(line):
            current = Correction(match.group(1))
            corrections.append(current)
            columns = []
        elif current is None:
            key, sep, value = line.partition(":")
            if not sep:
                raise ValueError(f"{where}: expected 'key: value'")
            fields.append((key.strip(), value.strip()))
        elif match := _VALUE.match(line):
            value = number(match.group(2), where)
            if match.group(1) == "Delay":
                current.delay_ms = value
            else:
                current.gain_db = value
        elif line.startswith("|"):
            cells = _cells(line)
            if not columns:
                columns = cells
                if columns not in (["Freq", "Gain"], ["Type", "Freq", "Gain", "Q"]):
                    raise ValueError(f"{where}: unknown table columns {columns}")
            elif all(set(c) <= set(":-") for c in cells):
                continue                    # the Markdown separator row
            elif len(cells) != len(columns):
                raise ValueError(f"{where}: expected {len(columns)} cells")
            elif len(columns) == 2:
                current.points.append((number(cells[0], where), number(cells[1], where)))
            else:
                if not cells[0].startswith("Parametric Eq"):
                    raise ValueError(f"{where}: unsupported filter type {cells[0]!r}")
                current.peqs.append(Peq(*(number(c, where) for c in cells[1:])))
        else:
            raise ValueError(f"{where}: unexpected line {line!r}")
    if not corrections:
        raise ValueError("no channel calibration in the text export")
    return Export(corrections, fields)


def load(path) -> Export:
    return read(Path(path).read_text(encoding="utf-8-sig"))


class TextExportInspector(ExportInspector):
    load = load


FORMAT = Format("soundid-export-txt", (".txt",),
                "SoundID export: text (Dolby Atmos Renderer, SPQ DSP)", TextExportInspector,
                lambda data: data.lstrip(b"\xef\xbb\xbf").startswith(FIRST_KEY.encode()))
