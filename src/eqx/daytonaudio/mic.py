"""Reader for Dayton Audio microphone calibration files (``<serial>.txt``, ``.omm``).

Two header layouts, then one ``frequency gain [phase]`` row per line:

- USB microphones (UMM-6, iMM-6, OmniMic ``.omm``), the REW header line of
  miniDSP UMIK files, optionally quoted::

      Sens Factor =-19.134dB, SERNO: 1680031
      "Sens Factor = -2.899dB, SERNO: 40k0001"

  The rows have a third column: phase in degrees.

- Analog microphones (EMM-6), a REW comment line with the sensitivity at
  1 kHz::

      *1000Hz	-41.0
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
SIDE_SUFFIX = "_90deg"

_ANALOG = re.compile(
    rf"^\s*\*\s*(?P<hz>{cal.NUMBER})\s*Hz\s+(?P<sens>{cal.NUMBER})\s*$", re.IGNORECASE
)


@dataclass
class DaytonMic:
    model: str  # "OmniMic", "UMM-6 / iMM-6" or "EMM-6"
    serial: str  # EMM-6: the file stem; the file holds none
    sensitivity_db: float
    sensitivity_unit: str  # "dBFS" (USB) or "dBV" (analog)
    reference_hz: float | None  # analog: the sensitivity frequency
    profile: MicProfile  # name: serial; angle: degrees_0 or degrees_90
    phase: list[tuple[float, float]]  # sorted (frequency Hz, degrees); may be empty
    header: list[str]  # the non-numeric lines

    @property
    def sensitivity(self) -> str:
        if self.reference_hz is None:
            return (
                f"{self.sensitivity_db:g} dBFS rms at 94 dB SPL, maximum input volume"
            )
        return f"{self.sensitivity_db:g} dBV/Pa at {self.reference_hz:g} Hz"


def _rows(text: str) -> tuple[list[tuple[float, ...]], list[str]]:
    """Numeric rows (2 or 3 values) and the other non-empty lines, as ``rew.cal``."""
    rows, other = [], []
    for line in text.splitlines():
        columns = line.replace(",", " ").split()
        try:
            values = [float(c) for c in columns[:3]]
            if len(values) < 2:
                raise ValueError
        except ValueError:
            if line.strip():
                other.append(line.rstrip())
            continue
        rows.append(tuple(values))
    return rows, other


def read(text: str, name: str = "", suffix: str = ".txt") -> DaytonMic:
    """``name`` (the file stem) marks the 90 degree table by its ``_90deg`` suffix;
    ``suffix`` ``.omm`` marks an OmniMic file."""
    rows, other = _rows(text)
    side = name.lower().endswith(SIDE_SUFFIX)
    stem = name[: -len(SIDE_SUFFIX)] if side else name
    usb = next((m for m in map(cal.SENS_FACTOR.match, other) if m), None)
    analog = next((m for m in map(_ANALOG.match, other) if m), None)
    if usb is not None:
        model = "OmniMic" if suffix.lower() == ".omm" else "UMM-6 / iMM-6"
        serial, unit, hz = usb["serial"], "dBFS", None
        sensitivity = float(usb["sens"])
    elif analog is not None:
        model, serial, unit = "EMM-6", stem, "dBV"
        sensitivity, hz = float(analog["sens"]), float(analog["hz"])
    else:
        raise ValueError(
            f"{name or 'file'}: no Dayton Audio header line "
            '("Sens Factor =...dB, SERNO: ..." or "*1000Hz <dBV>")'
        )
    angle = SIDE_ANGLE if side else PLAIN_ANGLE
    profile = MicProfile.from_points(serial, angle, [r[:2] for r in rows])
    phase = sorted((r[0], r[2]) for r in rows if len(r) == 3)
    return DaytonMic(model, serial, sensitivity, unit, hz, profile, phase, other)


def load(path) -> DaytonMic:
    path = Path(path)
    return read(
        path.read_text(encoding="utf-8", errors="replace"), path.stem, path.suffix
    )


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class DaytonInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        mic = read(data.decode("utf-8", errors="replace"), path.stem, path.suffix)
        points = mic.profile.points
        phase = dict(mic.phase)
        if phase:
            table = Table(
                ["frequency Hz", "gain dB", "phase deg"],
                [(f, g, phase.get(f)) for f, g in points],
            )
        else:
            table = Table(["frequency Hz", "gain dB"], points)
        return [
            file_section(path, data),
            Section(
                "microphone",
                [
                    ("model", mic.model),
                    ("serial", mic.serial),
                    ("sensitivity", mic.sensitivity),
                    ("angle", mic.profile.angle),
                    *(("text", line) for line in mic.header),
                ],
            ),
            Section(
                "calibration",
                [
                    ("points", len(points)),
                    ("range", frequency_range(points)),
                    ("phase", "yes" if phase else "no"),
                ],
                table,
            ),
        ]


def sniff(data: bytes) -> bool:
    """The first non-empty line is the analog header, or the USB header followed by
    a row with phase (UMIK rows have none)."""
    lines = [line for line in head_text(data).splitlines() if line.strip()]
    if not lines:
        return False
    if _ANALOG.match(lines[0]):
        return True
    if not cal.SENS_FACTOR.match(lines[0]):
        return False
    rows, _ = _rows("\n".join(lines[1:3]))
    return bool(rows) and len(rows[0]) == 3


FORMAT = Format(
    "dayton",
    (".txt", ".omm"),
    "Dayton Audio microphone calibration file",
    DaytonInspector,
    sniff,
)
