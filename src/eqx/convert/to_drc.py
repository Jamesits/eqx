"""Measurements of other formats -> DRC input impulse response."""

from __future__ import annotations

from pathlib import Path

from ..drc import pcm, run
from ..rew import mdat
from .base import Converter, Result
from .common import (
    CHANNEL_OPTION,
    DEFAULT_RATE,
    RATE_OPTION,
    channel_name,
    drc_input,
    file_name,
    missing,
)
from .to_swproj import MIC_OPTIONS, check_mic_options, flat_mic_profile, mic_profile_of


class MdatToDrc(Converter):
    """The minimum-phase impulse response of one measurement's magnitude."""

    source = "mdat"
    target = "drc"
    description = "a REW speaker measurement as a DRC input impulse response"
    options = MIC_OPTIONS + (CHANNEL_OPTION, RATE_OPTION)

    def __init__(
        self,
        mic_profile: Path | None = None,
        mic_profile_format: str | None = None,
        mic_curve: str | None = None,
        channel: str = "left",
        rate: float = DEFAULT_RATE,
    ):
        check_mic_options(mic_profile, mic_profile_format, mic_curve)
        self.mic = (mic_profile, mic_profile_format, mic_curve)
        self.channel = channel_name(channel)
        if rate <= 0:
            raise ValueError("--rate must be positive")
        self.rate = rate

    def _convert(self, path: Path) -> Result:
        measurements = {m.channel: m for m in mdat.load(path)}
        if self.channel not in measurements:
            raise missing(self.channel, measurements)
        profile, source = mic_profile_of(*self.mic, flat_mic_profile)
        ir = drc_input(measurements[self.channel], profile, self.rate)
        name = file_name(f"{path.stem} {self.channel}") + ".pcm"
        return Result(
            pcm.write(ir),
            name,
            [
                (
                    f"{len(ir)} samples at {self.rate:g} Hz; "
                    f"mic table: {profile.name} {profile.angle} ({source})"
                ),
                (
                    f"run: {run.DEFAULT_EXECUTABLE} --BCInFile=<this file> "
                    f"normal-{self.rate / 1000:.1f}.drc (a configuration of this "
                    "sample rate)"
                ),
            ],
        )
