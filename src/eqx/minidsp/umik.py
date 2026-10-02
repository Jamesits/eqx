"""Reader for miniDSP UMIK microphone calibration files (``<serial>.txt``).

A REW calibration file with a header line, quoted or not::

    "Sens Factor =-5.846dB, SERNO: 7001234"
    Sens Factor =-3.27dB, SERNO: 7060837
    "Sens Factor =-14.52dB, AGain =18dB, SERNO: 8101234"

then one ``frequency<TAB>gain`` row per line; the number of points (80, 133
or 615) and the decimals vary by serial.  The 90 degree file
(``<serial>_90deg.txt``) has a second header line
``"Auto-generated 90-degree calibration file"``.  For a serial without
calibration data miniDSP serves an HTML error message as the ``.txt`` file.

https://www.hifi-selbstbau.de/index.php/hsb/ueberarbeitung-des-hifi-selbstbau-hoerraums/umik1-kalibrierung-wenn-ja-warum-nicht
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

_SIDE = re.compile(r"\b90[- ]?deg", re.IGNORECASE)
_NO_DATA = "Unable to locate calibration data"

# The first serial digit; the known serials start with 70x-72x (UMIK-1) and 810 (UMIK-2).
MODELS = {"7": "UMIK-1", "8": "UMIK-2"}


@dataclass
class Umik:
    serial: str
    sensitivity_db: float  # dBFS rms at 94 dB SPL, maximum input volume
    analog_gain_db: float | None  # gain setting of the calibration, if given
    profile: MicProfile  # name: serial; angle: degrees_0 or degrees_90
    header: list[str]  # the non-numeric lines

    @property
    def model(self) -> str:
        return MODELS.get(self.serial[:1], "unknown")


def read(text: str, name: str = "") -> Umik:
    """``name`` (the file stem) marks the 90 degree table by its ``_90deg`` suffix."""
    if _NO_DATA in text:
        raise ValueError(
            f"{name or 'file'}: miniDSP has no calibration data for this serial "
            "(the file is the download's error message)"
        )
    table, other = cal.read(text, name)
    match = next((m for m in map(cal.SENS_FACTOR.match, other) if m), None)
    if match is None:
        raise ValueError(
            f'{name or "file"}: no UMIK header line ("Sens Factor =...dB, SERNO: ...")'
        )
    side = name.lower().endswith("_90deg") or any(_SIDE.search(line) for line in other)
    serial = match["serial"]
    return Umik(
        serial,
        float(match["sens"]),
        float(match["gain"]) if match["gain"] is not None else None,
        MicProfile(serial, SIDE_ANGLE if side else PLAIN_ANGLE, table.points),
        other,
    )


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
            Section(
                "microphone",
                [
                    ("model", umik.model),
                    ("serial", umik.serial),
                    (
                        "sensitivity",
                        (
                            f"{umik.sensitivity_db:g} dBFS rms at 94 dB SPL, "
                            "maximum input volume"
                        ),
                    ),
                    ("analog gain", f"{gain:g} dB" if gain is not None else None),
                    ("angle", umik.profile.angle),
                    *(("text", line) for line in umik.header),
                ],
            ),
            Section(
                "calibration",
                [("points", len(points)), ("range", frequency_range(points))],
                Table(["frequency Hz", "gain dB"], points),
            ),
        ]


def sniff(data: bytes) -> bool:
    """The first non-empty line is the header, or the download's error message."""
    text = head_text(data)
    first = next((line for line in text.splitlines() if line.strip()), "")
    return cal.SENS_FACTOR.match(first) is not None or (
        first.lstrip().startswith("<") and _NO_DATA in text
    )


FORMAT = Format(
    "umik", (".txt",), "miniDSP UMIK microphone calibration file", UmikInspector, sniff
)
