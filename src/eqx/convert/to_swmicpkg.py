"""REW, miniDSP UMIK and Dayton Audio microphone calibration files -> SoundID
microphone package."""

from __future__ import annotations

from pathlib import Path

from ..curve import log_resample
from ..daytonaudio import mic
from ..minidsp import umik
from ..model import MicProfile
from ..options import Option
from ..rew import cal
from ..soundid import swmicpkg
from .base import Converter, Result

SERIAL_OPTION = Option(
    "--serial",
    help="microphone serial, the package name (default: the "
    "serial in a UMIK or Dayton Audio file, else the first input name)",
)


def package(serial: str, tables: dict[str, MicProfile]) -> tuple[bytes, list[str]]:
    """The package of the ``tables`` (angle -> table), each resampled to the package grid.

    A missing angle gets a copy of the degrees_0 table.
    """
    grid = swmicpkg.grid()
    profiles, notes = {}, []
    for angle in swmicpkg.ANGLES:
        if angle in tables:
            gains = log_resample(tables[angle].points, grid)
            profiles[angle] = MicProfile(serial, angle, list(zip(grid, gains)))
        else:
            profiles[angle] = MicProfile(
                serial, angle, profiles[swmicpkg.PLAIN_ANGLE].points
            )
            notes.append(f"{angle}: copy of {swmicpkg.PLAIN_ANGLE}")
    return swmicpkg.write(list(profiles.values())), notes


class RewcalToSwmicpkg(Converter):
    """The inputs are the 0, 30 and 90 degree tables, resampled to the package grid.

    Both formats hold the microphone's own response, so no inversion.  A
    missing angle gets a copy of the 0 degree table.
    """

    source = "rewcal"
    target = "swmicpkg"
    description = (
        "microphone calibration (inputs: 0, optional 30 and 90 degrees) as a "
        "SoundID microphone package"
    )
    options = (SERIAL_OPTION,)
    inputs = len(swmicpkg.ANGLES)

    def __init__(self, serial: str | None = None):
        self.serial = serial

    def _convert(self, *paths: Path) -> Result:
        serial = self.serial or paths[0].stem
        tables, notes = {}, []
        for angle, path in zip(swmicpkg.ANGLES, paths):
            table, other = cal.load(path)
            tables[angle] = table
            notes.append(
                f"{angle}: {path.name}, {len(table.points)} points"
                + (f"; {len(other)} other lines ignored" if other else "")
            )
        data, copies = package(serial, tables)
        return Result(data, f"{serial}.swmicpkg", notes + copies)


class _MicFilesToSwmicpkg(Converter):
    """The inputs are the 0 degree and the optional 90 degree file, in any order.

    Each file's angle comes from its header or ``_90deg`` name.  There is no
    30 degree table; it gets a copy of the 0 degree table.  The sensitivity
    has no place in the package.  Subclasses read the files.
    """

    target = "swmicpkg"
    options = (SERIAL_OPTION,)
    inputs = 2

    def __init__(self, serial: str | None = None):
        self.serial = serial

    def _load(self, path: Path):
        """An object with ``serial``, ``model``, ``sensitivity_db``, ``profile``."""
        raise NotImplementedError

    def _note(self, mic) -> list[str]:
        raise NotImplementedError

    def _convert(self, *paths: Path) -> Result:
        files = [(path, self._load(path)) for path in paths]
        serials = {m.serial for _, m in files}
        if len(serials) > 1:
            raise ValueError(
                f"the inputs are of different microphones: {', '.join(sorted(serials))}"
            )
        tables, notes = {}, []
        for path, m in files:
            angle = m.profile.angle
            if angle in tables:
                raise ValueError(f"two {angle} tables: {paths[0].name}, {path.name}")
            tables[angle] = m.profile
            notes.append(f"{angle}: {path.name}, {len(m.profile.points)} points")
        if swmicpkg.PLAIN_ANGLE not in tables:
            path, m = files[0]
            raise ValueError(
                f"no {swmicpkg.PLAIN_ANGLE} table; add the {m.serial}{path.suffix} file"
            )
        m = files[0][1]
        serial = self.serial or m.serial
        data, copies = package(serial, tables)
        return Result(data, f"{serial}.swmicpkg", self._note(m) + notes + copies)


class UmikToSwmicpkg(_MicFilesToSwmicpkg):
    source = "umik"
    description = (
        "UMIK calibration (inputs: 0, optional 90 degrees) as a SoundID "
        "microphone package"
    )

    def _load(self, path: Path):
        return umik.load(path)

    def _note(self, u) -> list[str]:
        return [
            f"{u.model} {u.serial}, sensitivity {u.sensitivity_db:g} dBFS (not stored)"
        ]


class DaytonToSwmicpkg(_MicFilesToSwmicpkg):
    source = "dayton"
    description = (
        "Dayton Audio calibration (inputs: 0, optional 90 degrees) as a SoundID "
        "microphone package"
    )

    def _load(self, path: Path):
        return mic.load(path)

    def _note(self, m) -> list[str]:
        sensitivity = f"{m.sensitivity_db:g} {m.sensitivity_unit}"
        notes = [f"{m.model} {m.serial}, sensitivity {sensitivity} (not stored)"]
        if m.phase:
            notes.append("phase not stored")
        return notes
