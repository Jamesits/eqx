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


class RewcalToSwmicpkg(Converter):
    """The inputs are the 0, 30 and 90 degree tables, resampled to the package grid.

    Both formats hold the microphone's own response, so no inversion.  A
    missing angle gets a copy of the 0 degree table.
    """

    source = "rewcal"
    target = "swmicpkg"
    description = ("microphone calibration (inputs: 0, optional 30 and 90 degrees) as a "
                   "SoundID microphone package")
    options = (Option("--serial", help="microphone serial, the package name "
                                       "(default: the first input name)"),)
    inputs = len(swmicpkg.ANGLES)

    def __init__(self, serial: str | None = None):
        self.serial = serial

    def _convert(self, *paths: Path) -> Result:
        serial = self.serial or paths[0].stem
        grid = swmicpkg.grid()
        logs = [math.log(f) for f in grid]
        profiles, notes = [], []
        for angle, path in zip(swmicpkg.ANGLES, paths):
            table, other = cal.load(path)
            gains = resample([math.log(f) for f, _ in table.points],
                             [g for _, g in table.points], logs)
            profiles.append(MicProfile(serial, angle, list(zip(grid, gains))))
            notes.append(f"{angle}: {path.name}, {len(table.points)} points"
                         + (f"; {len(other)} other lines ignored" if other else ""))
        for angle in swmicpkg.ANGLES[len(paths):]:
            profiles.append(MicProfile(serial, angle, profiles[0].points))
            notes.append(f"{angle}: copy of {swmicpkg.PLAIN_ANGLE}")
        return Result(swmicpkg.write(profiles), f"{serial}.swmicpkg", notes)
