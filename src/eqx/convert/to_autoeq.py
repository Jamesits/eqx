"""Curves of other formats -> AutoEq frequency response CSV (``frequency,raw``).

One file holds one curve, so stereo sources are converted one channel at a time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .. import correction
from ..audyssey import mqx
from ..autoeq import response
from ..dirac import targetcurve
from ..fileformat import frequency_range
from ..ik import arc4, arcx
from ..model import Correction
from ..options import Option
from ..rew import mdat
from ..rme import tmreq
from ..rogueamoeba import soundsource
from ..soundid import (export_biquad_json, export_biquad_xml, export_lvnd, export_peq_json,
                       export_txt, peqb, swproj, targetpreset)
from .base import Converter, Result
from .mdat_swproj import mic_response_db, standard_grid

CHANNELS = ("left", "right")
CHANNEL_OPTION = Option("--channel", choices=CHANNELS, help="channel to convert (default: left)")
SPEAKER_OPTION = Option("--speaker",
                        help="speaker, case-insensitive, as named by inspect, e.g. Left, "
                             "Subwoofer, Center (default: Left)")
COMPUTER_ID_OPTION = Option(
    "--computer-id",
    help="computer ID the profile was downloaded for (or SWHP_COMPUTER_ID; "
         "default: this machine's ID)")
KEY_OPTION = Option("--key", help="raw AES body key, hex")
PASSWORD_OPTION = Option("--password", help="project password (or SWPROJ_PASSWORD)")


def _channel_name(channel: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}")
    return channel.capitalize()


def _result(points, name: str, *notes: str) -> Result:
    return Result(response.write(points).encode("utf-8"), name,
                  [f"{len(points)} points, {frequency_range(points)}", *notes])


def _missing(channel: str, available) -> ValueError:
    return ValueError(f"no {channel} channel; available: {', '.join(available) or 'none'}")


class MdatToAutoeq(Converter):
    source = "mdat"
    target = "autoeq"
    description = "one channel of a REW measurement, unresampled"
    options = (CHANNEL_OPTION,)

    def __init__(self, channel: str = "left"):
        self.channel = _channel_name(channel)

    def _convert(self, path: Path) -> Result:
        measurements = {m.channel: m for m in mdat.load(path)}
        if self.channel not in measurements:
            raise _missing(self.channel, measurements)
        m = measurements[self.channel]
        return _result(list(zip(m.frequencies, m.response)), f"{path.stem} {self.channel}.csv",
                       f"{self.channel} measurement {m.name!r}, dB SPL")


class ArcxToAutoeq(Converter):
    source = "arcx"
    target = "autoeq"
    description = "the measured response of one speaker of an ARC X session or analysis"
    options = (SPEAKER_OPTION, arcx.POINT_OPTION)

    def __init__(self, speaker: str = "Left", point: int | None = None):
        self.speaker = speaker
        self.point = point

    def _convert(self, path: Path) -> Result:
        a = arcx.load(path)
        c = a.channel(self.speaker)
        frequencies, db, _ = arcx.response(a, c, self.point)
        points = "all points" if self.point is None else f"point {self.point}"
        return _result(list(zip(frequencies, db)), f"{path.stem} {a.speakers[c]}.csv",
                       f"{a.speakers[c]} response, {points}, dB re full scale")


class MqxToAutoeq(Converter):
    source = "mqx"
    target = "autoeq"
    description = "the measured response of one speaker of a MultEQ-X project"
    options = (SPEAKER_OPTION, mqx.POSITION_OPTION, mqx.MIC_RESPONSE_OPTION)

    def __init__(self, speaker: str = "Left", position: int | None = None,
                 mic_response: Path | None = None):
        self.speaker = speaker
        self.position = position
        self.mic_response = mic_response

    def _convert(self, path: Path) -> Result:
        m = mqx.load(path)
        c = m.channel(self.speaker)
        frequencies, db, _ = mqx.response(m, c, self.position)
        mic = "not compensated for the microphone"
        if self.mic_response is not None:
            db = [v - g for v, g in zip(db, mic_response_db(self.mic_response, frequencies))]
            mic = f"minus {Path(self.mic_response).name}"
        designation = m.channels[c].designation
        positions = ("enabled measurements" if self.position is None
                     else f"position {self.position}")
        return _result(list(zip(frequencies, db)), f"{path.stem} {designation}.csv",
                       f"{designation} response, {positions}, dB re full scale, {mic}")


class Arc4ToAutoeq(Converter):
    source = "arc4"
    target = "autoeq"
    description = "the measured response of one channel of an ARC 4 analysis"
    options = (CHANNEL_OPTION,)

    def __init__(self, channel: str = "left"):
        self.channel = _channel_name(channel)

    def _convert(self, path: Path) -> Result:
        a = arc4.load(path)
        frequencies, db = arc4.response(a, a.channel(self.channel))
        return _result(list(zip(frequencies, db)), f"{path.stem} {self.channel}.csv",
                       f"{self.channel} response, dB re the 40 Hz-10 kHz mean")


class SwprojToAutoeq(Converter):
    """``--speaker`` selects any channel by its SoundID name; ``--channel`` left or right."""

    source = "swproj"
    target = "autoeq"
    description = "the measurement curve of one channel of a SoundID project"
    options = (
        CHANNEL_OPTION,
        SPEAKER_OPTION,
        PASSWORD_OPTION,
    )

    def __init__(self, channel: str | None = None, speaker: str | None = None,
                 password: str | None = None):
        if channel is not None and speaker is not None:
            raise ValueError("give --channel or --speaker, not both")
        self.speaker = speaker if speaker is not None else _channel_name(channel or "left")
        password = password or os.environ.get("SWPROJ_PASSWORD")
        self.password = password.encode() if password else None

    def _convert(self, path: Path) -> Result:
        curves = swproj.measurement_curves(swproj.SwProj.open(path, self.password))
        names = {name.lower(): name for name in curves}
        if self.speaker.lower() not in names:
            raise _missing(self.speaker, curves)
        name = names[self.speaker.lower()]
        points = [(f, r) for f, r, _ in curves[name]]
        return _result(points, f"{path.stem} {name}.csv",
                       f"{name} measurement, dB relative to the project reference")


class PeqbToAutoeq(Converter):
    source = "peqb"
    target = "autoeq"
    description = "the response of one side of a SoundID profile (headphone: -correction)"
    options = (CHANNEL_OPTION, COMPUTER_ID_OPTION, KEY_OPTION)

    def __init__(self, channel: str = "left", computer_id: str | None = None,
                 key: str | None = None):
        self.channel = _channel_name(channel)
        peqb.key_for(computer_id, key)
        self.computer_id = computer_id
        self.key = key

    def _convert(self, path: Path) -> Result:
        p = peqb.open_decoded(path.read_bytes(), self.computer_id, self.key)
        side = self.channel
        # A project export holds the measurement; a headphone profile only the
        # correction, which is the inverse of the response.
        for c in p.curves:
            if (c.type_name == f"Measurement{side}"
                    or (c.type_name == "Measurement" and c.parameters.get("ChannelName") == side)):
                return _result([(f, r) for f, r, _ in c.points], f"{path.stem} {side}.csv",
                               f"{side} measurement")
        for c in p.curves:
            if c.type_name == f"Correction{side}":
                return _result([(f, -r) for f, r, _ in c.points], f"{path.stem} {side}.csv",
                               f"{side} response = -{c.type_name}")
        raise ValueError(f"no {side} measurement or correction curve; curves: "
                         f"{', '.join(c.type_name for c in p.curves) or 'none'}")


class TargetpresetToAutoeq(Converter):
    source = "targetpreset"
    target = "autoeq"
    description = "the target curve of a Custom Target Preset, on the standard 355-point grid"

    def _convert(self, path: Path) -> Result:
        preset = targetpreset.load(path)
        grid = standard_grid()
        points = list(zip(grid, targetpreset.target_response(preset, grid)))
        filters = sum(f.enabled for g in preset.filter_groups for f in g.filters)
        return _result(points, f"{path.stem}.csv",
                       f"{filters} enabled filters; the correction band is not part of the curve")


class TargetcurveToAutoeq(Converter):
    source = "targetcurve"
    target = "autoeq"
    description = "a Dirac Live target curve, on the standard 355-point grid"

    def _convert(self, path: Path) -> Result:
        curve = targetcurve.load(path)
        curve.validate()
        grid = standard_grid()
        return _result(list(zip(grid, curve.response(grid))), f"{path.stem}.csv",
                       f"{len(curve.breakpoints)} breakpoints; the correction range "
                       f"{curve.low_hz:g}-{curve.high_hz:g} Hz is not part of the curve")


SAMPLE_RATE_OPTION = Option(
    "--sample-rate", type=float,
    help="biquad set of this sample rate, Hz (default: 48000 if present, else the lowest)")


class ExportToAutoeq(Converter):
    """The correction of one channel of a device export.

    Filters are evaluated on the standard grid; graphic EQ points are written as they are.
    """

    target = "autoeq"
    description = "the correction of one channel of a SoundID device export"
    options = (CHANNEL_OPTION,)
    loader: Callable[..., correction.Export]    # the format module's load(path, **options)

    def __init__(self, channel: str = "left", sample_rate: float | None = None):
        self.channel = _channel_name(channel)
        self.sample_rate = sample_rate
        self.load_options: dict = {}

    def correction(self, export: correction.Export) -> Correction:
        return export.select(self.channel, self.sample_rate)

    def _convert(self, path: Path) -> Result:
        c = self.correction(type(self).loader(path, **self.load_options))
        if c.biquads or c.peqs:
            grid = standard_grid()
            points = list(zip(grid, c.response(grid)))
        else:
            points = [(f, g + c.gain_db) for f, g in c.points]
        side = correction.channel_name(c.channel)
        rate = f" at {c.sample_rate:g} Hz" if c.sample_rate else ""
        return _result(points, self.output_name(path, side), f"{side} correction{rate}, dB")

    def output_name(self, path: Path, side: str) -> str:
        return f"{path.stem} {side}.csv"


class SoundidExportBiquadJsonToAutoeq(ExportToAutoeq):
    source = "soundid-export-biquad-json"
    options = (CHANNEL_OPTION, SAMPLE_RATE_OPTION, export_biquad_json.SERIAL_OPTION)
    loader = export_biquad_json.load

    def __init__(self, channel: str = "left", sample_rate: float | None = None,
                 serial_number: str | None = None):
        super().__init__(channel, sample_rate)
        self.load_options = {"serial_number": serial_number}


class SoundidExportBiquadXmlToAutoeq(ExportToAutoeq):
    source = "soundid-export-biquad-xml"
    options = (CHANNEL_OPTION, SAMPLE_RATE_OPTION)
    loader = export_biquad_xml.load


class SoundidExportPeqJsonToAutoeq(ExportToAutoeq):
    source = "soundid-export-peq-json"
    loader = export_peq_json.load


class SoundidExportTxtToAutoeq(ExportToAutoeq):
    source = "soundid-export-txt"
    loader = export_txt.load


class TmreqToAutoeq(ExportToAutoeq):
    source = "tmreq"
    description = "the Room EQ of one channel of a TotalMix preset"
    loader = tmreq.load


class SoundidExportLvndToAutoeq(ExportToAutoeq):
    """One file holds one channel and names it, so there is no channel option."""

    source = "soundid-export-lvnd"
    options = ()
    loader = export_lvnd.load

    def correction(self, export: correction.Export) -> Correction:
        return export.corrections[0]

    def output_name(self, path: Path, side: str) -> str:
        return f"{path.stem}.csv"


class SoundsourceToAutoeq(ExportToAutoeq):
    """One profile applies to both channels, so there is no channel option."""

    source = "soundsource"
    description = "the EQ curve of a SoundSource Headphone EQ profile"
    options = ()
    loader = soundsource.load

    def correction(self, export: correction.Export) -> Correction:
        return export.corrections[0]

    def output_name(self, path: Path, side: str) -> str:
        return f"{path.stem}.csv"
