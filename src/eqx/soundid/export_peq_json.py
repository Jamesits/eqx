"""Reader for SoundID parametric EQ JSON exports (Grace Design m908, Lynx Aurora ``*.bin``).

Encrypted (see ``export_partners``).  One list of bell filters per channel; the
device designs the biquads itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..correction import Export, ExportInspector, number
from ..fileformat import Format
from ..model import Correction, Peq
from . import export_partners

ID = "soundid-export-peq-json"
# Grace Design writes "Peak", Lynx "Parametric"; both are bells.
BELL_TYPES = ("Peak", "Parametric")


def read(data: bytes) -> Export:
    plain, encryption = export_partners.open_as(
        data, ID, "parametric EQ JSON export", search=False
    )
    try:
        root = json.loads(plain)
        corrections = []
        for ch in root["channels"]:
            where = ch["name"]
            peqs = []
            for f in ch["peqFilters"]:
                if f["type"] not in BELL_TYPES:
                    raise ValueError(f"{where}: unsupported filter type {f['type']!r}")
                peqs.append(
                    Peq(
                        number(f["frequency"], where),
                        number(f["gain"], where),
                        number(f["qFactor"], where),
                    )
                )
            spot = ch["listeningSpotCompensationData"]
            corrections.append(
                Correction(
                    ch["name"],
                    number(spot["gain"], where),
                    number(spot["delay"], where),
                    peqs=peqs,
                )
            )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a parametric EQ JSON export: {exc!r}") from None
    return Export(
        corrections,
        [
            ("name", root.get("name")),
            ("target mode", root.get("targetMode")),
            ("layout", root.get("layoutType")),
            ("safe headroom dB", root.get("safeHeadroomDb")),
        ],
        encryption,
        plain.decode("utf-8"),
    )


def load(path) -> Export:
    return read(Path(path).read_bytes())


class PeqJsonInspector(ExportInspector):
    load = load


FORMAT = Format(
    ID,
    (".bin",),
    "SoundID export: parametric EQ JSON (Grace Design, Lynx)",
    PeqJsonInspector,
    lambda data: export_partners.partner_format(data) == ID,
)
