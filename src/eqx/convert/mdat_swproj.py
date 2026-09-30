"""REW measurements -> SoundID ``.swproj`` speaker project.

Audio samples are omitted.  Curves are resampled to SoundID's standard
355-point, 20 Hz--22 kHz logarithmic grid.
"""

from __future__ import annotations

import bisect
import math
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .. import formats
from ..model import EPOCH, Measurement, MicProfile
from ..options import Option
from ..rew import mdat
from ..soundid import peqb, swmicpkg, swproj
from .base import Converter, Result

DEFAULT_LOW_CUTOFF_HZ = 60.0
DEFAULT_HIGH_CUTOFF_HZ = 20000.0
# SoundID hard-caps speaker correction boost at this value.
SOUNDID_MAX_BOOST_DB = 12.0
DEFAULT_MAX_BOOST_DB = SOUNDID_MAX_BOOST_DB
LEVEL_LOW_HZ = 200.0
LEVEL_HIGH_HZ = 10000.0
DEFAULT_CLIP_FRACTION = 0.05

GRID_NAME = "LOG_355_20_22000"
NULL_ID = "00000000-0000-0000-0000-000000000000"


def standard_grid() -> list[float]:
    return [20.0 * (22000.0 / 20.0) ** (i / 354.0) for i in range(355)]


def interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation, clamped to the end values."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x) - 1
    x0, x1, y0, y1 = xs[i], xs[i + 1], ys[i], ys[i + 1]
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def resample(frequencies: list[float], values: list[float], grid: list[float]) -> list[float]:
    return [interp(frequencies, values, f) for f in grid]


def quantile(values: list[float], q: float) -> float:
    """Linearly interpolated quantile, ``0 <= q <= 1``."""
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    i = math.floor(position)
    if i + 1 >= len(ordered):
        return ordered[-1]
    return ordered[i] + (position - i) * (ordered[i + 1] - ordered[i])


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------
@dataclass
class SpeakerCurves:
    response: list[float]
    group_delay: list[float]
    correction: list[float]
    correction_group_delay: list[float]


def estimate_reference_spl(
    responses: list[list[float]],
    grid: list[float],
    *,
    low_cutoff_hz: float = DEFAULT_LOW_CUTOFF_HZ,
    high_cutoff_hz: float = DEFAULT_HIGH_CUTOFF_HZ,
    max_boost_db: float = DEFAULT_MAX_BOOST_DB,
    clip_fraction: float = DEFAULT_CLIP_FRACTION,
) -> float:
    """SPL mapped to 0 dB: ``min(median, quantile(clip_fraction) + max_boost_db)``.

    Pools the calibrated dB values of all channels in 200 Hz--10 kHz, limited
    to the correction band.  Unsmoothed measurements swing tens of dB across
    narrow notches; the median ignores them, while a mean or power average is
    pulled around.  The second term lowers the level so that at most
    ``clip_fraction`` of those points need more boost than the cap.
    """
    low = max(LEVEL_LOW_HZ, low_cutoff_hz)
    high = min(LEVEL_HIGH_HZ, high_cutoff_hz)
    if low >= high:
        low, high = low_cutoff_hz, high_cutoff_hz
    values = [v for response in responses for f, v in zip(grid, response) if low <= f <= high]
    if not values:
        raise ValueError("no frequency points in the level band")
    return min(statistics.median(values), quantile(values, clip_fraction) + max_boost_db)


def prepare_speaker_curves(
    measurements: list[Measurement],
    grid: list[float],
    profile_points: list[tuple[float, float]],
    *,
    reference_spl: float | None = None,
    low_cutoff_hz: float = DEFAULT_LOW_CUTOFF_HZ,
    high_cutoff_hz: float = DEFAULT_HIGH_CUTOFF_HZ,
    max_boost_db: float = DEFAULT_MAX_BOOST_DB,
    clip_fraction: float = DEFAULT_CLIP_FRACTION,
) -> tuple[dict[str, SpeakerCurves], float]:
    """Calibrate and normalize first, then constrain the inverse speaker EQ.

    ``reference_spl=None`` estimates the reference from the measurements.
    Returns the curves and the reference SPL used.
    """
    if reference_spl is not None and not math.isfinite(reference_spl):
        raise ValueError("reference SPL must be finite")
    if not (
        math.isfinite(low_cutoff_hz)
        and math.isfinite(high_cutoff_hz)
        and 0 < low_cutoff_hz < high_cutoff_hz
    ):
        raise ValueError("cutoffs must be finite and satisfy 0 < low < high")
    if not (math.isfinite(max_boost_db) and 0 <= max_boost_db <= SOUNDID_MAX_BOOST_DB):
        raise ValueError(f"maximum boost must be between 0 and {SOUNDID_MAX_BOOST_DB:g} dB")
    if not 0 <= clip_fraction <= 1:
        raise ValueError("clip fraction must be between 0 and 1")

    band = [i for i, f in enumerate(grid) if low_cutoff_hz <= f <= high_cutoff_hz]
    if len(band) < 3:
        raise ValueError("correction band must contain at least three frequency points")
    # The standard grid rarely lands exactly on the cutoffs. Keep the nearest
    # in-band samples flat, too, so interpolation from the outer zero samples
    # cannot extend correction below the low cutoff or above the high cutoff.
    first, last = band[0], band[-1]
    mic_freq = [p[0] for p in profile_points]
    mic_gain = [p[1] for p in profile_points]
    calibration = [interp(mic_freq, mic_gain, f) for f in grid]
    # Apply microphone calibration over the WHOLE range.  The table is the
    # microphone's own response, so it is subtracted.
    calibrated = [
        [value - gain for value, gain in
         zip(resample(m.frequencies, m.response, grid), calibration)]
        for m in measurements
    ]
    if not all(math.isfinite(value) for spl in calibrated for value in spl):
        raise ValueError("measurement contains non-finite values")
    if reference_spl is None:
        reference_spl = estimate_reference_spl(
            calibrated, grid,
            low_cutoff_hz=low_cutoff_hz, high_cutoff_hz=high_cutoff_hz,
            max_boost_db=max_boost_db, clip_fraction=clip_fraction,
        )
    curves: dict[str, SpeakerCurves] = {}
    for measurement, spl in zip(measurements, calibrated):
        gd = resample(measurement.frequencies, measurement.group_delay, grid)
        # The same SPL reference is used for both channels, preserving their
        # level balance.
        response = [value - reference_spl for value in spl]
        if not all(math.isfinite(value) for value in response + gd):
            raise ValueError(f"{measurement.channel} curve contains non-finite values")
        correction = [
            min(-value, max_boost_db) if first < i < last else 0.0
            for i, value in enumerate(response)
        ]
        correction_gd = [-value if first < i < last else 0.0 for i, value in enumerate(gd)]
        curves[measurement.channel] = SpeakerCurves(response, gd, correction, correction_gd)
    return curves, reference_spl


def _channel_params(measurement: Measurement, correction: bool) -> dict[str, Any]:
    # SoundID writes negated zero on correction curves.
    zero = "-0" if correction else "0"
    return {
        "ChannelDelayMs": zero,
        "ChannelGroup": "Front",
        "ChannelIndex": measurement.index,
        "ChannelName": measurement.channel,
        "Delay": zero,
        "Frequency": GRID_NAME,
        "Transfer": zero,
    }


def _correction_type(measurement: Measurement) -> str:
    return "CorrectionLeft" if measurement.index == 0 else "CorrectionRight"


# ---------------------------------------------------------------------------
# project XML
# ---------------------------------------------------------------------------
NS_SW = swproj.NS["s"]
NS_A = swproj.NS["a"]
NS_L = swproj.NS["l"]
NS_P = swproj.NS["p"]
for _prefix, _uri in (("", NS_SW), ("i", swproj.NS["i"]), ("a", NS_A), ("l", NS_L), ("p", NS_P)):
    ET.register_namespace(_prefix, _uri)


def _element(parent: ET.Element, name: str, text: Any = None) -> ET.Element:
    element = ET.SubElement(parent, f"{{{NS_SW}}}{name}")
    if text is not None:
        element.text = str(text)
    return element


def _db_stamp(parent: ET.Element) -> None:
    _element(parent, "DbId", "0")
    _element(parent, "DbModificationTime", EPOCH)
    _element(parent, "ModificationTime", EPOCH)


def _key_values(parent: ET.Element, values: Iterable[tuple[str, Any]]) -> None:
    """Append DataContract string pairs below an already-selected parent."""
    for key, value in values:
        item = ET.SubElement(parent, f"{{{NS_A}}}KeyValueOfstringstring")
        ET.SubElement(item, f"{{{NS_A}}}Key").text = str(key)
        ET.SubElement(item, f"{{{NS_A}}}Value").text = str(value)


def _params(parent: ET.Element, values: Iterable[tuple[str, Any]]) -> None:
    _key_values(_element(parent, "Parameters"), values)


def _point_list(parent: ET.Element, grid: list[float], response: list[float], gd: list[float]) -> None:
    point_list = ET.SubElement(_element(parent, "Points"), f"{{{NS_L}}}list")
    for frequency, value, group_delay in zip(grid, response, gd):
        point = _element(point_list, "AflPoint")
        _element(point, "Frequency", repr(float(frequency)))
        _element(point, "GroupDelay", repr(float(group_delay)))
        _element(point, "Response", repr(float(value)))


def _curve(
    parent: ET.Element,
    curve_type: str,
    name: str,
    grid: list[float],
    response: list[float],
    gd: list[float],
    params: Iterable[tuple[str, Any]],
) -> ET.Element:
    curve = _element(parent, "Curve")
    _db_stamp(curve)
    _element(curve, "CurveType", curve_type)
    _element(curve, "Id", NULL_ID)
    _element(curve, "Name", name)
    _params(curve, params)
    _point_list(curve, grid, response, gd)
    return curve


def build_project_xml(
    measurements: list[Measurement],
    grid: list[float],
    profile: MicProfile,
    corrected: dict[str, SpeakerCurves],
    name: str,
) -> bytes:
    root = ET.Element(f"{{{NS_SW}}}Project")
    _db_stamp(root)
    _element(root, "CurveCollections")
    curves = _element(root, "Curves")

    for measurement in measurements:
        speaker = corrected[measurement.channel]
        index = measurement.index
        _curve(curves, "Measurement", f"Balanced Measurement CH {index}", grid,
               speaker.response, speaker.group_delay,
               _channel_params(measurement, False).items())
        _curve(curves, _correction_type(measurement), f"Correction CH {index}", grid,
               speaker.correction, speaker.correction_group_delay,
               _channel_params(measurement, True).items())

    mic_freq = [p[0] for p in profile.points]
    mic_response = [p[1] for p in profile.points]
    _curve(curves, "Correction", f"{profile.name} {profile.angle}", mic_freq, mic_response,
           [0.0] * len(mic_freq), (("MicDegrees", profile.angle),))

    frequency_collections = _element(root, "FrequencyCollections")
    frequency_collection = ET.SubElement(frequency_collections, f"{{{NS_P}}}FrequencyCollection")
    info = _element(frequency_collection, "CollectionInfo")
    _element(info, "Custom", "false")
    _element(info, "Name", GRID_NAME)
    _element(info, "AssignedName", "Default 355")
    _element(info, "StartFrequency", "20")
    _element(info, "EndFrequency", "22000")
    _element(info, "PointCount", len(grid))
    _element(info, "Logarithmic", "true")
    frequencies = _element(frequency_collection, "Frequencies")
    for frequency in grid:
        _element(frequencies, "Value", repr(float(frequency)))

    _element(root, "FrequencyName", GRID_NAME)
    _element(root, "Id", NULL_ID)
    _element(root, "MeasurementCollections")
    _element(root, "Measurements")
    _element(root, "Name", name)

    room_collections = _element(root, "RoomMeasurementCollections")
    room_collection = _element(room_collections, "RoomMeasurementCollection")
    _db_stamp(room_collection)
    _element(room_collection, "FrequencyName", GRID_NAME)
    _element(room_collection, "Id", NULL_ID)
    room_measurements = _element(room_collection, "Measurements")
    _element(room_collection, "Name")
    _params(
        room_collection,
        (
            ("ChannelLayout", "0"),
            ("DistanceBetweenSpeakers", "0"),
            ("IsLfeLouder", "false"),
            ("MeasuredDistanceBetweenSpeakers", "0"),
            ("RoomReverbation", "0"),
            ("SampleRate", measurements[0].sample_rate),
            ("SessionId", "0"),
            ("deviceType", "Other"),
        ),
    )

    rm = _element(room_measurements, "RoomMeasurement")
    _db_stamp(rm)
    rms = _element(rm, "Measurements")
    _params(
        rm,
        (
            ("MainPointType", "Chair"),
            ("MeasurementType", "MainPoint"),
            ("Position_X", "0"),
            ("Position_Y", "0"),
            ("SessionId", "0"),
            ("Size_H", "0.35"),
            ("Size_W", "0.35"),
        ),
    )
    room_points = _element(rm, "Points")
    for measurement in measurements:
        point = _element(room_points, "RoomPoint")
        _db_stamp(point)
        _element(point, "Channel", measurement.channel)
        _element(point, "Distance", "0")
        _element(point, "DistanceInSamples", "0")
        _element(point, "InputLevel", "0")
        _element(point, "OutputLevel", "0")
        _params(point, (("ChannelIndex", measurement.index),))

    for measurement in measurements:
        speaker = corrected[measurement.channel]
        m = _element(rms, "Measurement")
        _element(m, "Channel", measurement.channel)
        _element(m, "Delay", "0")
        _params(m, (("ChannelIndex", measurement.index),))
        _point_list(m, grid, speaker.response, speaker.group_delay)
        _element(m, "RawData")  # optional audio samples intentionally omitted
        _element(m, "Time", measurement.timestamp)
        _element(m, "Transfer", "0")
    _element(rm, "Time", max((m.timestamp for m in measurements if m.timestamp), default=EPOCH))

    values = _element(root, "Values")
    _key_values(values, (("VersionHistory", "3.0.0.2_1"),))
    ET.indent(root, "\t")
    xml = ET.tostring(root, encoding="utf-8")
    # DataContract emits this declaration on projects written by SoundID even
    # when no element uses xsi attributes.  Keep the same root namespace set.
    xml = xml.replace(
        f'<Project xmlns="{NS_SW}"'.encode(),
        f'<Project xmlns="{NS_SW}" xmlns:i="{swproj.NS["i"]}"'.encode(),
        1,
    )
    return xml + b"\n"


# ---------------------------------------------------------------------------
# PEQb part
# ---------------------------------------------------------------------------
def build_eqb(
    measurements: list[Measurement], grid: list[float], corrected: dict[str, SpeakerCurves]
) -> bytes:
    # PEQb v3.0.0.2 stores each speaker's Measurement together with its
    # CorrectionLeft/CorrectionRight curve. Use the same constrained correction
    # as the XML; do not invert the measurement again here, bypassing the limits.
    # Both curves must be present for the app to switch presets successfully.
    curves = []
    for measurement in measurements:
        speaker = corrected[measurement.channel]
        curves.append(peqb.Curve(
            peqb.CURVE_TYPE_ID["Measurement"],
            list(zip(grid, speaker.response, speaker.group_delay)),
            _channel_params(measurement, False),
            flags=peqb.F_FREQUENCY | peqb.F_RESPONSE | peqb.F_PARAMETERS,
        ))
        curves.append(peqb.Curve(
            peqb.CURVE_TYPE_ID[_correction_type(measurement)],
            list(zip(grid, speaker.correction, speaker.correction_group_delay)),
            _channel_params(measurement, True),
            flags=peqb.F_FREQUENCY | peqb.F_RESPONSE | peqb.F_GROUP_DELAY | peqb.F_PARAMETERS,
        ))
    return peqb.write(curves)


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------
@dataclass
class Conversion:
    data: bytes                         # complete .swproj file
    grid: list[float]
    curves: dict[str, SpeakerCurves]
    reference_spl: float                # calibrated SPL mapped to 0 dB


def convert(
    measurements: list[Measurement],
    profile: MicProfile,
    name: str,
    *,
    reference_spl: float | None = None,
    low_cutoff_hz: float = DEFAULT_LOW_CUTOFF_HZ,
    high_cutoff_hz: float = DEFAULT_HIGH_CUTOFF_HZ,
    max_boost_db: float = DEFAULT_MAX_BOOST_DB,
    clip_fraction: float = DEFAULT_CLIP_FRACTION,
) -> Conversion:
    grid = standard_grid()
    corrected, reference_spl = prepare_speaker_curves(
        measurements,
        grid,
        profile.points,
        reference_spl=reference_spl,
        low_cutoff_hz=low_cutoff_hz,
        high_cutoff_hz=high_cutoff_hz,
        max_boost_db=max_boost_db,
        clip_fraction=clip_fraction,
    )
    xml = build_project_xml(measurements, grid, profile, corrected, name)
    eqb = build_eqb(measurements, grid, corrected)
    return Conversion(swproj.write(xml, eqb), grid, corrected, reference_spl)


MIC_PROFILE_FORMATS = ("swmicpkg", "swproj")


def load_mic_profile(path: Path, angle: str = swmicpkg.PLAIN_ANGLE,
                     kind: str | None = None) -> MicProfile:
    """One microphone table from a ``.swmicpkg``, or from a ``.swproj`` measured with it.

    ``kind`` None detects the format by extension.
    """
    kind = kind or formats.detect(path, MIC_PROFILE_FORMATS)
    if kind == "swmicpkg":
        return swmicpkg.load(path, angle)
    if kind == "swproj":
        return swproj.mic_profile(swproj.SwProj.open(path), angle)
    raise ValueError(f"{Path(path).name}: a microphone profile must be .swmicpkg or .swproj")


class SpeakerProjectConverter(Converter):
    """Speaker measurements as a SoundID speaker project; subclasses read the measurements."""

    target = "swproj"
    options = (
        Option("--mic-profile", type=Path,
               help="required: SoundID microphone package (.swmicpkg), or a .swproj "
                    "measured with the microphone"),
        Option("--mic-profile-format", choices=MIC_PROFILE_FORMATS,
               help="format of --mic-profile (default: by extension)"),
        Option("--mic-angle",
               help="angle between mic axis and speaker: degrees_0, degrees_30 or "
                    f"degrees_90 (default: {swmicpkg.PLAIN_ANGLE})"),
        Option("--reference-spl", type=float,
               help="calibrated SPL mapped to 0 dB for both channels "
                    "(default: estimated from the 200 Hz-10 kHz level)"),
        Option("--low-cutoff-hz", type=float,
               help=f"no speaker EQ below this frequency (default: {DEFAULT_LOW_CUTOFF_HZ:g} Hz)"),
        Option("--high-cutoff-hz", type=float,
               help=f"no speaker EQ above this frequency (default: {DEFAULT_HIGH_CUTOFF_HZ:g} Hz)"),
        Option("--max-boost-db", type=float,
               help=f"maximum positive speaker EQ gain, at most +{SOUNDID_MAX_BOOST_DB:g} "
                    f"(default: +{DEFAULT_MAX_BOOST_DB:g} dB)"),
        Option("--clip-percent", type=float,
               help="estimated reference only: percent of 200 Hz-10 kHz points allowed to "
                    f"need more than the maximum boost (default: {DEFAULT_CLIP_FRACTION * 100:g})"),
    )

    def __init__(
        self,
        mic_profile: Path | None = None,
        mic_profile_format: str | None = None,
        mic_angle: str = swmicpkg.PLAIN_ANGLE,
        reference_spl: float | None = None,
        low_cutoff_hz: float = DEFAULT_LOW_CUTOFF_HZ,
        high_cutoff_hz: float = DEFAULT_HIGH_CUTOFF_HZ,
        max_boost_db: float = DEFAULT_MAX_BOOST_DB,
        clip_percent: float = DEFAULT_CLIP_FRACTION * 100,
    ):
        if mic_profile is None:
            raise ValueError(f"{self.source} -> {self.target} needs --mic-profile")
        self.mic_profile = Path(mic_profile)
        self.mic_profile_format = mic_profile_format
        self.mic_angle = mic_angle
        self.settings = dict(
            reference_spl=reference_spl,
            low_cutoff_hz=low_cutoff_hz,
            high_cutoff_hz=high_cutoff_hz,
            max_boost_db=max_boost_db,
            clip_fraction=clip_percent / 100,
        )

    def measurements(self, path: Path) -> list[Measurement]:
        """The measurements in ``path``, left channel first."""
        raise NotImplementedError

    def convert(self, path: Path) -> Result:
        path = Path(path)
        measurements = self.measurements(path)
        profile = load_mic_profile(self.mic_profile, self.mic_angle,
                                   self.mic_profile_format)
        result = convert(measurements, profile, path.stem, **self.settings)
        s = self.settings
        source = "estimated" if s["reference_spl"] is None else "given"
        notes = [
            f"measurements: {len(measurements)}; frequency points: {len(result.grid)}; "
            f"mic table: {profile.name} {profile.angle}",
            f"reference: {result.reference_spl:.1f} dB SPL = 0 dB ({source}); correction band: "
            f"{s['low_cutoff_hz']:g}-{s['high_cutoff_hz']:g} Hz; "
            f"maximum boost: {s['max_boost_db']:g} dB",
        ]
        notes += [f"{channel} correction: {min(c.correction):+.2f} to {max(c.correction):+.2f} dB"
                  for channel, c in result.curves.items()]
        return Result(result.data, path.stem + ".swproj", notes)


class MdatToSwproj(SpeakerProjectConverter):
    source = "mdat"
    description = "REW speaker measurements as a SoundID speaker project (no audio samples)"

    def measurements(self, path: Path) -> list[Measurement]:
        return mdat.load(path)
