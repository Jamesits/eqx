"""SoundID microphone package table -> REW microphone calibration file."""

from __future__ import annotations

from pathlib import Path

from ..options import Option
from ..rew import cal
from ..soundid import swmicpkg
from .base import Converter, Result


class SwmicpkgToRewcal(Converter):
    """Writes one table unchanged: no resampling, no sensitivity line.

    Both formats hold the microphone's own response, so no inversion.
    """

    source = "swmicpkg"
    target = "rewcal"
    description = "one microphone table as a REW calibration file"
    options = (
        Option("--angle", help=f"table to convert (default: {swmicpkg.PLAIN_ANGLE})"),
    )

    def __init__(self, angle: str = swmicpkg.PLAIN_ANGLE):
        self.angle = angle

    def convert(self, path: Path) -> Result:
        profile = swmicpkg.load(path, self.angle)
        points = profile.points
        return Result(
            cal.write(profile, "SoundID").encode("utf-8"),
            f"{profile.name} {profile.angle}.txt",
            [f"{len(points)} points, {points[0][0]:g}-{points[-1][0]:g} Hz"],
        )
