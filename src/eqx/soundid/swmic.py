"""Reader for SoundID microphone calibration tables (``.swmic``, ``.txt``).

The files of a downloaded microphone profile, before SoundID packs them into
a ``.swmicpkg``::

    <serial>_cal_0degree.txt                  plain table
    <serial>_cal_Sonarworks_30degree.swmic    IV || AES-128-CBC(table)
    <serial>_cal_Sonarworks_90degree.swmic

A table is one ``frequency<TAB>gain`` row per point, no header: frequency
with one decimal, gain with two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..fileformat import Format, Inspector, file_section, frequency_range, head_text
from ..model import MicProfile
from ..report import Section, Table
from . import crypto, swmicpkg

_NAME = re.compile(
    r"^(?P<serial>.+?)_cal_(?:sonarworks_)?(?P<degrees>\d+)degrees?$", re.IGNORECASE
)
_ROW = re.compile(r"^\d+\.\d\t-?\d+\.\d\d$")
# Downloaded tables start at the package grid's first frequency.
_FIRST_ROW = re.compile(r"^20\.0\t")


@dataclass
class SwmicTable:
    serial: str  # from the file name
    encrypted: bool
    profile: MicProfile  # name: serial; angle from the file name


def parse_name(stem: str) -> tuple[str, str]:
    """(serial, angle) of a ``<serial>_cal_[Sonarworks_]<n>degree`` file stem;
    other names: (stem, ``degrees_0``)."""
    match = _NAME.match(stem)
    if match is None:
        return stem, swmicpkg.PLAIN_ANGLE
    return match["serial"], f"degrees_{int(match['degrees'])}"


def _is_text(data: bytes) -> bool:
    return all(32 <= b < 127 or b in b"\t\r\n" for b in data)


def read(data: bytes, name: str = "") -> SwmicTable:
    """``name`` (the file stem) gives the serial and the angle.

    The content, not the extension, tells an encrypted table: a renamed
    ``.swmic`` still reads.  A random IV makes an all-text ciphertext
    practically impossible.
    """
    serial, angle = parse_name(name)
    encrypted = not _is_text(data)
    if encrypted:
        try:
            data = crypto.decrypt(swmicpkg.PACKAGE_KEY, data)
        except ValueError as exc:
            raise ValueError(
                f"{name or 'file'}: cannot decrypt the table: {exc}"
            ) from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name or 'file'} is not a valid table: {exc}") from exc
    return SwmicTable(serial, encrypted, swmicpkg.parse_table(text, serial, angle))


def load(path) -> SwmicTable:
    path = Path(path)
    return read(path.read_bytes(), path.stem)


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class SwmicInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        table = read(data, path.stem)
        points = table.profile.points
        return [
            file_section(path, data),
            Section(
                "microphone",
                [
                    ("serial", table.serial),
                    ("angle", table.profile.angle),
                    ("encrypted", table.encrypted),
                ],
            ),
            Section(
                "calibration",
                [("points", len(points)), ("range", frequency_range(points))],
                Table(["frequency Hz", "gain dB"], points),
            ),
        ]


def sniff(data: bytes) -> bool:
    """Plain text: every line a ``frequency<TAB>gain`` row with one and two
    decimals, the first at 20 Hz."""
    lines = head_text(data).splitlines()
    if len(data) > 4096:
        lines = lines[:-1]  # may be cut
    lines = [line for line in lines if line.strip()]
    return (
        len(lines) >= 2
        and _FIRST_ROW.match(lines[0]) is not None
        and all(_ROW.match(line) for line in lines)
    )


FORMAT = Format(
    "swmic",
    (".swmic", ".txt"),
    "SoundID microphone calibration table",
    SwmicInspector,
    sniff,
)
