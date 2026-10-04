"""SoundID microphone package or table, AutoEq CSV -> REW microphone calibration file."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..model import MicProfile
from ..options import Option
from ..rew import cal
from ..soundid import swmic, swmicpkg
from .base import Converter, Result
from .common import COLUMN_OPTION


def _result(profile: MicProfile, application: str, name: str) -> Result:
    points = profile.points
    return Result(
        cal.write(profile, application).encode("utf-8"),
        name,
        [f"{len(points)} points, {points[0][0]:g}-{points[-1][0]:g} Hz"],
    )


class SwmicpkgToRewcal(Converter):
    """Writes one table unchanged: no resampling, no sensitivity line.

    Both formats hold the microphone's own response, so no inversion.
    """

    source = "swmicpkg"
    target = "rewcal"
    description = "one microphone table as a REW calibration file"
    options = (
        Option("--curve", help=f"table to convert (default: {swmicpkg.PLAIN_ANGLE})"),
    )

    def __init__(self, curve: str = swmicpkg.PLAIN_ANGLE):
        self.curve = curve

    def _convert(self, path: Path) -> Result:
        profile = swmicpkg.load(path, self.curve)
        return _result(profile, "SoundID", f"{profile.name} {profile.angle}.txt")


class SwmicToRewcal(Converter):
    """Writes the table unchanged, decrypted."""

    source = "swmic"
    target = "rewcal"
    description = "a SoundID microphone table as a REW calibration file"

    def _convert(self, path: Path) -> Result:
        profile = swmic.load(path).profile
        return _result(profile, "SoundID", f"{profile.name} {profile.angle}.txt")


class AutoeqToRewcal(Converter):
    source = "autoeq"
    target = "rewcal"
    description = "one CSV column as a REW calibration file"
    options = (COLUMN_OPTION,)

    def __init__(self, column: str = response.RAW):
        self.column = column

    def _convert(self, path: Path) -> Result:
        profile = MicProfile.from_points(
            path.stem, "", response.load(path).curve(self.column)
        )
        return _result(profile, "AutoEq", f"{path.stem}.txt")
