"""Curves of other formats -> AutoEq frequency response CSV (``frequency,raw``).

One file holds one curve, so stereo sources are converted one channel at a time.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..autoeq import response
from ..fileformat import frequency_range
from ..options import Option
from ..rew import mdat
from ..soundid import peqb, swproj, targetpreset
from .base import Converter, Result
from .mdat_swproj import standard_grid

CHANNELS = ("left", "right")
CHANNEL_OPTION = Option("--channel", choices=CHANNELS, help="channel to convert (default: left)")


def _channel_name(channel: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}")
    return channel.capitalize()


def _result(points, name: str, *notes: str) -> Result:
    return Result(response.write(points).encode("utf-8"), name,
                  [f"{len(points)} points, {frequency_range(points)}", *notes])


def _missing(channel: str, available) -> ValueError:
    return ValueError(f"no {channel} channel; available: {', '.join(available) or 'none'}")


class MdatToAutoeq(Converter):
    source = "mdat"
    target = "autoeq"
    description = "one channel of a REW measurement, unresampled"
    options = (CHANNEL_OPTION,)

    def __init__(self, channel: str = "left"):
        self.channel = _channel_name(channel)

    def convert(self, path: Path) -> Result:
        path = Path(path)
        measurements = {m.channel: m for m in mdat.load(path)}
        if self.channel not in measurements:
            raise _missing(self.channel, measurements)
        m = measurements[self.channel]
        return _result(list(zip(m.frequencies, m.response)), f"{path.stem} {self.channel}.csv",
                       f"{self.channel} measurement {m.name!r}, dB SPL")


class SwprojToAutoeq(Converter):
    source = "swproj"
    target = "autoeq"
    description = "the measurement curve of one channel of a SoundID project"
    options = (
        CHANNEL_OPTION,
        Option("--password", help="project password (or SWPROJ_PASSWORD)"),
    )

    def __init__(self, channel: str = "left", password: str | None = None):
        self.channel = _channel_name(channel)
        password = password or os.environ.get("SWPROJ_PASSWORD")
        self.password = password.encode() if password else None

    def convert(self, path: Path) -> Result:
        path = Path(path)
        curves = swproj.measurement_curves(swproj.SwProj.open(path, self.password))
        if self.channel not in curves:
            raise _missing(self.channel, curves)
        points = [(f, r) for f, r, _ in curves[self.channel]]
        return _result(points, f"{path.stem} {self.channel}.csv",
                       f"{self.channel} measurement, dB relative to the project reference")


class PeqbToAutoeq(Converter):
    source = "peqb"
    target = "autoeq"
    description = "the response of one side of a SoundID profile (headphone: -correction)"
    options = (
        CHANNEL_OPTION,
        Option("--computer-id",
               help="computer ID the profile was downloaded for (or SWHP_COMPUTER_ID; "
                    "default: this machine's ID)"),
        Option("--key", help="raw AES body key, hex"),
    )

    def __init__(self, channel: str = "left", computer_id: str | None = None,
                 key: str | None = None):
        self.channel = _channel_name(channel)
        peqb.key_for(computer_id, key)
        self.computer_id = computer_id
        self.key = key

    def convert(self, path: Path) -> Result:
        path = Path(path)
        p = peqb.open_decoded(path.read_bytes(), self.computer_id, self.key)
        side = self.channel
        # A project export holds the measurement; a headphone profile only the
        # correction, which is the inverse of the response.
        for c in p.curves:
            if (c.type_name == f"Measurement{side}"
                    or (c.type_name == "Measurement" and c.parameters.get("ChannelName") == side)):
                return _result([(f, r) for f, r, _ in c.points], f"{path.stem} {side}.csv",
                               f"{side} measurement")
        for c in p.curves:
            if c.type_name == f"Correction{side}":
                return _result([(f, -r) for f, r, _ in c.points], f"{path.stem} {side}.csv",
                               f"{side} response = -{c.type_name}")
        raise ValueError(f"no {side} measurement or correction curve; curves: "
                         f"{', '.join(c.type_name for c in p.curves) or 'none'}")


class TargetpresetToAutoeq(Converter):
    source = "targetpreset"
    target = "autoeq"
    description = "the target curve of a Custom Target Preset, on the standard 355-point grid"

    def convert(self, path: Path) -> Result:
        path = Path(path)
        preset = targetpreset.load(path)
        grid = standard_grid()
        points = list(zip(grid, targetpreset.target_response(preset, grid)))
        filters = sum(f.enabled for g in preset.filter_groups for f in g.filters)
        return _result(points, f"{path.stem}.csv",
                       f"{filters} enabled filters; the correction band is not part of the curve")
