"""miniDSP UMIK calibration files -> SoundID microphone package."""

from __future__ import annotations

from pathlib import Path

from ..minidsp import umik
from .base import Converter, Result
from .rewcal_swmicpkg import SERIAL_OPTION, package


class UmikToSwmicpkg(Converter):
    """The inputs are the 0 degree and the optional 90 degree file, in any order.

    Each file's angle comes from its header or ``_90deg`` name.  UMIK has no
    30 degree table; it gets a copy of the 0 degree table.  The sensitivity
    has no place in the package.
    """

    source = "umik"
    target = "swmicpkg"
    description = ("UMIK calibration (inputs: 0, optional 90 degrees) as a SoundID "
                   "microphone package")
    options = (SERIAL_OPTION,)
    inputs = 2

    def __init__(self, serial: str | None = None):
        self.serial = serial

    def _convert(self, *paths: Path) -> Result:
        files = [(path, umik.load(path)) for path in paths]
        serials = {u.serial for _, u in files}
        if len(serials) > 1:
            raise ValueError(f"the inputs are of different microphones: {', '.join(sorted(serials))}")
        tables, notes = {}, []
        for path, u in files:
            angle = u.profile.angle
            if angle in tables:
                raise ValueError(f"two {angle} tables: {paths[0].name}, {path.name}")
            tables[angle] = u.profile
            notes.append(f"{angle}: {path.name}, {len(u.profile.points)} points")
        if umik.PLAIN_ANGLE not in tables:
            raise ValueError(f"no {umik.PLAIN_ANGLE} table; add the {files[0][1].serial}.txt file")
        u = files[0][1]
        serial = self.serial or u.serial
        data, copies = package(serial, tables)
        return Result(data, f"{serial}.swmicpkg",
                      [f"{u.model} {u.serial}, sensitivity {u.sensitivity_db:g} dBFS "
                       "(not stored)"] + notes + copies)
