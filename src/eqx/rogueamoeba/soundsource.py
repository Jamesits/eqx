"""Reader and writer for SoundSource Headphone EQ custom profiles (``*.txt``).

The Equalizer APO / AutoEq parametric EQ text::

    Preamp: -7.9 dB
    Filter 1: ON PK Fc 210 Hz Gain -4.9 dB Q 0.46

The reader follows SoundSource's own parser (AudioHijackKit's
``AHAutoEQNode``): the first ``Preamp:`` anywhere, case-insensitive; a filter
line contains "filter" and "fc" and no ``#`` or ``//``; other lines are
ignored.  One profile applies to both channels.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..correction import Export, ExportInspector
from ..fileformat import Format, fixed, head_text
from ..model import Correction, Peq

MAX_FILTERS = 32
# SoundSource clamps these on load; gain is not limited.
FREQUENCY_HZ, MIN_Q = (20.0, 20000.0), 0.4
DEFAULT_FREQUENCY, DEFAULT_GAIN, DEFAULT_Q = 1000.0, 0.0, 1.0
CHANNEL = "Both"

KINDS = {
    "bell": ("PK", "PEQ", "PEAK", "BELL", "MODAL"),
    "low-shelf": ("LS", "LSC", "LOW_SHELF"),
    "high-shelf": ("HS", "HSC", "HI_SHELF", "HIGH_SHELF"),
    "high-pass": ("HP", "HPF", "HPQ", "HIPASS", "HI_PASS", "HIGHPASS", "HIGH_PASS"),
    "low-pass": ("LP", "LPF", "LPQ", "LOWPASS", "LOW_PASS"),
    "all-pass": ("AP",),
    "notch": ("NO",),
}
_KIND = {token: kind for kind, tokens in KINDS.items() for token in tokens}
_NUMBER = r"([-+]?[0-9]*[.,]?[0-9]+)"
_PREAMP = re.compile(r"Preamp:\s+" + _NUMBER, re.IGNORECASE)
_FILTER = re.compile(
    r"Filter\s*\d*\s*:\s+(ON|OFF)\s+("
    + "|".join(_KIND)
    + r")\s+(3dB|6dB|12dB|18dB|Fc)",
    re.IGNORECASE,
)
_VALUES = {
    name: re.compile(name + r"\s+" + _NUMBER, re.IGNORECASE)
    for name in ("Fc", "Gain", "Q")
}


def _number(text: str) -> float:
    # Comma decimals: SoundSource replaces "," by ".".
    return float(text.replace(",", "."))


def _value(line: str, name: str, default: float) -> float:
    match = _VALUES[name].search(line)
    return _number(match.group(1)) if match else default


def _slope_q(gain_db: float, slope_db: float) -> float:
    """The Q of a shelf given as a slope token.

    SoundSource uses the cookbook shelf slope S = 10^(max(dB, 3) / 40), not
    the dB per octave the token names.
    """
    a = 10 ** (gain_db / 40)
    s = 10 ** (max(slope_db, 3.0) / 40)
    root = (a + 1 / a) * (1 / s - 1) + 2
    if root <= 0:
        raise ValueError(
            f"a {slope_db:g} dB shelf slope is undefined at {gain_db:g} dB gain"
        )
    return 1 / math.sqrt(root)


def is_filter_line(line: str) -> bool:
    lower = line.lower()
    return (
        "filter" in lower and "fc" in lower and "#" not in lower and "//" not in lower
    )


def read(text: str) -> Export:
    """One correction; filters as SoundSource plays them (clamped, OFF filters included)."""
    text = text.lstrip("﻿")
    preamp = _PREAMP.search(text)
    gain_db = _number(preamp.group(1)) if preamp else 0.0
    peqs, off = [], 0
    # SoundSource splits on LF only, then trims each line.
    for line in (line.strip() for line in text.split("\n")):
        if not is_filter_line(line):
            continue
        match = _FILTER.search(line)
        if match is None:
            raise ValueError(f"not a valid filter: {line!r}")
        state, token, slope = match.groups()
        kind = _KIND[token.upper()]
        frequency = min(
            max(_value(line, "Fc", DEFAULT_FREQUENCY), FREQUENCY_HZ[0]), FREQUENCY_HZ[1]
        )
        gain = _value(line, "Gain", DEFAULT_GAIN)
        q = max(_value(line, "Q", DEFAULT_Q), MIN_Q)
        if slope.lower() != "fc" and kind in ("low-shelf", "high-shelf"):
            q = _slope_q(gain, float(slope[:-2]))
        off += state != "ON"
        peqs.append(Peq(frequency, gain, q, kind))
    if not peqs:
        raise ValueError("no filter in the SoundSource profile")
    if len(peqs) > MAX_FILTERS:
        raise ValueError(f"{len(peqs)} filters; SoundSource takes {MAX_FILTERS}")
    # SoundSource drops the ON/OFF flag, so OFF filters play too.
    fields = [("OFF filters (applied)", off)] if off else []
    return Export([Correction(CHANNEL, gain_db, peqs=peqs)], fields, text=text)


def load(path) -> Export:
    return read(Path(path).read_text(encoding="utf-8"))


def sniff(data: bytes) -> bool:
    """A filter line among the first lines."""
    text = head_text(data)
    return any(
        is_filter_line(line) and _FILTER.search(line) for line in text.split("\n")
    )


def _fixed(value: float, digits: int) -> str:
    return fixed(value, digits).rstrip("0").rstrip(".")


TOKENS = {kind: tokens[0] for kind, tokens in KINDS.items()}


def write(correction: Correction) -> str:
    """Profile text: the gain as the preamp, then the parametric filters."""
    c = correction
    if c.biquads or c.points:
        raise ValueError("a SoundSource profile takes parametric filters only")
    if c.delay_ms:
        raise ValueError("a SoundSource profile has no delay")
    if not c.peqs:
        raise ValueError("a SoundSource profile needs at least one filter")
    if len(c.peqs) > MAX_FILTERS:
        raise ValueError(f"{len(c.peqs)} filters; SoundSource takes {MAX_FILTERS}")
    lines = [f"Preamp: {_fixed(c.gain_db, 2)} dB"]
    for n, p in enumerate(c.peqs, 1):
        if not FREQUENCY_HZ[0] <= p.frequency <= FREQUENCY_HZ[1] or p.q < MIN_Q:
            raise ValueError(
                f"filter {n}: SoundSource takes {FREQUENCY_HZ[0]:g}-"
                f"{FREQUENCY_HZ[1]:g} Hz and Q >= {MIN_Q:g}"
            )
        if p.kind not in TOKENS:
            raise ValueError(f"filter {n}: SoundSource has no {p.kind} filter")
        lines.append(
            f"Filter {n}: ON {TOKENS[p.kind]} Fc {_fixed(p.frequency, 1)} Hz "
            f"Gain {_fixed(p.gain_db, 2)} dB Q {_fixed(p.q, 3)}"
        )
    return "\n".join(lines) + "\n"


class SoundsourceInspector(ExportInspector):
    load = load


FORMAT = Format(
    "soundsource",
    (".txt",),
    "SoundSource Headphone EQ custom profile",
    SoundsourceInspector,
    sniff,
)
