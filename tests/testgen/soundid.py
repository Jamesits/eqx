"""SoundID microphone packages and tables, headphone profiles (PEQb), Custom Target Presets and
device exports."""

from __future__ import annotations

import json
import struct

from eqx import dsp
from eqx.model import MicProfile
from eqx.soundid import crypto, export_lvnd, export_partners, peqb, swmicpkg

from .common import (
    ANGLES,
    COMPUTER_ID,
    SPEAKER,
    ZERO_IV,
    bells,
    high_cut,
    hp1,
    normalized,
    peak,
    response,
    rounded_points,
)

PROJ_DIR, MIC_DIR, PEQB_DIR, PRESET_DIR = (
    "soundid/swproj",
    "soundid/swmicpkg",
    "soundid/peqb",
    "soundid/targetpreset",
)
BIQUAD_JSON_DIR = "soundid/soundid-export-biquad-json"
PEQ_JSON_DIR = "soundid/soundid-export-peq-json"
BIQUAD_XML_DIR = "soundid/soundid-export-biquad-xml"
LVND_DIR = "soundid/soundid-export-lvnd"
EXPORT_TXT_DIR = "soundid/soundid-export-txt"
SWMIC_DIR = "soundid/swmic"


# ---------------------------------------------------------------------------
# microphone packages
# ---------------------------------------------------------------------------
MICS = {
    "FLAT01": {a: [] for a in ANGLES},
    "TILT01": {
        "degrees_0": [hp1(10), peak(12000, 3, 0.7)],
        "degrees_30": [hp1(10), peak(12000, 1.5, 0.7)],
        "degrees_90": [hp1(10), high_cut(8000, -3)],
    },
}


def _mic_profile(serial: str, angle: str, sections) -> MicProfile:
    return MicProfile(
        serial, angle, [(f, response(sections, f)[0]) for f in swmicpkg.grid()]
    )


def write_swmicpkg(serial: str, tables: dict) -> bytes:
    return swmicpkg.write(
        [_mic_profile(serial, angle, sections) for angle, sections in tables.items()]
    )


def swmic_files(serial: str, tables: dict) -> dict[str, bytes]:
    """The table files of a downloaded profile: degrees_0 plain, the others
    encrypted with a zero IV."""
    out = {}
    for angle, sections in tables.items():
        degrees = angle.removeprefix("degrees_")
        text = swmicpkg.table(_mic_profile(serial, angle, sections)).encode()
        if angle == swmicpkg.PLAIN_ANGLE:
            out[f"{SWMIC_DIR}/{serial}_cal_{degrees}degree.txt"] = text
        else:
            name = f"{SWMIC_DIR}/{serial}_cal_Sonarworks_{degrees}degree.swmic"
            out[name] = crypto.encrypt(swmicpkg.PACKAGE_KEY, text, ZERO_IV)
    return out


# ---------------------------------------------------------------------------
# PEQb headphone profiles
# ---------------------------------------------------------------------------
def write_peqb_v1(
    curves, parameters: dict, version: tuple, computer_id: str | None
) -> bytes:
    """PEQb 3.0.0.0/3.0.0.1, encrypted with ``computer_id`` unless it is None."""
    body = peqb.body_v1(curves, parameters)
    header = peqb.MAGIC + bytes(version)
    if version == (3, 0, 0, 0):
        return header + body
    if computer_id is None:
        return header + b"\x00" + body
    return (
        header
        + b"\x01"
        + crypto.encrypt(crypto.swhp_key(computer_id), peqb.BODY_HEADER + body, ZERO_IV)
    )


def headphone_curves(sections, band_db: float):
    correction = rounded_points(sections)
    return peqb.headphone_curves(correction, correction, band_db)


TILT_HEADPHONE = [peak(60, 4, 0.7), peak(3000, -3, 2), peak(9000, 5, 3)]
SWHP = {
    # name: (sections, make, model, average, error band, version, computer ID)
    "Flat Flat Wired": ([], "Flat", "Flat", False, 0.9, (3, 0, 0, 1), COMPUTER_ID),
    "Flat Flat Wired 3.0.0.1 plain": (
        [],
        "Flat",
        "Flat",
        False,
        0.9,
        (3, 0, 0, 1),
        None,
    ),
    "Flat Flat Wired 3.0.0.0": ([], "Flat", "Flat", False, 0.9, (3, 0, 0, 0), None),
    "Tilt Tilt Wired Average": (
        TILT_HEADPHONE,
        "Tilt",
        "Tilt",
        True,
        3.0,
        (3, 0, 0, 1),
        COMPUTER_ID,
    ),
}


# ---------------------------------------------------------------------------
# Custom Target Presets
# ---------------------------------------------------------------------------
def _filters(*specs):
    return [
        {
            "colorId": i,
            "enabled": enabled,
            "frequency": float(f),
            "gain": float(g),
            "id": i + 1,
            "q": float(q),
            "type": t,
        }
        for i, (t, f, g, q, enabled) in enumerate(specs)
    ]


FLAT_FILTERS = _filters(
    ("low-shelf", 50, 0, 1, True),
    ("bell", 700, 0, 1, True),
    ("high-shelf", 8000, 0, 1, True),
)


def _preset(name, enabled, flip, low, high, filters):
    return {
        "filterGroups": [
            {
                "cutoff": {
                    "enabled": enabled,
                    "flipState": flip,
                    "leftFreq": float(low),
                    "rightFreq": float(high),
                },
                "filters": filters,
            }
        ],
        "name": name,
    }


PRESETS = [
    _preset("Flat", True, "inner", 20, 20000, FLAT_FILTERS),
    _preset(
        "Bass and treble",
        True,
        "inner",
        30,
        16000,
        _filters(
            ("low-shelf", 120, 4, 0.7, True),
            ("bell", 2000, -1.5, 1.4, True),
            ("bell", 5000, 2, 3, False),
            ("high-shelf", 8000, -2, 1, True),
        ),
    ),
    _preset("Mid band outer", True, "outer", 1500, 4000, FLAT_FILTERS),
    _preset("Correction off", False, "inner", 20, 20000, FLAT_FILTERS),
]


# ---------------------------------------------------------------------------
# device exports
# ---------------------------------------------------------------------------
EXPORT_NAME = "Tilt"
MERGING_SERIAL = "A000042"
# SoundID writes this truncated GUID.
EXPORT_GUID = "00000000-0000-0000-0000-0000"
# The bands of SoundID's graphic EQ export.
THIRD_OCTAVES = (
    40,
    50,
    63,
    80,
    100,
    125,
    160,
    200,
    250,
    315,
    400,
    500,
    630,
    800,
    1000,
    1250,
    1600,
    2000,
    2500,
    3150,
    4000,
    5000,
    6300,
    8000,
    10000,
    12500,
    16000,
)
LVND_SIZE = 4096


def _partner_key(partner_id: str) -> str:
    return next(p for p in export_partners.PARTNERS if p.id == partner_id).key


def _export_json(obj) -> bytes:
    return json.dumps(obj, indent=4, sort_keys=True).encode()


def write_biquad_json(rates, key: str) -> bytes:
    root = {
        "channel_config": [
            {
                "channels": [
                    {
                        "balance_gain": 0.0,
                        "coefs": [normalized(b) for b in bells(side, r)],
                        "delay": 0.0,
                        "post_gain": 0.0,
                        "pre_gain": 0.0,
                        "type": side,
                    }
                    for side in SPEAKER
                ],
                "profile_id": i + 1,
            }
            for i, r in enumerate(rates)
        ],
        "name": EXPORT_NAME,
        "profile_configs": [
            {"dsp_type": "Biquad", "id": i + 1, "sample_rate": str(r)}
            for i, r in enumerate(rates)
        ],
        "safe_headroom": -12.0,
        "target_mode": "Flat",
    }
    return export_partners.encrypt(key, _export_json(root), ZERO_IV)


def write_peq_json(filter_type: str, key: str) -> bytes:
    root = {
        "channels": [
            {
                "guid": EXPORT_GUID,
                "listeningSpotCompensationData": {"delay": 0.0, "gain": 0.0},
                "name": side[0],
                "peqFilters": [
                    {
                        "frequency": f,
                        "gain": float(g),
                        "qFactor": float(q),
                        "type": filter_type,
                    }
                    for f, g, q in SPEAKER[side]
                ],
            }
            for side in SPEAKER
        ],
        "layoutType": "2.0 (Stereo)",
        "name": EXPORT_NAME,
        "safeHeadroomDb": -12.0,
        "targetMode": "Flat",
    }
    return export_partners.encrypt(key, _export_json(root), ZERO_IV)


def write_biquad_xml(rates) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<roomCorrection>"]
    for i, r in enumerate(rates):
        lines += [
            (
                f'\t<profile id="{i}" type="biquad" sampleRate="{r}" '
                f'friendlyName="{EXPORT_NAME}">'
            ),
            "\t\t<tags/>",
            '\t\t<listeningSpotOptimisation enabled="1"/>',
        ]
        for side in SPEAKER:
            lines += [
                f'\t\t<channel friendlyName="{side}">',
                '\t\t\t<delay ms="0"/>',
                '\t\t\t<makeupGain dB="0"/>',
            ]
            for n, bq in enumerate(bells(side, r)):
                b0, b1, b2, a0, a1, a2 = normalized(bq)
                lines.append(
                    f'\t\t\t<biquad idx="{n}" a0="{a0:.17g}" a1="{a1:.17g}" '
                    f'a2="{a2:.17g}" b0="{b0:.17g}" b1="{b1:.17g}" b2="{b2:.17g}"/>'
                )
            lines.append("\t\t</channel>")
        lines.append("\t</profile>")
    lines.append("</roomCorrection>")
    return export_partners.encrypt(
        _partner_key("adam"), ("\n".join(lines) + "\n\n").encode(), ZERO_IV
    )


def _f32_bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _lvnd_message(index: int, fields: list[int]) -> bytes:
    check = 0
    for v in fields:
        check ^= v
    body = b"".join(export_lvnd.encode_field(v) for v in [*fields, check])
    return (
        bytes([export_lvnd.STX, ord("0") + index % 10])
        + body
        + bytes([export_lvnd.ETX])
    )


def write_lvnd(side: str, gain_db: float, delay_ms: float) -> bytes:
    """Parameter addresses as SoundID writes them."""
    messages = []
    for n, bq in enumerate(bells(side, export_lvnd.SAMPLE_RATE)):
        b0, b1, b2, _a0, a1, a2 = normalized(bq)
        messages.append(
            _lvnd_message(
                n,
                [
                    export_lvnd.BIQUAD,
                    ((30000 + n) << 12) | 0xB,
                    0,
                    2048,
                    5,
                    *map(_f32_bits, (b0, b1, b2, a1, a2)),
                ],
            )
        )
    n = len(messages)
    messages.append(
        _lvnd_message(
            n, [export_lvnd.GAIN, (31000 << 12) | 8, _f32_bits(gain_db), 256, 0]
        )
    )
    samples = round(delay_ms * export_lvnd.SAMPLE_RATE / 1000)
    messages.append(
        _lvnd_message(n + 1, [export_lvnd.DELAY, (30500 << 12) | 9, samples, 512, 0])
    )
    body = b"".join(messages)
    data = export_lvnd.HEADER.pack(export_lvnd.MAGIC, 0, 0, len(body)) + body
    return data + bytes(LVND_SIZE - len(data))


def _text_export(tables: dict[str, list[str]]) -> bytes:
    lines = [
        f"Preset name: {EXPORT_NAME}",
        f"Profile name: {EXPORT_NAME}",
        "Target mode: Flat",
        "Audio setup: 2.0 (Stereo)",
        "",
    ]
    for side, rows in tables.items():
        lines += [
            f"{side[0]} channel calibration:",
            "Delay: 0 ms",
            "Gain: 0 dB",
            "",
            *rows,
            "",
        ]
    return "\n".join(lines).encode()


def _cells(*cells: tuple[str, int]) -> str:
    return "|" + "|".join(f"{text:<{width}}" for text, width in cells) + "|"


def write_graphic_text() -> bytes:
    tables = {}
    for side in SPEAKER:
        rows = [
            _cells(("Freq", 10), ("Gain", 10)),
            _cells((":" + "-" * 9, 10), (":" + "-" * 9, 10)),
        ]
        for f in THIRD_OCTAVES:
            g = round(dsp.cascade_db(bells(side), f, dsp.PEQ_SAMPLE_RATE), 1) + 0.0
            rows.append(_cells((f"{f:<8g}Hz", 10), (f"{g:<8g}dB", 10)))
        tables[side] = rows
    return _text_export(tables)


def write_peq_text() -> bytes:
    tables = {}
    for side in SPEAKER:
        rows = [
            _cells(("Type", 20), ("Freq", 10), ("Gain", 10), ("Q", 10)),
            _cells((":" + "-" * 19, 20), *[(":" + "-" * 9, 10)] * 3),
        ]
        for n, (f, g, q) in enumerate(SPEAKER[side], 1):
            rows.append(
                _cells(
                    (f"Parametric Eq {n}", 20),
                    (f"{f:<8g}Hz", 10),
                    (f"{g:<8g}dB", 10),
                    (f"{q:g}", 10),
                )
            )
        tables[side] = rows
    return _text_export(tables)


def files() -> dict[str, bytes]:
    out = {
        f"{MIC_DIR}/{serial}.swmicpkg": write_swmicpkg(serial, tables)
        for serial, tables in MICS.items()
    }
    out.update(swmic_files("TILT01", MICS["TILT01"]))
    for name, (sections, make, model, average, band, version, cid) in SWHP.items():
        out[f"{PEQB_DIR}/{name}.swhp"] = write_peqb_v1(
            headphone_curves(sections, band),
            peqb.headphone_parameters(make, model, average, band),
            version,
            cid,
        )
    for preset in PRESETS:
        out[f"{PRESET_DIR}/{preset['name']}.json"] = json.dumps(
            preset, sort_keys=True, separators=(",", ":")
        ).encode()
    out.update(
        {
            f"{BIQUAD_JSON_DIR}/Tilt Fluid.bin": write_biquad_json(
                (96000, 192000), _partner_key("fluid")
            ),
            f"{BIQUAD_JSON_DIR}/Tilt MERGING.bin": write_biquad_json(
                (44100, 48000), MERGING_SERIAL
            ),
            f"{PEQ_JSON_DIR}/Tilt Grace.bin": write_peq_json(
                "Peak", _partner_key("grace-design")
            ),
            f"{PEQ_JSON_DIR}/Tilt Lynx.bin": write_peq_json(
                "Parametric", _partner_key("lynx-aurora")
            ),
            f"{BIQUAD_XML_DIR}/Tilt.adam": write_biquad_xml((44100, 48000)),
            f"{LVND_DIR}/Tilt_192000_Left.bin": write_lvnd("Left", -1.5, 0.5),
            f"{LVND_DIR}/Tilt_192000_Right.bin": write_lvnd("Right", 0.0, 0.0),
            f"{EXPORT_TXT_DIR}/Tilt - Flat.txt": write_graphic_text(),
            f"{EXPORT_TXT_DIR}/Tilt - Flat - 8 - PEQ.txt": write_peq_text(),
        }
    )
    return out
