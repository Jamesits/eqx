"""MultEQ-X project -> SoundID ``.swproj`` speaker project."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ..audyssey import mqx
from ..model import Measurement
from ..soundid import layout
from .mdat_swproj import SpeakerProjectConverter

# MultEQ-X designation -> SoundID channel.  Front and rear heights are
# SoundID's top front and top rear.
CHANNELS = {
    "FL": "L", "FR": "R", "C": "C", "SW1": "LFE", "SLA": "Ls", "SRA": "Rs",
    "SBL": "Lrs", "SBR": "Rrs", "SB": "Cs", "FWL": "Lw", "FWR": "Rw",
    "TFL": "Ltf", "TFR": "Rtf", "TML": "Ltm", "TMR": "Rtm", "TRL": "Ltr", "TRR": "Rtr",
    "FHL": "Ltf", "FHR": "Rtf", "RHL": "Ltr", "RHR": "Rtr",
}


def soundid_layout(designations: list[str]) -> layout.Layout:
    """The SoundID layout with the same speakers as ``designations``."""
    unknown = [d for d in designations if d not in CHANNELS]
    if unknown:
        raise ValueError(f"no SoundID channel for {', '.join(unknown)}; "
                         f"supported: {', '.join(CHANNELS)}")
    shorts = [CHANNELS[d] for d in designations]
    if len(set(shorts)) != len(shorts):
        raise ValueError(f"{', '.join(designations)} map to the same SoundID channel twice")
    for candidate in layout.LAYOUTS.values():
        if sorted(c.short for c in candidate.channels) == sorted(shorts):
            return candidate
    raise ValueError(f"no SoundID layout has the speakers {', '.join(designations)}")


class MqxToSwproj(SpeakerProjectConverter):
    """Every enabled speaker, in the SoundID layout of the same speakers."""

    source = "mqx"
    description = "MultEQ-X speaker responses as a SoundID speaker project (no audio samples)"
    options = SpeakerProjectConverter.options + (mqx.POSITION_OPTION,)

    def __init__(self, position: int | None = None, **settings):
        super().__init__(**settings)
        self.position = position

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        m = mqx.load(path)
        channels = [c for c in m.channels
                    if (c.data.get("Calibration") or {}).get("IsEnabled", True) is not False
                    and any(r.channel == c.guid for r in m.recordings)]
        target = soundid_layout([c.designation for c in channels])
        measurements = []
        for c in channels:
            index = target.index(CHANNELS[c.designation])
            measured = mqx.measurement(m, c.designation, self.position, Path(path).stem)
            measurements.append(dataclasses.replace(
                measured, channel=target.channels[index].name, index=index))
        return target, sorted(measurements, key=lambda x: x.index)
