"""TotalMix Room EQ preset."""

from __future__ import annotations

from eqx.model import Correction, Peq
from eqx.rme import tmreq

from .common import SPEAKER

TMREQ_DIR = "rme/tmreq"


def write_tmreq() -> bytes:
    return tmreq.write(
        [
            Correction(side[0], peqs=[Peq(f, g, q) for f, g, q in filters])
            for side, filters in SPEAKER.items()
        ]
    ).encode()


def files() -> dict[str, bytes]:
    return {f"{TMREQ_DIR}/Tilt - Flat.tmreq": write_tmreq()}
