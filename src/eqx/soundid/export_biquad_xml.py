"""Reader for SoundID biquad XML exports (ADAM Audio A Series ``*.adam``).

Encrypted (see ``export_partners``).  ``roomCorrection`` holds one ``profile`` per
sample rate with the biquads of every channel.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..correction import Export, ExportInspector, number
from ..dsp import Biquad
from ..fileformat import Format
from ..model import Correction
from . import export_partners

ID = "soundid-export-biquad-xml"
COEFFICIENTS = ("b0", "b1", "b2", "a0", "a1", "a2")


def _attr(parent: ET.Element, child: str, name: str) -> str | None:
    elem = parent.find(child)
    return None if elem is None else elem.get(name)


def read(data: bytes) -> Export:
    plain, encryption = export_partners.open_as(
        data, ID, "biquad XML export", search=False
    )
    root = ET.fromstring(plain)
    if root.tag != "roomCorrection":
        raise ValueError(f"not a biquad XML export: root element {root.tag!r}")
    corrections, rates, name = [], [], ""
    for profile in root.findall("profile"):
        rate = number(profile.get("sampleRate"), "profile sampleRate")
        rates.append(rate)
        name = profile.get("friendlyName", name)
        for ch in profile.findall("channel"):
            where = f"{ch.get('friendlyName')} {rate:g} Hz"
            biquads = [
                Biquad(*(number(b.get(k), where) for k in COEFFICIENTS))
                for b in sorted(
                    ch.findall("biquad"), key=lambda b: number(b.get("idx"), where)
                )
            ]
            corrections.append(
                Correction(
                    ch.get("friendlyName", ""),
                    number(_attr(ch, "makeupGain", "dB"), where),
                    number(_attr(ch, "delay", "ms"), where),
                    rate,
                    biquads,
                )
            )
    spot = root.find("profile/listeningSpotOptimisation")
    return Export(
        corrections,
        [
            ("name", name),
            ("sample rates", " ".join(f"{r:g}" for r in rates)),
            (
                "listening spot optimisation",
                spot.get("enabled") if spot is not None else None,
            ),
        ],
        encryption,
        plain.decode("utf-8"),
    )


def load(path) -> Export:
    return read(Path(path).read_bytes())


class BiquadXmlInspector(ExportInspector):
    load = load


FORMAT = Format(
    ID, (".adam",), "SoundID export: biquad XML (ADAM Audio)", BiquadXmlInspector
)
