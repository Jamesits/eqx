"""Reader for SoundID biquad JSON exports (Fluid Audio, MERGING+ANUBIS ``*.bin``).

Encrypted (see ``export_partners``).  One profile per sample rate; each profile
holds the biquads of every channel.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..correction import Export, ExportInspector, number
from ..dsp import Biquad
from ..fileformat import Format
from ..model import Correction
from ..options import Option
from . import export_partners

ID = "soundid-export-biquad-json"
SERIAL_OPTION = Option("--serial-number",
                       help="MERGING device serial number (default: search every number)")


def read(data: bytes, serial: str | None = None) -> Export:
    plain, partner, key = export_partners.open_export(data, serial)
    if partner.format != ID:
        raise ValueError(f"a {partner.name} export is not a biquad JSON export")
    try:
        root = json.loads(plain)
        rates = {p["id"]: number(p["sample_rate"], "sample_rate")
                 for p in root["profile_configs"]}
        corrections = []
        for config in root["channel_config"]:
            rate = rates[config["profile_id"]]
            for ch in config["channels"]:
                where = f"{ch['type']} {rate:g} Hz"
                biquads = []
                for coefs in ch["coefs"]:
                    if len(coefs) != 6:
                        raise ValueError(f"{where}: a biquad has {len(coefs)} coefficients")
                    biquads.append(Biquad(*(number(c, where) for c in coefs)))
                gain = sum(number(ch[k], where) for k in ("balance_gain", "pre_gain", "post_gain"))
                corrections.append(Correction(ch["type"], gain, number(ch["delay"], where),
                                              rate, biquads))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a biquad JSON export: {exc!r}") from None
    serial = f", serial number {key}" if partner.key is None else ""
    return Export(corrections,
                  [("name", root.get("name")), ("target mode", root.get("target_mode")),
                   ("safe headroom dB", root.get("safe_headroom")),
                   ("sample rates", " ".join(f"{r:g}" for r in rates.values()))],
                  f"AES-256-CBC, {partner.name} key{serial}", plain.decode("utf-8"))


def load(path, serial_number: str | None = None) -> Export:
    return read(Path(path).read_bytes(), serial_number)


class BiquadJsonInspector(ExportInspector):
    load = load
    options = (SERIAL_OPTION,)


FORMAT = Format(ID, (".bin",), "SoundID export: biquad JSON (Fluid Audio, MERGING)",
                BiquadJsonInspector, lambda data: export_partners.partner_format(data) == ID)
