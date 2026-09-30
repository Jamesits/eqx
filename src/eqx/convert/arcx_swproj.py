"""ARC X session or analysis -> SoundID ``.swproj`` speaker project."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ..ik import arcx
from ..model import Measurement
from ..soundid import layout
from .mdat_swproj import SpeakerProjectConverter

# ARC X layout id -> SoundID layout id, and the SoundID channel of each ARC X
# channel.  ARC X's 4.0 rears and 5.1 rear surrounds are SoundID's surrounds.
LAYOUTS = {
    0: (0, ("L", "R")),
    1: (0, ("L", "R")),
    2: (1, ("L", "R", "LFE")),
    3: (6, ("L", "R", "Ls", "Rs")),
    4: (11, ("L", "R", "C", "LFE", "Ls", "Rs")),
    7: (14, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs")),
    5: (16, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Ltf", "Rtf", "Ltr", "Rtr")),
    6: (17, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Ltf", "Rtf", "Ltm", "Rtm",
             "Ltr", "Rtr")),
    8: (20, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Lw", "Rw", "Ltf", "Rtf", "Ltm",
             "Rtm", "Ltr", "Rtr")),
}


class ArcxToSwproj(SpeakerProjectConverter):
    """Every speaker, in the SoundID layout of the same speakers."""

    source = "arcx"
    description = "ARC X speaker responses as a SoundID speaker project (no audio samples)"
    options = SpeakerProjectConverter.options + (arcx.POINT_OPTION,)

    def __init__(self, point: int | None = None, **settings):
        super().__init__(**settings)
        self.point = point

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        a = arcx.load(path)
        if a.layout is None:
            raise ValueError(f"unknown ARC X layout of {len(a.channels)} channels; "
                             "the SoundID layout needs the session or the analysis layout name")
        target_id, shorts = LAYOUTS[a.layout]
        target = layout.LAYOUTS[target_id]
        measurements = []
        for speaker, short in zip(a.speakers, shorts):
            index = target.index(short)
            m = arcx.measurement(a, speaker, self.point, Path(path).stem)
            measurements.append(dataclasses.replace(
                m, channel=target.channels[index].name, index=index))
        return target, sorted(measurements, key=lambda m: m.index)
