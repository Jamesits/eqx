"""DRC correction filters (``.pcm``)."""

from __future__ import annotations

from eqx.drc import pcm

from .common import bells, impulse_response

PCM_DIR = "drc/pcm"
RATE = 48000
TAPS = 8192
# DRC's filters peak late: its sample configurations delay them by ~20 ms.
DELAY = 960
GAIN_DB = -6.0


def files() -> dict[str, bytes]:
    """A filter per side: the side's speaker bells, a 20 ms delay."""
    return {
        f"{PCM_DIR}/Room {side} filter.pcm": pcm.write(
            impulse_response(TAPS, DELAY, GAIN_DB, bells(side, RATE))
        )
        for side in ("Left", "Right")
    }
