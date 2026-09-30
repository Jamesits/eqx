"""ARC X session or analysis -> SoundID ``.swproj`` speaker project."""

from __future__ import annotations

from pathlib import Path

from ..ik import arcx
from ..model import Measurement
from .mdat_swproj import SpeakerProjectConverter


class ArcxToSwproj(SpeakerProjectConverter):
    """The Left and Right speakers; other speakers are left out."""

    source = "arcx"
    description = "ARC X Left and Right responses as a SoundID speaker project (no audio samples)"
    options = SpeakerProjectConverter.options + (arcx.POINT_OPTION,)

    def __init__(self, point: int | None = None, **settings):
        super().__init__(**settings)
        self.point = point

    def measurements(self, path: Path) -> list[Measurement]:
        a = arcx.load(path)
        return [arcx.measurement(a, s, self.point, Path(path).stem)
                for s in ("Left", "Right") if s in a.speakers]
