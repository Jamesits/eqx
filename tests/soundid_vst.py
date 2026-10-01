"""Compare eqx's FIR of SoundID profiles with the SoundID Reference VST3 plug-in.

    uv run --with pedalboard --with numpy python tests/soundid_vst.py PROFILE.swhp|.swproj ...
        [--out DIR] [--rate HZ] [--tolerance-db DB] [--safe-headroom] [--listening-spot]
        [--limit-correction DB] [--limit-low NAME] [--limit-high NAME]

Windows only; needs SoundID Reference with a license and a signed-in user.
The plug-in is loaded headless; the profile is selected through the
plug-in state.  For each profile and filter type it records the impulse
response and compares its gain (20 Hz-20 kHz) and peak sample with
``PeqbToFir`` / ``SwprojToFir`` with the same settings.  Safe headroom and
listening spot are off unless given.  Both filters are written to ``--out`` as FIR WAVs.
Exit status 1 if a gain differs by more than ``--tolerance-db`` or a peak
sample differs.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pedalboard

from eqx.convert.to_fir import PeqbToFir, SwprojToFir
from eqx.soundid import playback
from eqx.wav import fir

PLUGIN = Path(
    os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files"),
    "VST3",
    "SoundID Reference VST3 Plugin.vst3",
)
# The plug-in keeps its presets here too; it rewrites the file on every load.
GLOBAL_CONFIG = Path(
    os.environ.get("LOCALAPPDATA", ""),
    "Sonarworks",
    "SoundID Reference",
    "Plugin",
    "cfg.json",
)
ROOT = Path(__file__).resolve().parent.parent
# Plug-in preset filterType -> eqx phase.
FILTER_TYPES = {2: "minimum", 3: "linear"}
FLAT_TARGET = 3
BAND_HZ = (20.0, 20000.0)
NUL = b"\x00"


# --------------------------------------------------------------------------
# plug-in state
# --------------------------------------------------------------------------
# JUCE MemoryBlock base64: "<size>." then 6 bits per character, low bits first.
_B64 = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"


def _b64_decode(text: str) -> bytes:
    size, _, body = text.partition(".")
    out = bytearray()
    for i in range(0, len(body), 4):  # 4 characters = 24 bits = 3 bytes
        value = sum(_B64.index(ch) << (6 * k) for k, ch in enumerate(body[i : i + 4]))
        out += value.to_bytes(3, "little")
    return bytes(out[: int(size)])


def _b64_encode(data: bytes) -> str:
    chars = (len(data) * 8 + 5) // 6
    padded = data + bytes(-len(data) % 3)
    out = []
    for i in range(0, len(padded), 3):
        value = int.from_bytes(padded[i : i + 3], "little")
        out += [_B64[(value >> (6 * k)) & 63] for k in range(4)]
    return f"{len(data)}." + "".join(out[:chars])


def _xml_block(xml: bytes) -> bytes:
    """JUCE binary XML: ``VC2!``, u32 size, text, NUL."""
    body = xml + NUL
    return b"VC2!" + len(body).to_bytes(4, "little") + body


def _set_state(plugin, root_attrs: dict, extra: dict) -> None:
    """Change the attributes of the plug-in's own state XML.

    Layout: VST3PluginState XML > IComponent (JUCE base64) > VST2 fxb chunk
    (``CcnK`` size at 0x14, chunk size at 0xAC, both big endian) > JUCE
    binary XML at 0xB0, then JUCE's private data.
    """
    raw = plugin.raw_state
    outer = raw[8:].rstrip(NUL).decode()
    span = re.search(r"<IComponent>(.*?)</IComponent>", outer, re.DOTALL).span(1)
    comp = _b64_decode(outer[span[0] : span[1]])
    xml_size = int.from_bytes(comp[0xB4:0xB8], "little")
    chunk_size = int.from_bytes(comp[0xAC:0xB0], "big")
    root = ET.fromstring(comp[0xB8 : 0xB8 + xml_size].rstrip(NUL))
    for key, value in root_attrs.items():
        root.set(key, str(value))
    for key, value in extra.items():
        root.find("extra").set(key, str(value))
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?> '
        + ET.tostring(root, encoding="unicode").encode()
    )
    chunk = _xml_block(xml) + comp[0xB8 + xml_size : 0xB0 + chunk_size]
    new = (
        bytearray(comp[:0xAC])
        + len(chunk).to_bytes(4, "big")
        + chunk
        + comp[0xB0 + chunk_size :]
    )
    new[0x14:0x18] = (len(new) - 0x18).to_bytes(4, "big")
    outer = outer[: span[0]] + _b64_encode(bytes(new)) + outer[span[1] :]
    plugin.raw_state = _xml_block(outer.encode())


def _pump(seconds: float) -> None:
    """Dispatch this thread's window messages: the plug-in applies its state on them."""
    user32 = ctypes.windll.user32
    msg = ctypes.wintypes.MSG()
    end = time.time() + seconds
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def load(profile: Path, filter_type: int, **preset_values):
    """The plug-in with ``profile`` loaded: calibration on, flat target, no headroom.

    ``preset_values`` override preset fields, e.g. ``limitControlsCalibration``.
    """
    plugin = pedalboard.load_plugin(str(PLUGIN))
    # A preset in the global config with the same id wins over the state.
    preset_id = time.time_ns() // 1_000_000
    headphone = profile.suffix.lower() in (".swhp", ".eqb")
    preset = {
        "id": preset_id,
        "name": "eqx",
        "userChangedPresetName": False,
        "srEnabled": True,
        "targetMode": FLAT_TARGET,
        "virtualMonitoringTargetMode": FLAT_TARGET,
        "calibrationProfile": json.dumps(
            {
                "calibrationMode": "Headphone" if headphone else "Speaker",
                "mode": "1",
                "path": str(profile),
            }
        ),
        "filterType": filter_type,
        "customLinearity": 0,
        "headroomEnabled": False,
        "headroomMode": 0,
        "dryWet": 100,
        "gainValue": 0.0,
        "muted": False,
        "channelCount": 2,
        "delayCompensationEnabled": False,
        "listeningSpotEnabled": False,
        "monoEnabled": False,
        "flipChannelsEnabled": False,
        "limitControlsCalibration": 12,
        "limitControlsMaxLow": 0,
        "limitControlsMaxHigh": 0,
        "relativeDecay": 100.0,
        "translationCheckId": "",
        "virtualMonitoringEnabled": False,
        "virtualMonitoringProEnabled": False,
        "virtualMonitoringMode": 0,
        "virtualMonitoringProImage": "",
        "groupGain": {},
        "virtualMonitoringTranslationCheckId": "",
    }
    preset.update(preset_values)
    presets = {
        "active": preset_id,
        "listening": [],
        "presets": [preset],
        "recents": [str(profile)],
        "scannedProfiles": [str(profile)],
    }
    _set_state(
        plugin,
        {"headphoneModeEnabled": int(headphone)},
        {"presets": json.dumps(presets)},
    )
    _pump(1.0)
    return plugin


def impulse_response(plugin, rate: float, length: int) -> np.ndarray:
    """[2, n], trailing zeros removed."""
    plugin.process(
        np.zeros((2, int(rate)), np.float32), rate, buffer_size=512, reset=False
    )
    x = np.zeros((2, length), np.float32)
    x[:, 0] = 1.0
    y = plugin.process(x, rate, buffer_size=512, reset=False).astype(float)
    nonzero = np.nonzero(np.abs(y).max(axis=0) > 1e-12)[0]
    return y[:, : nonzero[-1] + 1] if len(nonzero) else y[:, :1]


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def gain_difference_db(a, b, rate: float) -> float:
    n = 1 << 18
    f = np.fft.rfftfreq(n, 1 / rate)
    band = (f >= BAND_HZ[0]) & (f <= BAND_HZ[1])
    ga = np.abs(np.fft.rfft(a, n)[band])
    gb = np.abs(np.fft.rfft(b, n)[band])
    return float(
        np.abs(20 * np.log10(np.maximum(ga, 1e-12) / np.maximum(gb, 1e-12))).max()
    )


def preset_values(settings: dict) -> dict:
    """Plug-in preset fields for eqx ``settings``."""
    return {
        "headroomEnabled": settings["safe_headroom"],
        "headroomMode": 2,
        "listeningSpotEnabled": settings.get("listening_spot", False),
        "limitControlsCalibration": int(settings.get("limit_correction", 12)),
        "limitControlsMaxLow": int(
            playback.LIMIT_LOW[settings.get("limit_low", "neutral")]
        ),
        "limitControlsMaxHigh": int(
            playback.LIMIT_HIGH[settings.get("limit_high", "neutral")]
        ),
    }


def compare(profile: Path, rate: float, out: Path, settings: dict) -> list[tuple]:
    speaker = profile.suffix.lower() == ".swproj"
    if not speaker:
        settings = {"safe_headroom": settings["safe_headroom"]}
    rows = []
    for filter_type, phase in FILTER_TYPES.items():
        plugin = load(profile, filter_type, **preset_values(settings))
        vst = impulse_response(plugin, rate, int(rate))
        del plugin
        converter = (SwprojToFir if speaker else PeqbToFir)(phase, rate, **settings)
        eqx = fir.read(converter.convert([profile]).data).channels
        stem = f"{profile.stem} {phase}"
        (out / f"{stem} vst.wav").write_bytes(
            fir.write(fir.Fir(rate, [list(c) for c in vst]))
        )
        (out / f"{stem} eqx.wav").write_bytes(fir.write(fir.Fir(rate, eqx)))
        # The plug-in is loaded with two channels.
        for c in range(min(2, len(eqx))):
            rows.append(
                (
                    profile.name,
                    phase,
                    fir.channel_name(c, 2),
                    vst.shape[1],
                    len(eqx[c]),
                    int(np.argmax(np.abs(vst[c]))),
                    int(np.argmax(np.abs(eqx[c]))),
                    gain_difference_db(vst[c], eqx[c], rate),
                )
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("profiles", type=Path, nargs="+")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "testdata" / "real" / "soundid_vst"
    )
    parser.add_argument("--rate", type=float, default=48000.0)
    parser.add_argument(
        "--tolerance-db",
        type=float,
        help="default: 0.25 for headphone profiles, 0.5 for speaker projects",
    )
    parser.add_argument("--safe-headroom", action="store_true")
    parser.add_argument("--listening-spot", action="store_true")
    parser.add_argument(
        "--limit-correction",
        type=float,
        choices=playback.LIMIT_CORRECTION_DB,
        default=12.0,
    )
    parser.add_argument("--limit-low", choices=playback.LIMIT_LOW, default="neutral")
    parser.add_argument("--limit-high", choices=playback.LIMIT_HIGH, default="neutral")
    args = parser.parse_args(argv)
    settings = {
        "safe_headroom": args.safe_headroom,
        "listening_spot": args.listening_spot,
        "limit_correction": args.limit_correction,
        "limit_low": args.limit_low,
        "limit_high": args.limit_high,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp()) / "cfg.json"
    if GLOBAL_CONFIG.exists():
        shutil.copy2(GLOBAL_CONFIG, backup)
    rows = []
    try:
        for profile in args.profiles:
            rows += compare(profile.resolve(), args.rate, args.out, settings)
    finally:
        if backup.exists():
            shutil.copy2(backup, GLOBAL_CONFIG)
    print(
        f"{'profile':<36} {'phase':<8} {'side':<5} {'taps vst/eqx':>13} "
        f"{'peak vst/eqx':>13} {'gain diff dB':>12}"
    )
    failed = False
    for name, phase, side, taps_vst, taps_eqx, peak_vst, peak_eqx, diff in rows:
        tolerance = args.tolerance_db or (
            0.5 if name.lower().endswith(".swproj") else 0.25
        )
        bad = diff > tolerance or peak_vst != peak_eqx
        failed |= bad
        print(
            f"{name[:36]:<36} {phase:<8} {side:<5} {f'{taps_vst}/{taps_eqx}':>13} "
            f"{f'{peak_vst}/{peak_eqx}':>13} {diff:12.3f}{'  FAIL' if bad else ''}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
