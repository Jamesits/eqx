"""Reader for miniDSP UMIK microphone calibration files (``<serial>.txt``).

A REW calibration file with a quoted header line::

    "Sens Factor =-5.846dB, SERNO: 7001234"
    "Sens Factor =-14.52dB, AGain =18dB, SERNO: 8101234"

then one ``frequency<TAB>gain`` row per line.  The 90 degree file
(``<serial>_90deg.txt``) has a second header line
``"Auto-generated 90-degree calibration file"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..fileformat import Format, Inspector, file_section, frequency_range, head_text
from ..model import MicProfile
from ..report import Section, Table
from ..rew import cal

PLAIN_ANGLE, SIDE_ANGLE = "degrees_0", "degrees_90"

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)"
_HEADER = re.compile(rf'^\s*"?\s*Sens Factor\s*=\s*(?P<sens>{_NUMBER})\s*dB\s*,'
                     rf'(?:\s*AGain\s*=\s*(?P<gain>{_NUMBER})\s*dB\s*,)?'
                     r'\s*SERNO:\s*(?P<serial>[^",\s]+)\s*"?\s*$', re.IGNORECASE)
_SIDE = re.compile(r"\b90[- ]?deg", re.IGNORECASE)

# The first serial digit; the known serials start with 700 (UMIK-1) and 810 (UMIK-2).
MODELS = {"7": "UMIK-1", "8": "UMIK-2"}


@dataclass
class Umik:
    serial: str
    sensitivity_db: float                   # dBFS rms at 94 dB SPL, maximum input volume
    analog_gain_db: float | None            # UMIK-2: gain setting of the calibration
    profile: MicProfile                     # name: serial; angle: degrees_0 or degrees_90
    header: list[str]                       # the non-numeric lines

    @property
    def model(self) -> str:
        return MODELS.get(self.serial[:1], "unknown")


def read(text: str, name: str = "") -> Umik:
    """``name`` (the file stem) marks the 90 degree table by its ``_90deg`` suffix."""
    table, other = cal.read(text, name)
    match = next((m for m in map(_HEADER.match, other) if m), None)
    if match is None:
        raise ValueError(f"{name or 'file'}: no UMIK header line "
                         '("Sens Factor =...dB, SERNO: ...")')
    side = name.lower().endswith("_90deg") or any(_SIDE.search(line) for line in other)
    serial = match["serial"]
    return Umik(serial, float(match["sens"]),
                float(match["gain"]) if match["gain"] is not None else None,
                MicProfile(serial, SIDE_ANGLE if side else PLAIN_ANGLE, table.points), other)


def load(path) -> Umik:
    path = Path(path)
    return read(path.read_text(encoding="utf-8", errors="replace"), path.stem)


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class UmikInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        umik = read(data.decode("utf-8", errors="replace"), path.stem)
        points = umik.profile.points
        gain = umik.analog_gain_db
        return [
            file_section(path, data),
            Section("microphone", [
                ("model", umik.model),
                ("serial", umik.serial),
                ("sensitivity", f"{umik.sensitivity_db:g} dBFS rms at 94 dB SPL, "
                                "maximum input volume"),
                ("analog gain", f"{gain:g} dB" if gain is not None else None),
                ("angle", umik.profile.angle),
                *(("text", line) for line in umik.header),
            ]),
            Section("calibration", [("points", len(points)), ("range", frequency_range(points))],
                    Table(["frequency Hz", "gain dB"], points)),
        ]


def sniff(data: bytes) -> bool:
    """The first non-empty line is the header."""
    lines = head_text(data).splitlines()
    return _HEADER.match(next((line for line in lines if line.strip()), "")) is not None


FORMAT = Format("umik", (".txt",), "miniDSP UMIK microphone calibration file", UmikInspector,
                sniff)
