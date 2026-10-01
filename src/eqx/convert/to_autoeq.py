"""Curves of other formats -> AutoEq frequency response CSV (``frequency,raw``).

One file holds one curve, so stereo sources are converted one channel at a time.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .. import correction
from ..audyssey import mqx
from ..dirac import filterslot, playback, targetcurve
from ..ik import arc4, arcx
from ..model import Correction, standard_grid
from ..options import Option
from ..rew import mdat
from ..rme import tmreq
from ..rode import fuzzmeasure
from ..rogueamoeba import soundsource
from ..soundid import (export_biquad_json, export_biquad_xml, export_lvnd, export_peq_json,
                       export_txt, peqb, swproj, targetpreset)
from ..wav import fir
from .base import Converter, Result
from .common import (CHANNEL_OPTION, DEFAULT_RATE, RATE_OPTION, SPEAKER_OPTION, channel_name,
                     csv_result, dirac_notes, dirac_output, dirac_rate, mic_response_db,
                     missing)


class MdatToAutoeq(Converter):
    source = "mdat"
    target = "autoeq"
    description = "one channel of a REW measurement, unresampled"
    options = (CHANNEL_OPTION,)

    def __init__(self, channel: str = "left"):
        self.channel = channel_name(channel)

    def _convert(self, path: Path) -> Result:
        measurements = {m.channel: m for m in mdat.load(path)}
        if self.channel not in measurements:
            raise missing(self.channel, measurements)
        m = measurements[self.channel]
        return csv_result(list(zip(m.frequencies, m.response)), f"{path.stem} {self.channel}.csv",
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
        return csv_result(list(zip(frequencies, db)), f"{path.stem} {a.speakers[c]}.csv",
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
        return csv_result(list(zip(frequencies, db)), f"{path.stem} {designation}.csv",
                          f"{designation} response, {positions}, dB re full scale, {mic}")


class FuzzmeasureToAutoeq(Converter):
    source = "fuzzmeasure"
    target = "autoeq"
    description = "the frequency response of one measurement of a FuzzMeasure document"
    options = (fuzzmeasure.MEASUREMENT_OPTION, fuzzmeasure.MIC_CALIBRATION_OPTION,
               fuzzmeasure.SPL_OPTION)

    def __init__(self, measurement: str | None = None, mic_calibration: bool = True,
                 spl: bool = False):
        self.measurement = measurement
        self.mic_calibration = mic_calibration
        self.spl = spl

    def _convert(self, path: Path) -> Result:
        d = fuzzmeasure.load(path)
        i = d.record(self.measurement)
        r = d.records[i]
        frequencies, db, _ = fuzzmeasure.response(r, calibration=self.mic_calibration,
                                                  spl=self.spl)
        level = "dB SPL" if self.spl else "dB re full scale"
        mic = "no microphone calibration"
        if r.calibration is not None and r.use_calibration:
            mic = (f"minus microphone calibration {r.calibration.name!r}" if self.mic_calibration
                   else f"microphone calibration {r.calibration.name!r} not applied")
        # Titles are free text; keep the file name valid.
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", r.title).strip() or f"measurement {i}"
        return csv_result(list(zip(frequencies, db)), f"{path.stem} {name}.csv",
                          f"measurement {r.title!r}, {level}, {mic}")


class Arc4ToAutoeq(Converter):
    source = "arc4"
    target = "autoeq"
    description = "the measured response of one channel of an ARC 4 analysis"
    options = (CHANNEL_OPTION,)

    def __init__(self, channel: str = "left"):
        self.channel = channel_name(channel)

    def _convert(self, path: Path) -> Result:
        a = arc4.load(path)
        frequencies, db = arc4.response(a, a.channel(self.channel))
        return csv_result(list(zip(frequencies, db)), f"{path.stem} {self.channel}.csv",
                          f"{self.channel} response, dB re the 40 Hz-10 kHz mean")


class SwprojToAutoeq(Converter):
    """``--speaker`` selects any channel by its SoundID name; ``--channel`` left or right."""

    source = "swproj"
    target = "autoeq"
    description = "the measurement curve of one channel of a SoundID project"
    options = (
        CHANNEL_OPTION,
        SPEAKER_OPTION,
        swproj.PASSWORD_OPTION,
    )

    def __init__(self, channel: str | None = None, speaker: str | None = None,
                 password: str | None = None):
        if channel is not None and speaker is not None:
            raise ValueError("give --channel or --speaker, not both")
        self.speaker = speaker if speaker is not None else channel_name(channel or "left")
        self.password = swproj.password_bytes(password)

    def _convert(self, path: Path) -> Result:
        curves = swproj.measurement_curves(swproj.SwProj.open(path, self.password))
        names = {name.lower(): name for name in curves}
        if self.speaker.lower() not in names:
            raise missing(self.speaker, curves)
        name = names[self.speaker.lower()]
        points = [(f, r) for f, r, _ in curves[name]]
        return csv_result(points, f"{path.stem} {name}.csv",
                          f"{name} measurement, dB relative to the project reference")


class PeqbToAutoeq(Converter):
    source = "peqb"
    target = "autoeq"
    description = "the response of one side of a SoundID profile (headphone: -correction)"
    options = (CHANNEL_OPTION, peqb.COMPUTER_ID_OPTION, peqb.KEY_OPTION)

    def __init__(self, channel: str = "left", computer_id: str | None = None,
                 key: str | None = None):
        self.channel = channel_name(channel)
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
                return csv_result([(f, r) for f, r, _ in c.points], f"{path.stem} {side}.csv",
                                  f"{side} measurement")
        for c in p.curves:
            if c.type_name == f"Correction{side}":
                return csv_result([(f, -r) for f, r, _ in c.points], f"{path.stem} {side}.csv",
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
        return csv_result(points, f"{path.stem}.csv",
                          f"{filters} enabled filters; the correction band is not part of "
                          "the curve")


class TargetcurveToAutoeq(Converter):
    source = "targetcurve"
    target = "autoeq"
    description = "a Dirac Live target curve, on the standard 355-point grid"

    def _convert(self, path: Path) -> Result:
        curve = targetcurve.load(path)
        curve.validate()
        grid = standard_grid()
        return csv_result(list(zip(grid, curve.response(grid))), f"{path.stem}.csv",
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
        self.channel = channel_name(channel)
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
        return csv_result(points, self.output_name(path, side), f"{side} correction{rate}, dB")

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


class FirToAutoeq(Converter):
    """A mono file is the left channel."""

    source = "fir"
    target = "autoeq"
    description = "the gain of one channel of a FIR filter, on the standard grid"
    options = (CHANNEL_OPTION,)

    def __init__(self, channel: str = "left"):
        self.channel = channel_name(channel)

    def _convert(self, path: Path) -> Result:
        f = fir.load(path)
        index = fir.CHANNEL_NAMES.index(self.channel)
        if index >= len(f.channels):
            raise ValueError(f"no {self.channel} channel; the filter has {len(f.channels)}")
        return csv_result(fir.response(f, index), f"{path.stem} {self.channel}.csv",
                          f"{self.channel} gain of {f.taps} taps at {f.sample_rate:g} Hz, dB")


class DiracFilterToAutoeq(Converter):
    source = "dirac-filter"
    target = "autoeq"
    description = "the gain the Dirac Live Processor plays on one output, on the standard grid"
    options = (SPEAKER_OPTION, RATE_OPTION)

    def __init__(self, speaker: str | None = None, rate: float = DEFAULT_RATE):
        self.speaker = speaker
        self.rate = dirac_rate(rate)

    def _convert(self, path: Path) -> Result:
        slot = filterslot.load(path).slot
        index, name = dirac_output(slot, self.speaker)
        ir, cross = playback.impulse_response(slot, index, self.rate)
        points = fir.response(fir.Fir(self.rate, [ir]), 0)
        return csv_result(points, f"{path.stem} {name}.csv",
                          *dirac_notes(slot, self.rate, [(name, cross)]))
