"""ARC 4 analysis -> SoundID ``.swproj`` speaker project."""

from __future__ import annotations

from pathlib import Path

from ..ik import arc4
from ..model import Measurement
from ..soundid import layout
from .mdat_swproj import SpeakerProjectConverter


class Arc4ToSwproj(SpeakerProjectConverter):
    """Left and right, stereo.  No group delay: ARC 4 keeps the magnitude only."""

    source = "arc4"
    description = "ARC 4 speaker responses as a SoundID speaker project (no audio samples)"

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        a = arc4.load(path)
        return layout.STEREO, [arc4.measurement(a, channel, Path(path).stem)
                               for channel in arc4.CHANNELS]
