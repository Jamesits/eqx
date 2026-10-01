"""SoundSource Headphone EQ profile."""

from __future__ import annotations

from eqx.model import Correction, Peq
from eqx.rogueamoeba import soundsource

from .common import SPEAKER

SOUNDSOURCE_DIR = "rogueamoeba/soundsource"


def write_soundsource() -> bytes:
    return soundsource.write(
        Correction(
            soundsource.CHANNEL,
            -1.5,
            peqs=[Peq(f, g, q) for f, g, q in SPEAKER["Left"]],
        )
    ).encode()


def files() -> dict[str, bytes]:
    return {f"{SOUNDSOURCE_DIR}/Tilt - Flat.txt": write_soundsource()}
