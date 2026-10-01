"""Reader for IK Multimedia ARC 4 analyses (``*.arc4a``).

An ``IKMPAK`` container (``eqx.ik.pak``)::

    info.xml                      analysis settings, root SerializedMeasure
    ch<c>.wav                     magnitude spectrum of channel c (0 left, 1 right)
    step<s>/sweep<w>ch<c>.wav     recorded sweep w of channel c at point s
    step<s>/tailch<c>.wav         recorded tail of channel c at point s

``info.xml`` is JUCE ValueTree XML.  A spectrum WAV holds no audio: its
float samples are a packed real FFT of 32768 points, ``[DC, Nyquist, re1,
im1, re2, im2, ...]``, with the magnitude in ``re`` and 0 in ``im``.  ARC 4
averages the power of every sweep, compensates the microphone and scales the
mean power over 40 Hz-10 kHz to 1.  IK publishes no specification; the
layout is taken from ARC 4 Analysis and the ARC 4 plug-in 1.x.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .. import impulse
from ..fileformat import Format, Inspector, response_section
from ..model import Measurement
from ..report import Section, Table
from . import arcx, pak

INFO = "info.xml"
CHANNELS = ("Left", "Right")
# The plug-in rebuilds the spectra of an older analysis from its sweeps.
MIN_VERSION = (4, 0, 0)
SWEEP = re.compile(r"step(\d+)/(?:sweep(\d+)|tail)ch(\d+)\.wav")


@dataclass
class Arc4:
    pak_version: int
    sizes: dict[str, int]  # pak entry name -> size
    info: ET.Element  # SerializedMeasure
    info_text: str
    sample_rate: float
    spectra: list[list[float]]  # [channel] packed real FFT
    steps: dict[int, tuple[int, int]]  # step -> (sweep files, tail files)

    @property
    def fft_size(self) -> int:
        return len(self.spectra[0])

    def channel(self, name: str) -> int:
        """Index of the channel ``name`` (case-insensitive)."""
        names = [c.lower() for c in CHANNELS]
        if name.lower() not in names:
            raise ValueError(f"no {name} channel; available: {', '.join(CHANNELS)}")
        return names.index(name.lower())


def read(data: bytes) -> Arc4:
    version, entries = pak.read(data)
    if INFO not in entries:
        raise ValueError("not an ARC 4 analysis: no info.xml")
    info_text = arcx.xml_text(entries[INFO], INFO)
    info = arcx.xml_root(info_text, INFO, "SerializedMeasure")
    if _version(info.get("Version", "")) < MIN_VERSION or not info.get("SHA"):
        raise ValueError(
            f"ARC 4 analysis version {info.get('Version')!r} is older than "
            "4.0.0 or has no SHA; ARC 4 rebuilds such an analysis from its "
            "sweeps, which is not supported"
        )
    sample_rate = arcx.number_attribute(info, "SampleRate", INFO)

    spectra = []
    for c in range(len(CHANNELS)):
        name = f"ch{c}.wav"
        if name not in entries:
            raise ValueError(f"ARC 4 analysis has no {name}")
        spectrum = arcx.read_wav(entries[name], name)[1]
        if len(spectrum) < 4 or len(spectrum) % 2:
            raise ValueError(
                f"{name}: {len(spectrum)} values, expected an even FFT size"
            )
        spectra.append(spectrum)
    if len(spectra[0]) != len(spectra[1]):
        raise ValueError("ch0.wav and ch1.wav differ in length")

    steps: dict[int, tuple[int, int]] = {}
    for name in entries:
        m = SWEEP.fullmatch(name)
        if m:
            sweeps, tails = steps.get(int(m[1]), (0, 0))
            steps[int(m[1])] = (sweeps + 1, tails) if m[2] else (sweeps, tails + 1)
    return Arc4(
        version,
        {k: len(v) for k, v in entries.items()},
        info,
        info_text,
        sample_rate,
        spectra,
        dict(sorted(steps.items())),
    )


def load(path) -> Arc4:
    return read(Path(path).read_bytes())


def _version(text: str) -> tuple[int, ...]:
    parts = text.strip().split(".")
    if not all(p.isdigit() for p in parts):
        return ()
    return tuple(int(p) for p in parts)


# --------------------------------------------------------------------------
# response
# --------------------------------------------------------------------------
def power(spectrum: list[float]) -> list[float]:
    """Power of FFT bins 0 .. n/2 of a packed real FFT."""
    n, s = len(spectrum), spectrum
    return (
        [s[0] * s[0]]
        + [s[2 * k] * s[2 * k] + s[2 * k + 1] * s[2 * k + 1] for k in range(1, n // 2)]
        + [s[1] * s[1]]
    )


def response(
    arc4: Arc4, channel: int, frequencies: list[float] | None = None
) -> tuple[list[float], list[float]]:
    """(frequencies, dB) of one channel; 0 dB is the mean power over 40 Hz-10 kHz."""
    if frequencies is None:
        frequencies = impulse.log_grid(arc4.sample_rate)
    p = power(arc4.spectra[channel])
    bands = impulse.spectrum_bands(
        p, [0.0] * len(p), arc4.sample_rate / arc4.fft_size, frequencies
    )
    return frequencies, [impulse.power_db(b[0]) for b in bands]


def measurement(arc4: Arc4, channel: str, name: str = "") -> Measurement:
    c = arc4.channel(channel)
    frequencies, db = response(arc4, c)
    return Measurement(
        CHANNELS[c],
        c,
        frequencies,
        db,
        [0.0] * len(frequencies),
        sample_rate=int(arc4.sample_rate),
        name=f"{CHANNELS[c]} {name}".strip(),
    )


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class Arc4Inspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        a = read(data)
        sections = [pak.file_section(path, data, a.pak_version, a.sizes)]
        sections.append(
            Section("SerializedMeasure", list(a.info.attrib.items()), raw=a.info_text)
        )
        sections.append(
            Section(
                "measurement points",
                [("points", len(a.steps))],
                Table(
                    ["point", "sweep files", "tail files"],
                    [(s, sweeps, tails) for s, (sweeps, tails) in a.steps.items()],
                ),
            )
        )
        for c, channel in enumerate(CHANNELS):
            sections.append(
                response_section(
                    f"channel {c} {channel}",
                    [("FFT size", a.fft_size)],
                    lambda c=c: response(a, c),
                )
            )
        return sections


FORMAT = Format("arc4", (".arc4a",), "IK Multimedia ARC 4 analysis", Arc4Inspector)
