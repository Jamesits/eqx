"""SoundID profiles, AutoEq CSV, Dirac Live Processor filter slot, dearVR MIX headphone
compensation, measurements corrected by DRC, DRC filters -> FIR filter WAV."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import dsp, fmath
from ..autoeq import response
from ..dirac import filterslot
from ..dirac import playback as dirac_playback
from ..options import Option
from ..sennheiser import hpc
from ..soundid import peqb, playback, swproj
from ..wav import fir
from .base import Converter, Result
from .common import (
    COLUMN_OPTION,
    DEFAULT_RATE,
    PHASE_OPTION,
    RATE_OPTION,
    curves,
    dearvr_filter,
    dirac_notes,
    dirac_rate,
    file_name,
)
from .to_autoeq import MEASUREMENTS, AutoeqToAutoeq, SwprojToAutoeq
from .with_drc import (
    RUN_OPTIONS,
    DrcFilterOutput,
    DrcRun,
    MeasurementDrc,
    check_unused,
    measurement_converters,
)

TAPS_OPTION = Option(
    "--taps",
    type=int,
    help="filter length (default: SoundID's, at 48 kHz 4093 minimum phase, "
    "4353 linear and mixed phase)",
)
NYQUIST_NOTCH_OPTION = Option(
    "--nyquist-notch",
    action=argparse.BooleanOptionalAction,
    help="leave the Nyquist bin undelayed, as SoundID: with an odd latency "
    "(32, 96, 192 kHz linear phase) a notch at Nyquist (default: off)",
)
GRID_FACTOR_OPTION = Option(
    "--grid-factor",
    type=int,
    help="design grid of at least this many times the filter length, at least "
    "32768 points; 1 as SoundID (default: 2)",
)
ENCODING_OPTION = Option(
    "--encoding",
    choices=tuple(fir.ENCODINGS),
    help="WAV sample format; Smaart's IR mode needs PCM, e.g. pcm24 (default: float32)",
)
SAFE_HEADROOM_OPTION = Option(
    "--safe-headroom",
    action=argparse.BooleanOptionalAction,
    help="lower the gain by the highest boost, as SoundID's Safe Headroom (default: on)",
)


def _check(
    phase: str,
    rate: float,
    taps: int | None,
    encoding: str = fir.FLOAT32,
    grid_factor: int = dsp.GRID_FACTOR,
) -> None:
    if encoding not in fir.ENCODINGS:
        raise ValueError(f"--encoding must be one of: {', '.join(fir.ENCODINGS)}")
    if phase not in dsp.PHASES:
        raise ValueError(f"phase must be one of: {', '.join(dsp.PHASES)}")
    if not rate > 0:
        raise ValueError("the sample rate must be positive")
    if taps is not None and (taps < 3 or (phase == "linear" and taps % 2 == 0)):
        raise ValueError("--taps must be at least 3, and odd for linear phase")
    if not isinstance(grid_factor, int) or grid_factor < 1:
        raise ValueError("--grid-factor must be a positive integer")


def _result(
    channels: list[list[float]], rate: float, encoding: str, name: str, notes: list[str]
) -> Result:
    """The WAV; PCM beyond full scale is scaled down to it, with a note."""
    if encoding != fir.FLOAT32:
        peak = max(abs(v) for c in channels for v in c)
        if peak > 1.0:
            channels = [[v / peak for v in c] for c in channels]
            notes = notes + [
                f"scaled by {-20 * fmath.log10(peak):.2f} dB to fit {encoding}"
            ]
    return Result(fir.write(fir.Fir(rate, channels), encoding), name, notes)


def _notes(
    channels: list[list[float]], rate: float, phase: str, taps: int | None = None
) -> list[str]:
    """``taps``: the filter length before the listening spot delays."""
    latency = dsp.fir_latency(taps or dsp.fir_taps(rate, phase), phase)
    taps = len(channels[0])
    return [
        (
            f"{len(channels)} channel(s), {taps} taps at {rate:g} Hz, {phase} phase, "
            f"latency {latency} samples ({1000 * latency / rate:.2f} ms)"
        )
    ]


def _drc_result(stem: str, filters: list, notes: list[str], encoding: str) -> Result:
    """DRC's filters at the correction level, zero-padded to one length."""
    irs = [f.fir() for _, f in filters]
    taps = max(len(ir) for ir in irs)
    irs = [ir + [0.0] * (taps - len(ir)) for ir in irs]
    rate = filters[0][1].rate
    return _result(
        irs,
        rate,
        encoding,
        stem + ".wav",
        [f"{len(irs)} channel(s), {taps} taps at {rate:g} Hz, DRC's phase"]
        + notes
        + [f"{channel} level {f.level_db:+.2f} dB" for channel, f in filters],
    )


class MeasurementDrcToFir(MeasurementDrc):
    target = "fir"
    stereo = True
    output_options = (ENCODING_OPTION,)

    def setup(self, encoding: str = fir.FLOAT32) -> None:
        if encoding not in fir.ENCODINGS:
            raise ValueError(f"--encoding must be one of: {', '.join(fir.ENCODINGS)}")
        self.encoding = encoding

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        return _drc_result(stem, filters, notes, self.encoding)


class AutoeqDrcToFir(MeasurementDrcToFir):
    """``AutoeqToFir`` with --drc-config."""

    source = "autoeq"
    reader = AutoeqToAutoeq
    description = ""


class SwprojDrcToFir(MeasurementDrcToFir):
    """``SwprojToFir`` with --drc-config."""

    source = "swproj"
    reader = SwprojToAutoeq
    description = ""


class PeqbToFir(Converter):
    source = "peqb"
    target = "fir"
    description = "the stereo filter SoundID Reference plays for a headphone profile"
    options = (
        PHASE_OPTION,
        RATE_OPTION,
        TAPS_OPTION,
        NYQUIST_NOTCH_OPTION,
        GRID_FACTOR_OPTION,
        peqb.COMPUTER_ID_OPTION,
        peqb.KEY_OPTION,
        SAFE_HEADROOM_OPTION,
        ENCODING_OPTION,
    )

    def __init__(
        self,
        phase: str = "minimum",
        rate: float = DEFAULT_RATE,
        taps: int | None = None,
        computer_id: str | None = None,
        key: str | None = None,
        safe_headroom: bool = True,
        encoding: str = fir.FLOAT32,
        nyquist_notch: bool = dsp.NYQUIST_NOTCH,
        grid_factor: int = dsp.GRID_FACTOR,
    ):
        _check(phase, rate, taps, encoding, grid_factor)
        self.encoding = encoding
        peqb.key_for(computer_id, key)
        self.phase, self.rate, self.taps = phase, rate, taps
        self.computer_id, self.key = computer_id, key
        self.safe_headroom = safe_headroom
        self.nyquist_notch, self.grid_factor = nyquist_notch, grid_factor

    def _convert(self, path: Path) -> Result:
        p = peqb.open_decoded(path.read_bytes(), self.computer_id, self.key)
        channels, gain = playback.headphone_fir(
            p,
            self.rate,
            self.phase,
            self.taps,
            self.safe_headroom,
            self.nyquist_notch,
            self.grid_factor,
        )
        return _result(
            channels,
            self.rate,
            self.encoding,
            f"{path.stem}.wav",
            _notes(channels, self.rate, self.phase, self.taps)
            + [f"gain {gain:.2f} dB"],
        )


class SwprojToFir(Converter):
    """One channel per project channel, in channel order.  With --drc-config,
    DRC's filters of the left and right measurements instead."""

    source = "swproj"
    target = "fir"
    description = (
        "the filter SoundID Reference plays for a speaker project; or DRC's "
        "filters of its measurements"
    )
    options = (
        PHASE_OPTION,
        RATE_OPTION,
        TAPS_OPTION,
        NYQUIST_NOTCH_OPTION,
        GRID_FACTOR_OPTION,
        swproj.PASSWORD_OPTION,
        SAFE_HEADROOM_OPTION,
        Option(
            "--listening-spot",
            action=argparse.BooleanOptionalAction,
            help="apply the project's listening spot delay and gain, as SoundID's "
            "Listening Spot (default: on)",
        ),
        Option(
            "--limit-correction",
            type=float,
            choices=playback.LIMIT_CORRECTION_DB,
            help="maximum boost, dB, as SoundID's Limit Controls Correction (default: 12)",
        ),
        Option(
            "--limit-low",
            choices=playback.LIMIT_LOW,
            help="SoundID's Limit Controls Max low frequencies (default: neutral)",
        ),
        Option(
            "--limit-high",
            choices=playback.LIMIT_HIGH,
            help="SoundID's Limit Controls Max high frequencies (default: neutral)",
        ),
        ENCODING_OPTION,
    ) + RUN_OPTIONS

    def __init__(
        self,
        phase: str = "minimum",
        rate: float = DEFAULT_RATE,
        taps: int | None = None,
        password: str | None = None,
        safe_headroom: bool = True,
        listening_spot: bool = True,
        limit_correction: float = 12.0,
        limit_low: str = "neutral",
        limit_high: str = "neutral",
        encoding: str = fir.FLOAT32,
        nyquist_notch: bool = dsp.NYQUIST_NOTCH,
        grid_factor: int = dsp.GRID_FACTOR,
        **drc,
    ):
        _check(phase, rate, taps, encoding, grid_factor)
        self.drc = None
        if DrcRun.of(**drc):
            check_unused(
                locals(),
                {
                    "phase": "minimum",
                    "rate": DEFAULT_RATE,
                    "taps": None,
                    "safe_headroom": True,
                    "listening_spot": True,
                    "limit_correction": 12.0,
                    "limit_low": "neutral",
                    "limit_high": "neutral",
                    "nyquist_notch": dsp.NYQUIST_NOTCH,
                    "grid_factor": dsp.GRID_FACTOR,
                },
            )
            self.drc = SwprojDrcToFir(password=password, encoding=encoding, **drc)
        self.encoding = encoding
        self.nyquist_notch, self.grid_factor = nyquist_notch, grid_factor
        if limit_correction not in playback.LIMIT_CORRECTION_DB:
            raise ValueError("--limit-correction must be 12, 6 or 0")
        if limit_low not in playback.LIMIT_LOW or limit_high not in playback.LIMIT_HIGH:
            raise ValueError(
                f"--limit-low: one of {', '.join(playback.LIMIT_LOW)}; "
                f"--limit-high: one of {', '.join(playback.LIMIT_HIGH)}"
            )
        self.phase, self.rate, self.taps = phase, rate, taps
        self.password = swproj.password_bytes(password)
        self.safe_headroom, self.listening_spot = safe_headroom, listening_spot
        self.limits = (limit_correction, limit_low, limit_high)

    def _convert(self, path: Path) -> Result:
        if self.drc is not None:
            return self.drc.convert([path])
        channels, irs, gain = playback.speaker_fir(
            swproj.SwProj.open(path, self.password),
            self.rate,
            self.phase,
            self.taps,
            self.safe_headroom,
            self.listening_spot,
            *self.limits,
            nyquist_notch=self.nyquist_notch,
            grid_factor=self.grid_factor,
        )
        notes = _notes(irs, self.rate, self.phase, self.taps) + [
            (
                f"channels: {', '.join(c.name for c in channels)}; gain {gain:.2f} dB; "
                f"limits: {self.limits[0]:g} dB, low {self.limits[1]}, high {self.limits[2]}"
            )
        ]
        if self.listening_spot:
            top = max(c.gain_db for c in channels)
            first = min(c.delay_ms for c in channels)
            notes += [
                f"{c.name} listening spot: "
                f"{playback.spot_samples(c.delay_ms - first, self.rate)} samples, "
                f"{c.gain_db - top:+.2f} dB"
                for c in channels
                if c.delay_ms != first or c.gain_db != top
            ]
        return _result(irs, self.rate, self.encoding, f"{path.stem}.wav", notes)


class AutoeqToFir(Converter):
    """The curve is the filter's gain; DC and Nyquist keep the end values.
    With --drc-config the curve is a measurement; the FIR is DRC's filter."""

    source = "autoeq"
    target = "fir"
    description = (
        "a curve (inputs: left, optional right) as a FIR filter; or DRC's "
        "filters of measurements"
    )
    options = (
        COLUMN_OPTION,
        PHASE_OPTION,
        RATE_OPTION,
        TAPS_OPTION,
        NYQUIST_NOTCH_OPTION,
        GRID_FACTOR_OPTION,
        ENCODING_OPTION,
    ) + RUN_OPTIONS
    inputs = 2

    def __init__(
        self,
        column: str = response.RAW,
        phase: str = "minimum",
        rate: float = DEFAULT_RATE,
        taps: int | None = None,
        encoding: str = fir.FLOAT32,
        nyquist_notch: bool = dsp.NYQUIST_NOTCH,
        grid_factor: int = dsp.GRID_FACTOR,
        **drc,
    ):
        _check(phase, rate, taps, encoding, grid_factor)
        self.drc = None
        if DrcRun.of(**drc):
            check_unused(
                locals(),
                {
                    "phase": "minimum",
                    "rate": DEFAULT_RATE,
                    "taps": None,
                    "nyquist_notch": dsp.NYQUIST_NOTCH,
                    "grid_factor": dsp.GRID_FACTOR,
                },
            )
            self.drc = AutoeqDrcToFir(column=column, encoding=encoding, **drc)
        self.encoding = encoding
        self.column = column
        self.phase, self.rate, self.taps = phase, rate, taps
        self.nyquist_notch, self.grid_factor = nyquist_notch, grid_factor

    def _convert(self, *paths: Path) -> Result:
        if self.drc is not None:
            return self.drc.convert(paths)
        channels = [
            dsp.design_fir_points(
                points,
                self.rate,
                self.phase,
                self.taps,
                nyquist_notch=self.nyquist_notch,
                grid_factor=self.grid_factor,
            )
            for _, points in curves(paths, self.column)
        ]
        return _result(
            channels,
            self.rate,
            self.encoding,
            f"{paths[0].stem}.wav",
            _notes(channels, self.rate, self.phase, self.taps),
        )


class DiracFilterToFir(Converter):
    """One WAV channel per output, in output order."""

    source = "dirac-filter"
    target = "fir"
    description = (
        "the impulse responses the Dirac Live Processor plays, as a FIR filter"
    )
    options = (RATE_OPTION, ENCODING_OPTION)

    def __init__(self, rate: float = DEFAULT_RATE, encoding: str = fir.FLOAT32):
        self.rate = dirac_rate(rate)
        _check("minimum", self.rate, None, encoding)
        self.encoding = encoding

    def _convert(self, path: Path) -> Result:
        slot = filterslot.load(path).slot
        names = dirac_playback.output_names(slot)
        played = [
            dirac_playback.impulse_response(slot, i, self.rate)
            for i in range(len(names))
        ]
        channels = dsp.padded([ir for ir, _ in played])
        return _result(
            channels,
            self.rate,
            self.encoding,
            f"{path.stem}.wav",
            [f"{len(channels)} channel(s): {', '.join(names)}; {len(channels[0])} taps"]
            + dirac_notes(
                slot, self.rate, [(n, c) for n, (_, c) in zip(names, played)]
            ),
        )


class DearvrHpcToFir(Converter):
    source = "dearvr-hpc"
    target = "fir"
    description = "one dearVR MIX headphone compensation filter, as stored"
    options = (hpc.HEADPHONE_OPTION, PHASE_OPTION, RATE_OPTION, ENCODING_OPTION)

    def __init__(
        self,
        headphone: str | None = None,
        phase: str = "minimum",
        rate: float = DEFAULT_RATE,
        encoding: str = fir.FLOAT32,
    ):
        _check(phase, rate, None, encoding)
        if phase not in hpc.PHASES:
            raise ValueError(
                f"dearVR MIX phase must be one of: {', '.join(hpc.PHASES)}"
            )
        self.headphone, self.phase, self.rate, self.encoding = (
            headphone,
            phase,
            rate,
            encoding,
        )

    def _convert(self, path: Path) -> Result:
        h, f, notes = dearvr_filter(path, self.headphone, self.phase, self.rate)
        channels = f.channels()
        return _result(
            channels,
            f.sample_rate,
            self.encoding,
            f"{file_name(h.name)}.wav",
            [
                (
                    f"{len(channels)} channel(s), {f.taps} taps at {f.sample_rate} Hz, "
                    f"{f.phase} phase, latency {hpc.peak(f)} samples "
                    f"({1000 * hpc.peak(f) / f.sample_rate:.2f} ms)"
                ),
                *notes,
            ],
        )


MEASUREMENTS_TO_FIR = measurement_converters(
    MeasurementDrcToFir,
    [r for r in MEASUREMENTS if r.source not in ("autoeq", "swproj")],
    "Fir",
    "measurements corrected by DRC (--drc-config required): DRC's filters as "
    "a FIR filter",
)


class DrcToFir(DrcFilterOutput):
    target = "fir"
    description = "DRC filters (inputs: left, optional right) as a FIR filter"
    stereo = True
    output_options = (ENCODING_OPTION,)

    def setup(self, encoding: str = fir.FLOAT32) -> None:
        if encoding not in fir.ENCODINGS:
            raise ValueError(f"--encoding must be one of: {', '.join(fir.ENCODINGS)}")
        self.encoding = encoding

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        return _drc_result(stem, filters, notes, self.encoding)
