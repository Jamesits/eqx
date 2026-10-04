"""DRC corrections of measurements, for outputs of curves and filters."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ..curve import log_resample
from ..drc import pcm, run
from ..model import standard_grid
from ..soundid.speakerproject import LEVEL_HIGH_HZ, LEVEL_LOW_HZ
from .base import Converter, Result
from .common import DEFAULT_RATE, RATE_OPTION, median_level, profile_db
from .to_swproj import (
    DRC_OPTIONS,
    MIC_OPTIONS,
    check_mic_options,
    flat_mic_profile,
    mic_profile_of,
)

RUN_OPTIONS = MIC_OPTIONS + DRC_OPTIONS
CHANNELS = ("Left", "Right")


@dataclass
class DrcFilter:
    """A DRC filter and its correction curve."""

    ir: list[float]
    rate: float
    # Added to the filter's gain: the correction level.
    level_db: float
    # (frequency, dB) on the standard grid.
    correction: list[tuple[float, float]]

    def fir(self) -> list[float]:
        """The filter at the correction level."""
        gain = 10 ** (self.level_db / 20)
        return [v * gain for v in self.ir]


def drc_filter(ir: list[float], rate: float, measured=None) -> DrcFilter:
    """``measured``: the (frequency, dB) DRC corrects, at its DRC input level.
    The level puts the corrected curve's 200 Hz-10 kHz median at 0 dB; without
    ``measured``, the filter's own median."""
    if rate < 2 * standard_grid()[-1]:
        raise ValueError(f"--rate {rate:g} Hz: DRC filters need at least 44100 Hz")
    grid = standard_grid()
    db, _ = pcm.response(ir, rate, grid)
    base = [0.0] * len(grid) if measured is None else log_resample(measured, grid)
    level = -statistics.median(
        b + v for f, b, v in zip(grid, base, db) if LEVEL_LOW_HZ <= f <= LEVEL_HIGH_HZ
    )
    return DrcFilter(ir, rate, level, [(f, v + level) for f, v in zip(grid, db)])


class DrcRun:
    """``--drc-config`` and the microphone options: DRC's filter of a curve."""

    def __init__(
        self,
        drc_config: Path,
        drc: str | None = None,
        mic_profile: Path | None = None,
        mic_profile_format: str | None = None,
        mic_angle: str | None = None,
    ):
        check_mic_options(mic_profile, mic_profile_format, mic_angle)
        self.config = Path(drc_config)
        self.program = drc or run.DEFAULT_EXECUTABLE
        self.mic = (mic_profile, mic_profile_format, mic_angle)

    @classmethod
    def of(cls, drc_config: Path | None = None, **options) -> DrcRun | None:
        """None without ``drc_config``; the other options need it."""
        if drc_config is None:
            given = [k for k, v in options.items() if v is not None]
            if given:
                flags = ", ".join("--" + k.replace("_", "-") for k in given)
                raise ValueError(f"{flags} need --drc-config")
            return None
        return cls(drc_config, **options)

    def filters(self, curves: list) -> tuple[list[DrcFilter], list[str]]:
        """The filter of each (frequency, dB) curve; and notes."""
        profile, source = mic_profile_of(*self.mic, flat_mic_profile)
        rate = run.config_rate(self.config)
        out = []
        for points in curves:
            points = [(f, v) for f, v in points if f > 0]
            mic = profile_db(profile, [f for f, _ in points])
            points = [(f, v - g) for (f, v), g in zip(points, mic)]
            level = median_level([("", points)])
            ir, _ = run.run(
                run.input_ir(points, rate, level), self.config, self.program
            )
            out.append(drc_filter(ir, rate, [(f, v - level) for f, v in points]))
        return out, [
            (
                f"DRC: {self.config.name} at {rate} Hz; "
                f"mic table: {profile.name} {profile.angle} ({source})"
            )
        ]


def run_options(kwargs: dict) -> dict:
    """Removes and returns the ``DrcRun`` keywords of ``kwargs``."""
    return {o.dest: kwargs.pop(o.dest) for o in RUN_OPTIONS if o.dest in kwargs}


def check_unused(given: dict, defaults: dict) -> None:
    """Options of the conversion without DRC differ from their defaults."""
    changed = [k for k, v in defaults.items() if given[k] != v]
    if changed:
        flags = ", ".join("--" + k.replace("_", "-") for k in changed)
        raise ValueError(f"{flags} do not apply with --drc-config")


def filter_name(stem: str, label: str = "") -> str:
    """The default output file stem of a DRC correction."""
    return f"{stem} {label} DRC" if label else f"{stem} DRC"


class MeasurementDrc(Converter):
    """Measurements corrected by DRC; ``reader`` (a converter to ``autoeq``
    with ``curve``) reads them with its options.

    ``stereo``: the output holds left and right.  A reader with a ``stereo``
    selection reads both from one input; others take one input per channel.
    """

    reader: ClassVar[type]
    stereo: ClassVar[bool]
    output_options: ClassVar[tuple] = ()
    explicit = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "reader" not in cls.__dict__:
            return
        excluded = cls.reader_excluded()
        cls.options = (
            tuple(o for o in cls.reader.curve_options if o.dest not in excluded)
            + RUN_OPTIONS
            + cls.output_options
        )
        cls.inputs = 2 if cls.stereo and cls.reader.stereo is None else 1

    @classmethod
    def reader_excluded(cls) -> set[str]:
        if cls.stereo and cls.reader.stereo is not None:
            return set(cls.reader.stereo_dests)
        return set()

    def __init__(self, **kwargs):
        drc = run_options(kwargs)
        own = {o.dest for o in self.reader.curve_options} - self.reader_excluded()
        self.reader_options = {k: kwargs.pop(k) for k in list(kwargs) if k in own}
        if drc.get("drc_config") is None:
            raise ValueError("--drc-config is required")
        self.drc = DrcRun(**drc)
        self.setup(**kwargs)

    def setup(self, **options) -> None:
        """The output options."""

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        raise NotImplementedError

    def curves(self, *paths: Path) -> list[tuple[str, str, list, str]]:
        """[(channel, label, points, note)]."""
        if self.stereo and self.reader.stereo is not None:
            out = []
            for channel, selection in zip(CHANNELS, self.reader.stereo):
                r = self.reader(**self.reader_options, **selection)
                out.append((channel, *r.curve(paths[0])))
            return out
        r = self.reader(**self.reader_options)
        return [(c, *r.curve(p)) for c, p in zip(CHANNELS, paths)]

    def _convert(self, *paths: Path) -> Result:
        curves = self.curves(*paths)
        filters, notes = self.drc.filters([points for _, _, points, _ in curves])
        label = "" if self.stereo else curves[0][1]
        return self.write(
            filter_name(paths[0].stem, label),
            [(channel, f) for (channel, *_), f in zip(curves, filters)],
            [note for *_, note in curves] + notes,
        )


class DrcFilterOutput(Converter):
    """DRC's filters (``.pcm``) as an output of ``write``."""

    source = "drc"
    stereo: ClassVar[bool]
    output_options: ClassVar[tuple] = ()
    explicit = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.options = (RATE_OPTION,) + cls.output_options
        cls.inputs = 2 if cls.stereo else 1

    def __init__(self, rate: float = DEFAULT_RATE, **options):
        self.rate = rate
        self.setup(**options)

    def setup(self, **options) -> None:
        """The output options."""

    def write(self, stem: str, filters: list, notes: list[str]) -> Result:
        raise NotImplementedError

    def _convert(self, *paths: Path) -> Result:
        filters = [
            (channel, drc_filter(pcm.load(p), self.rate))
            for channel, p in zip(CHANNELS, paths)
        ]
        return self.write(
            filter_name(paths[0].stem),
            filters,
            [f"DRC filters at {self.rate:g} Hz; level: 200 Hz-10 kHz median at 0 dB"],
        )


def measurement_converters(
    base: type, readers: list[type], target_name: str, description: str
) -> list[type]:
    """One ``base`` subclass (a ``MeasurementDrc``) per reader, named
    ``<reader source>To<target_name>``."""
    return [
        type(
            r.__name__.removesuffix("ToAutoeq") + "To" + target_name,
            (base,),
            {
                "source": r.source,
                "reader": r,
                "description": description,
                "__module__": base.__module__,
            },
        )
        for r in readers
    ]
