"""Speaker measurements of other formats -> SoundID ``.swproj`` speaker project.

Audio samples are omitted.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

from .. import formats
from ..audyssey import mqx
from ..autoeq import response
from ..ik import arc4, arcx
from ..minidsp import umik
from ..model import Measurement, MicProfile
from ..options import Option
from ..rew import mdat
from ..soundid import layout, speakerproject, swmicpkg, swproj
from ..soundid.speakerproject import (APPS, DEFAULT_CLIP_FRACTION, DEFAULT_HIGH_CUTOFF_HZ,
                                      DEFAULT_LFE_HIGH_CUTOFF_HZ, DEFAULT_LOW_CUTOFF_HZ,
                                      DEFAULT_MAX_BOOST_DB, SOUNDID_MAX_BOOST_DB)
from .base import Converter, Result
from .common import COLUMN_OPTION, curves

MIC_PROFILE_FORMATS = ("swmicpkg", "swproj", "umik")


def load_mic_profile(path: Path, angle: str | None = None,
                     kind: str | None = None) -> MicProfile:
    """One microphone table from a ``.swmicpkg``, a ``.swproj`` measured with it,
    or a UMIK file.

    ``angle`` selects the table of a profile with several (default
    ``degrees_0``); a profile with one table rejects it and gives that table.
    ``kind`` None detects the format by extension.
    """
    path = Path(path)
    kind = kind or formats.detect(path, MIC_PROFILE_FORMATS)
    if kind == "swmicpkg":
        text = path.read_text(encoding="utf-8")
        available = swmicpkg.angles(text)
        read = lambda a: swmicpkg.read(text, a, path.stem)
    elif kind == "swproj":
        profiles = {p.angle: p for p in swproj.mic_profiles(swproj.SwProj.open(path))}
        available, read = list(profiles), profiles.__getitem__
    elif kind == "umik":
        profile = umik.load(path).profile
        available, read = [profile.angle], lambda a: profile
    else:
        raise ValueError(f"{path.name}: a microphone profile must be .swmicpkg, .swproj "
                         "or a UMIK .txt")
    if not available:
        raise ValueError(f"{path.name} holds no microphone table")
    if len(available) == 1:
        if angle is not None:
            raise ValueError(f"{path.name} holds one microphone table ({available[0]}); "
                             "--mic-angle applies only to profiles with several tables")
        return read(available[0])
    angle = angle or swmicpkg.PLAIN_ANGLE
    if angle not in available:
        raise ValueError(f"{path.name} has no microphone {angle!r} table; "
                         f"available: {', '.join(available)}")
    return read(angle)


def _channel_values(items: list[str], flag: str) -> dict[str, float]:
    """``CHANNEL=NUMBER`` items -> {channel: number}."""
    out: dict[str, float] = {}
    for item in items:
        name, sep, text = item.partition("=")
        name = name.strip()
        try:
            value = float(text)
        except ValueError:
            value = math.nan
        if not sep or not name or not math.isfinite(value):
            raise ValueError(f"{flag} {item!r}: expected CHANNEL=NUMBER")
        if name.lower() in (n.lower() for n in out):
            raise ValueError(f"{flag}: {name} given twice")
        out[name] = value
    return out


class SpeakerProjectConverter(Converter):
    """Speaker measurements as a SoundID speaker project; subclasses read the measurements."""

    target = "swproj"
    options = (
        Option("--mic-profile", type=Path,
               help="required: SoundID microphone package (.swmicpkg), a .swproj "
                    "measured with the microphone, or a UMIK calibration file (.txt)"),
        Option("--mic-profile-format", choices=MIC_PROFILE_FORMATS,
               help="format of --mic-profile (default: by extension)"),
        Option("--mic-angle",
               help="table of a --mic-profile with several tables, by the angle between "
                    "mic axis and speaker: degrees_0, degrees_30 or degrees_90 "
                    f"(default: {swmicpkg.PLAIN_ANGLE}); a profile with one table uses it"),
        Option("--reference-spl", type=float,
               help="calibrated SPL mapped to 0 dB for all channels "
                    "(default: estimated from the 200 Hz-10 kHz level)"),
        Option("--low-cutoff-hz", type=float,
               help=f"no speaker EQ below this frequency (default: {DEFAULT_LOW_CUTOFF_HZ:g} Hz)"),
        Option("--high-cutoff-hz", type=float,
               help=f"no speaker EQ above this frequency (default: {DEFAULT_HIGH_CUTOFF_HZ:g} Hz)"),
        Option("--lfe-high-cutoff-hz", type=float,
               help="no LFE channel EQ above this frequency "
                    f"(default: {DEFAULT_LFE_HIGH_CUTOFF_HZ:g} Hz)"),
        Option("--max-boost-db", type=float,
               help=f"maximum positive speaker EQ gain, at most +{SOUNDID_MAX_BOOST_DB:g} "
                    f"(default: +{DEFAULT_MAX_BOOST_DB:g} dB)"),
        Option("--clip-percent", type=float,
               help="estimated reference only: percent of 200 Hz-10 kHz points allowed to "
                    f"need more than the maximum boost (default: {DEFAULT_CLIP_FRACTION * 100:g})"),
        Option("--spot-delay-ms", action="append", metavar="CHANNEL=MS",
               help="listening spot delay of a channel, e.g. Right=0.15; repeatable "
                    "(default: 0)"),
        Option("--spot-gain-db", action="append", metavar="CHANNEL=DB",
               help="listening spot gain of a channel, e.g. Left=-0.5; repeatable "
                    "(default: 0)"),
        Option("--app", choices=APPS,
               help="soundid: SoundID Reference; sonarworks-reference: Sonarworks Reference "
                    "3 and 4, stereo only (default: soundid)"),
    )

    def __init__(
        self,
        mic_profile: Path | None = None,
        mic_profile_format: str | None = None,
        mic_angle: str | None = None,
        reference_spl: float | None = None,
        low_cutoff_hz: float = DEFAULT_LOW_CUTOFF_HZ,
        high_cutoff_hz: float = DEFAULT_HIGH_CUTOFF_HZ,
        lfe_high_cutoff_hz: float = DEFAULT_LFE_HIGH_CUTOFF_HZ,
        max_boost_db: float = DEFAULT_MAX_BOOST_DB,
        clip_percent: float = DEFAULT_CLIP_FRACTION * 100,
        spot_delay_ms: list[str] | None = None,
        spot_gain_db: list[str] | None = None,
        app: str = "soundid",
    ):
        if app not in APPS:
            raise ValueError(f"--app must be one of: {', '.join(APPS)}")
        self.app = app
        if mic_profile is None:
            raise ValueError(f"{self.source} -> {self.target} needs --mic-profile")
        self.mic_profile = Path(mic_profile)
        self.mic_profile_format = mic_profile_format
        self.mic_angle = mic_angle
        self.settings = dict(
            reference_spl=reference_spl,
            low_cutoff_hz=low_cutoff_hz,
            high_cutoff_hz=high_cutoff_hz,
            lfe_high_cutoff_hz=lfe_high_cutoff_hz,
            max_boost_db=max_boost_db,
            clip_fraction=clip_percent / 100,
        )
        self.spot_values = ((_channel_values(spot_delay_ms or [], "--spot-delay-ms"), 0,
                             "--spot-delay-ms"),
                            (_channel_values(spot_gain_db or [], "--spot-gain-db"), 1,
                             "--spot-gain-db"))

    def spot(self, measurements: list[Measurement]) -> dict[str, tuple[float, float]]:
        """{channel: (delay ms, gain dB)}; option channel names are case-insensitive."""
        names = {m.channel.lower(): m.channel for m in measurements}
        spot: dict[str, list[float]] = {}
        for values, slot, flag in self.spot_values:
            for name, value in values.items():
                if name.lower() not in names:
                    raise ValueError(f"{flag}: no {name} channel; channels: "
                                     f"{', '.join(names.values())}")
                spot.setdefault(names[name.lower()], [0.0, 0.0])[slot] = value
        return {channel: (delay, gain) for channel, (delay, gain) in spot.items()}

    def measurements(self, *paths: Path) -> tuple[layout.Layout, list[Measurement]]:
        """The SoundID layout and the measurements in ``paths``, in channel order."""
        raise NotImplementedError

    def _convert(self, *paths: Path) -> Result:
        path = paths[0]
        target, measurements = self.measurements(*paths)
        profile = load_mic_profile(self.mic_profile, self.mic_angle,
                                   self.mic_profile_format)
        spot = self.spot(measurements)
        result = speakerproject.convert(measurements, profile, path.stem, layout=target,
                                        spot=spot, app=self.app, **self.settings)
        s = self.settings
        source = "estimated" if s["reference_spl"] is None else "given"
        app = ("Sonarworks Reference 3 / 4" if self.app == "sonarworks-reference"
               else "SoundID Reference")
        notes = [
            f"app: {app}; layout: {target.name}; measurements: {len(measurements)}; "
            f"frequency points: {len(result.grid)}; mic table: {profile.name} {profile.angle}",
            f"reference: {result.reference_spl:.1f} dB SPL = 0 dB ({source}); correction band: "
            f"{s['low_cutoff_hz']:g}-{s['high_cutoff_hz']:g} Hz; "
            f"maximum boost: {s['max_boost_db']:g} dB",
        ]
        if any(target.channels[m.index].is_lfe for m in measurements):
            notes.append(f"LFE correction band: {s['low_cutoff_hz']:g}-"
                         f"{min(s['high_cutoff_hz'], s['lfe_high_cutoff_hz']):g} Hz")
        notes += [f"{channel} correction: {min(c.correction):+.2f} to {max(c.correction):+.2f} dB"
                  for channel, c in result.curves.items()]
        notes += [f"{channel} listening spot: delay {delay:g} ms, gain {gain:+g} dB"
                  for channel, (delay, gain) in spot.items()]
        return Result(result.data, path.stem + ".swproj", notes)


class MdatToSwproj(SpeakerProjectConverter):
    source = "mdat"
    description = "REW speaker measurements as a SoundID speaker project (no audio samples)"

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        return layout.STEREO, mdat.load(path)


class AutoeqToSwproj(SpeakerProjectConverter):
    """Group delay is not in the CSV, so it is 0 and there is no group-delay correction."""

    source = "autoeq"
    description = ("speaker measurements (inputs: left, optional right) as a SoundID "
                   "speaker project")
    options = SpeakerProjectConverter.options + (COLUMN_OPTION,)
    inputs = 2

    def __init__(self, column: str = response.RAW, **settings):
        super().__init__(**settings)
        self.column = column

    def measurements(self, *paths: Path) -> tuple[layout.Layout, list[Measurement]]:
        return layout.STEREO, [
            Measurement(channel, index, [f for f, _ in points], [v for _, v in points],
                        [0.0] * len(points), name=f"{channel} {paths[0].stem}")
            for index, (channel, points) in enumerate(curves(paths, self.column))
        ]


def in_layout(target: layout.Layout,
              measured: list[tuple[str, Measurement]]) -> list[Measurement]:
    """(SoundID channel short name, measurement) pairs as channels of ``target``,
    in channel order."""
    out = []
    for short, m in measured:
        index = target.index(short)
        out.append(dataclasses.replace(m, channel=target.channels[index].name, index=index))
    return sorted(out, key=lambda m: m.index)


class Arc4ToSwproj(SpeakerProjectConverter):
    """Left and right, stereo.  No group delay: ARC 4 keeps the magnitude only."""

    source = "arc4"
    description = "ARC 4 speaker responses as a SoundID speaker project (no audio samples)"

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        a = arc4.load(path)
        return layout.STEREO, [arc4.measurement(a, channel, Path(path).stem)
                               for channel in arc4.CHANNELS]


# ARC X layout id -> SoundID layout id, and the SoundID channel of each ARC X
# channel.  ARC X's 4.0 rears and 5.1 rear surrounds are SoundID's surrounds.
ARCX_LAYOUTS = {
    0: (0, ("L", "R")),
    1: (0, ("L", "R")),
    2: (1, ("L", "R", "LFE")),
    3: (6, ("L", "R", "Ls", "Rs")),
    4: (11, ("L", "R", "C", "LFE", "Ls", "Rs")),
    7: (14, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs")),
    5: (16, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Ltf", "Rtf", "Ltr", "Rtr")),
    6: (17, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Ltf", "Rtf", "Ltm", "Rtm",
             "Ltr", "Rtr")),
    8: (20, ("L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Lw", "Rw", "Ltf", "Rtf", "Ltm",
             "Rtm", "Ltr", "Rtr")),
}


class ArcxToSwproj(SpeakerProjectConverter):
    """Every speaker, in the SoundID layout of the same speakers."""

    source = "arcx"
    description = "ARC X speaker responses as a SoundID speaker project (no audio samples)"
    options = SpeakerProjectConverter.options + (arcx.POINT_OPTION,)

    def __init__(self, point: int | None = None, **settings):
        super().__init__(**settings)
        self.point = point

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        a = arcx.load(path)
        if a.layout is None:
            raise ValueError(f"unknown ARC X layout of {len(a.channels)} channels; "
                             "the SoundID layout needs the session or the analysis layout name")
        target_id, shorts = ARCX_LAYOUTS[a.layout]
        target = layout.LAYOUTS[target_id]
        return target, in_layout(target, [
            (short, arcx.measurement(a, speaker, self.point, Path(path).stem))
            for speaker, short in zip(a.speakers, shorts)])


# MultEQ-X designation -> SoundID channel.  Front and rear heights are
# SoundID's top front and top rear.
MQX_CHANNELS = {
    "FL": "L", "FR": "R", "C": "C", "SW1": "LFE", "SLA": "Ls", "SRA": "Rs",
    "SBL": "Lrs", "SBR": "Rrs", "SB": "Cs", "FWL": "Lw", "FWR": "Rw",
    "TFL": "Ltf", "TFR": "Rtf", "TML": "Ltm", "TMR": "Rtm", "TRL": "Ltr", "TRR": "Rtr",
    "FHL": "Ltf", "FHR": "Rtf", "RHL": "Ltr", "RHR": "Rtr",
}


def mqx_layout(designations: list[str]) -> layout.Layout:
    """The SoundID layout with the same speakers as the MultEQ-X ``designations``."""
    unknown = [d for d in designations if d not in MQX_CHANNELS]
    if unknown:
        raise ValueError(f"no SoundID channel for {', '.join(unknown)}; "
                         f"supported: {', '.join(MQX_CHANNELS)}")
    shorts = [MQX_CHANNELS[d] for d in designations]
    if len(set(shorts)) != len(shorts):
        raise ValueError(f"{', '.join(designations)} map to the same SoundID channel twice")
    for candidate in layout.LAYOUTS.values():
        if sorted(c.short for c in candidate.channels) == sorted(shorts):
            return candidate
    raise ValueError(f"no SoundID layout has the speakers {', '.join(designations)}")


class MqxToSwproj(SpeakerProjectConverter):
    """Every enabled speaker, in the SoundID layout of the same speakers."""

    source = "mqx"
    description = "MultEQ-X speaker responses as a SoundID speaker project (no audio samples)"
    options = SpeakerProjectConverter.options + (mqx.POSITION_OPTION,)

    def __init__(self, position: int | None = None, **settings):
        super().__init__(**settings)
        self.position = position

    def measurements(self, path: Path) -> tuple[layout.Layout, list[Measurement]]:
        m = mqx.load(path)
        channels = [c for c in m.channels
                    if (c.data.get("Calibration") or {}).get("IsEnabled", True) is not False
                    and any(r.channel == c.guid for r in m.recordings)]
        target = mqx_layout([c.designation for c in channels])
        return target, in_layout(target, [
            (MQX_CHANNELS[c.designation],
             mqx.measurement(m, c.designation, self.position, Path(path).stem))
            for c in channels])
