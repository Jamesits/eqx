"""Reader and writer for REW microphone calibration files (``*.txt``, ``*.cal``).

One ``frequency gain [phase]`` row per line.  REW skips lines that do not
start with a number, and subtracts the table from measurements.
"""

from __future__ import annotations

from pathlib import Path

from ..fileformat import Format, Inspector, file_section, frequency_range
from ..model import MicProfile
from ..report import Section, Table

COMMENT = "*"


def _number(value: float) -> str:
    return format(value + 0.0, ".6g")       # + 0.0 turns -0.0 into 0.0


def write(profile: MicProfile, source: str = "") -> str:
    """Calibration file text; ``source`` names the origin in the comment line."""
    title = " ".join(s for s in (source, "microphone", profile.name, profile.angle) if s)
    lines = [f"{COMMENT} {title}"]
    lines += [f"{_number(f)}\t{_number(g)}" for f, g in profile.points]
    return "\n".join(lines) + "\n"


def read(text: str, name: str = "") -> tuple[MicProfile, list[str]]:
    """Return the table and the non-numeric lines (comments, sensitivity)."""
    points, other = [], []
    for line in text.splitlines():
        columns = line.replace(",", " ").split()
        try:
            points.append((float(columns[0]), float(columns[1])))
        except (IndexError, ValueError):
            if line.strip():
                other.append(line.rstrip())
    return MicProfile.from_points(name, "", points), other


def load(path) -> tuple[MicProfile, list[str]]:
    path = Path(path)
    return read(path.read_text(encoding="utf-8", errors="replace"), path.stem)


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class RewcalInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        profile, other = read(data.decode("utf-8", errors="replace"), path.stem)
        return [
            file_section(path, data),
            Section("calibration",
                    [("points", len(profile.points)),
                     ("range", frequency_range(profile.points)),
                     *(("text", line) for line in other)],
                    Table(["frequency Hz", "gain dB"], profile.points)),
        ]


def sniff(data: bytes) -> bool:
    """A numeric row among the first lines."""
    for line in data[:4096].decode("utf-8", errors="replace").splitlines():
        try:
            float(line.replace(",", " ").split()[0])
            return True
        except (IndexError, ValueError):
            pass
    return False


FORMAT = Format("rewcal", (".txt", ".cal"), "REW microphone calibration file", RewcalInspector,
                sniff)
