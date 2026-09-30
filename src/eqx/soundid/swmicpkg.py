"""Reader for SoundID microphone calibration packages (``*.swmicpkg``).

A package is a JSON object mapping angle names (``degrees_0``, ...) to base64
text tables of ``frequency gain`` rows.  ``degrees_0`` is plain; the other
tables are ``IV || AES-128-CBC(table)``.

A table is the microphone's own response, not its inverse: the 90 degree
table falls below the 0 degree table at high frequencies.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from ..fileformat import Format, Inspector, file_section, frequency_range
from ..model import MicProfile
from ..report import Section, Table
from . import crypto

# Key of the encrypted package tables.  SoundID builds it from two 64-bit
# immediates, not from a password, so it appears in no string table.
PACKAGE_KEY = bytes.fromhex("888d6cb3f3541d51a3ba70cd15dc7e24")
PLAIN_ANGLE = "degrees_0"


def angles(data: str) -> list[str]:
    try:
        package = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot read microphone package: {exc}") from exc
    if not isinstance(package, dict):
        raise ValueError("microphone package is not a JSON object")
    return list(package)


def read(data: str, angle: str = PLAIN_ANGLE, name: str = "") -> MicProfile:
    """Read one table from package text."""
    try:
        package = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot read microphone package {name}: {exc}") from exc
    if angle not in package:
        raise ValueError(f"microphone package has no {angle!r} table; "
                         f"available: {', '.join(package)}")
    try:
        blob = base64.b64decode(package[angle], validate=True)
        if angle != PLAIN_ANGLE:
            blob = crypto.decrypt(PACKAGE_KEY, blob)
        text = blob.decode("utf-8")
    except ValueError as exc:               # also UnicodeDecodeError
        raise ValueError(f"{name}:{angle} is not a valid table: {exc}") from exc
    points: list[tuple[float, float]] = []
    for line in text.splitlines():
        columns = line.strip().split()
        if len(columns) < 2:
            continue
        try:
            points.append((float(columns[0]), float(columns[1])))
        except ValueError:
            continue
    return MicProfile.from_points(name, angle, points)


def read_all(data: str, name: str = "") -> list[MicProfile]:
    return [read(data, angle, name) for angle in angles(data)]


def load(path, angle: str = PLAIN_ANGLE) -> MicProfile:
    """Read one table; the microphone name is the file name stem (the serial)."""
    path = Path(path)
    return read(path.read_text(encoding="utf-8"), angle, path.stem)


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class SwmicpkgInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        text = data.decode("utf-8")
        names = angles(text)
        sections = [file_section(path, data, ("serial", path.stem),
                                 ("tables", ", ".join(names)))]
        for angle in names:
            fields = [("encrypted", angle != PLAIN_ANGLE)]
            try:
                profile = read(text, angle, path.stem)
            except ValueError as exc:
                sections.append(Section(f"table {angle}", fields + [("error", str(exc))]))
                continue
            fields += [("points", len(profile.points)),
                       ("range", frequency_range(profile.points))]
            sections.append(Section(f"table {angle}", fields,
                                    Table(["frequency Hz", "gain dB"], profile.points)))
        return sections


FORMAT = Format("swmicpkg", (".swmicpkg",), "SoundID microphone calibration package",
                SwmicpkgInspector)
