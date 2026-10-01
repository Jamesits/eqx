"""REW microphone calibration files -> SoundID microphone package."""

from __future__ import annotations

import math
from pathlib import Path

from ..model import MicProfile
from ..options import Option
from ..rew import cal
from ..soundid import swmicpkg
from .base import Converter, Result
from .mdat_swproj import resample

SERIAL_OPTION = Option("--serial", help="microphone serial, the package name (default: the "
                                        "serial in a UMIK file, else the first input name)")


def package(serial: str, tables: dict[str, MicProfile]) -> tuple[bytes, list[str]]:
    """The package of the ``tables`` (angle -> table), each resampled to the package grid.

    A missing angle gets a copy of the degrees_0 table.
    """
    grid = swmicpkg.grid()
    logs = [math.log(f) for f in grid]
    profiles, notes = {}, []
    for angle in swmicpkg.ANGLES:
        if angle in tables:
            table = tables[angle]
            gains = resample([math.log(f) for f, _ in table.points],
                             [g for _, g in table.points], logs)
            profiles[angle] = MicProfile(serial, angle, list(zip(grid, gains)))
        else:
            profiles[angle] = MicProfile(serial, angle, profiles[swmicpkg.PLAIN_ANGLE].points)
            notes.append(f"{angle}: copy of {swmicpkg.PLAIN_ANGLE}")
    return swmicpkg.write(list(profiles.values())), notes


class RewcalToSwmicpkg(Converter):
    """The inputs are the 0, 30 and 90 degree tables, resampled to the package grid.

    Both formats hold the microphone's own response, so no inversion.  A
    missing angle gets a copy of the 0 degree table.
    """

    source = "rewcal"
    target = "swmicpkg"
    description = ("microphone calibration (inputs: 0, optional 30 and 90 degrees) as a "
                   "SoundID microphone package")
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
            notes.append(f"{angle}: {path.name}, {len(table.points)} points"
                         + (f"; {len(other)} other lines ignored" if other else ""))
        data, copies = package(serial, tables)
        return Result(data, f"{serial}.swmicpkg", notes + copies)
