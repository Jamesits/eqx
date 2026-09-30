"""Reader for RME TotalMix FX Room EQ presets (``*.tmreq``).

Pseudo-XML: element names contain spaces, so it is not XML::

    <Preset>
        <Room EQ L>
            <Params>
                <val e="REQ Delay" v="0.00,"/>
                <val e="REQ Band1 Freq" v="50.00,"/>  (Q, Gain; bands 1-9)
                <val e="REQ Band1Type" v="0.00,"/>    (also "REQ Band8 Type", "REQ Band9 Type")
                <val e="Chan Gain" v="0.00,"/>
            </Params>
        </Room EQ L>
        <Room EQ R>...</Room EQ R>
    </Preset>

A value ends with a comma.  RME publishes no specification; see
https://forum.rme-audio.de/viewtopic.php?id=43057.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..correction import Export, ExportInspector, number
from ..fileformat import Format
from ..model import Correction, Peq

BANDS = 9
# Only these bands have a type.  "Band1Type" has no space; RME keeps the
# spelling for compatibility, and SoundID writes it too.
TYPE_NAMES = {1: "REQ Band1Type", 8: "REQ Band8 Type", 9: "REQ Band9 Type"}
# Type codes as reported on the RME forum, not confirmed by RME.  A shelf is
# a low shelf on band 1 and a high shelf on bands 8 and 9.
BELL, SHELF, LOW_PASS, HIGH_PASS = 0, 1, 2, 3
_CHANNEL = re.compile(r"<Room EQ ([^>]+)>(.*?)</Room EQ \1>", re.S)
_VAL = re.compile(r'<val e="([^"]*)" v="([^"]*)"/>')


def _kind(band: int, code: float, where: str) -> str:
    if code == BELL:
        return "bell"
    if code == SHELF:
        return "low-shelf" if band == 1 else "high-shelf"
    if code == LOW_PASS:
        return "low-pass"
    if code == HIGH_PASS:
        return "high-pass"
    raise ValueError(f"{where}: unknown band {band} type {code:g}")


def read(text: str) -> Export:
    if "<Preset>" not in text:
        raise ValueError("not a TotalMix Room EQ preset")
    corrections = []
    for name, body in _CHANNEL.findall(text):
        values = {k: v.split(",")[0] for k, v in _VAL.findall(body)}

        def get(key: str) -> float:
            if key not in values:
                raise ValueError(f"{name}: missing {key!r}")
            return number(values[key], f"{name} {key}")

        peqs = []
        for band in range(1, BANDS + 1):
            kind = "bell"
            if band in TYPE_NAMES and TYPE_NAMES[band] in values:
                kind = _kind(band, get(TYPE_NAMES[band]), name)
            prefix = f"REQ Band{band} "
            peqs.append(Peq(get(prefix + "Freq"), get(prefix + "Gain"), get(prefix + "Q"), kind))
        corrections.append(Correction(name, get("Chan Gain"), get("REQ Delay"), peqs=peqs))
    if not corrections:
        raise ValueError("no Room EQ channel in the TotalMix preset")
    return Export(corrections)


def load(path) -> Export:
    return read(Path(path).read_text(encoding="utf-8-sig"))


class TmreqInspector(ExportInspector):
    load = load


FORMAT = Format("tmreq", (".tmreq",), "RME TotalMix FX Room EQ preset", TmreqInspector)
