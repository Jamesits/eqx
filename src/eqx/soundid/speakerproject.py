"""SoundID speaker project writer: measurements -> ``.swproj``.

Audio samples are omitted.  Curves are resampled to SoundID's standard
355-point, 20 Hz--22 kHz logarithmic grid.
"""

from __future__ import annotations

import base64
import json
import math
import statistics
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..curve import quantile, resample
from ..model import EPOCH, Measurement, MicProfile, standard_grid
from . import layout as sid_layout
from . import peqb, swproj

DEFAULT_LOW_CUTOFF_HZ = 60.0
DEFAULT_HIGH_CUTOFF_HZ = 20000.0
# The LFE channel carries 120 Hz and below.
DEFAULT_LFE_HIGH_CUTOFF_HZ = 120.0
# SoundID hard-caps speaker correction boost at this value.
SOUNDID_MAX_BOOST_DB = 12.0
DEFAULT_MAX_BOOST_DB = SOUNDID_MAX_BOOST_DB
LEVEL_LOW_HZ = 200.0
LEVEL_HIGH_HZ = 10000.0
DEFAULT_CLIP_FRACTION = 0.05

GRID_NAME = "LOG_355_20_22000"
NULL_ID = "00000000-0000-0000-0000-000000000000"
# Target applications: SoundID Reference, or Sonarworks Reference 3 and 4.
APPS = ("soundid", "sonarworks-reference")


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------
@dataclass
class SpeakerCurves:
    response: list[float]
    group_delay: list[float]
    correction: list[float]
    correction_group_delay: list[float]


def _level_band(low_cutoff_hz: float, high_cutoff_hz: float) -> tuple[float, float]:
    """200 Hz--10 kHz limited to the correction band; the band if they do not overlap."""
    low = max(LEVEL_LOW_HZ, low_cutoff_hz)
    high = min(LEVEL_HIGH_HZ, high_cutoff_hz)
    return (low, high) if low < high else (low_cutoff_hz, high_cutoff_hz)


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

    Pools the calibrated dB values of all given channels in 200 Hz--10 kHz, limited
    to the correction band.  Unsmoothed measurements swing tens of dB across
    narrow notches; the median ignores them, while a mean or power average is
    pulled around.  The second term lowers the level so that at most
    ``clip_fraction`` of those points need more boost than the cap.
    """
    low, high = _level_band(low_cutoff_hz, high_cutoff_hz)
    values = [
        v for response in responses for f, v in zip(grid, response) if low <= f <= high
    ]
    if not values:
        raise ValueError("no frequency points in the level band")
    return min(
        statistics.median(values), quantile(values, clip_fraction) + max_boost_db
    )


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
    lfe: Iterable[str] = (),
    lfe_high_cutoff_hz: float = DEFAULT_LFE_HIGH_CUTOFF_HZ,
    filters: dict[str, tuple[list[float], list[float]]] | None = None,
) -> tuple[dict[str, SpeakerCurves], float]:
    """Calibrate and normalize first, then constrain the speaker EQ.

    ``reference_spl=None`` estimates the reference from the measurements.
    ``lfe`` names the LFE channels: their band ends at ``lfe_high_cutoff_hz``
    and they are left out of the reference estimate.
    ``filters``: {channel: (dB, group delay s) on ``grid``}, a correction
    filter used instead of the inverse response.  Its level is moved so that
    the corrected response has a 0 dB median in the level band.
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
    if not (math.isfinite(lfe_high_cutoff_hz) and lfe_high_cutoff_hz > 0):
        raise ValueError("LFE cutoff must be finite and positive")
    if not (math.isfinite(max_boost_db) and 0 <= max_boost_db <= SOUNDID_MAX_BOOST_DB):
        raise ValueError(
            f"maximum boost must be between 0 and {SOUNDID_MAX_BOOST_DB:g} dB"
        )
    if not 0 <= clip_fraction <= 1:
        raise ValueError("clip fraction must be between 0 and 1")

    lfe = set(lfe)
    bands = {False: _band(grid, low_cutoff_hz, high_cutoff_hz)}
    if any(m.channel in lfe for m in measurements):
        bands[True] = _band(
            grid, low_cutoff_hz, min(high_cutoff_hz, lfe_high_cutoff_hz)
        )
    calibration = resample(
        [f for f, _ in profile_points], [g for _, g in profile_points], grid
    )
    # Apply microphone calibration over the WHOLE range.  The table is the
    # microphone's own response, so it is subtracted.
    calibrated = [
        [
            value - gain
            for value, gain in zip(
                resample(m.frequencies, m.response, grid), calibration
            )
        ]
        for m in measurements
    ]
    if not all(math.isfinite(value) for spl in calibrated for value in spl):
        raise ValueError("measurement contains non-finite values")
    if reference_spl is None:
        # An LFE channel is far below the others in the level band.
        pooled = [
            spl for m, spl in zip(measurements, calibrated) if m.channel not in lfe
        ]
        reference_spl = estimate_reference_spl(
            pooled or calibrated,
            grid,
            low_cutoff_hz=low_cutoff_hz,
            high_cutoff_hz=high_cutoff_hz,
            max_boost_db=max_boost_db,
            clip_fraction=clip_fraction,
        )
    curves: dict[str, SpeakerCurves] = {}
    for measurement, spl in zip(measurements, calibrated):
        gd = resample(measurement.frequencies, measurement.group_delay, grid)
        # The same SPL reference is used for all channels, preserving their
        # level balance.
        response = [value - reference_spl for value in spl]
        if not all(math.isfinite(value) for value in response + gd):
            raise ValueError(f"{measurement.channel} curve contains non-finite values")
        first, last = bands[measurement.channel in lfe]
        if filters and measurement.channel in filters:
            gain, gain_gd = filters[measurement.channel]
            low, high = _level_band(low_cutoff_hz, high_cutoff_hz)
            offset = statistics.median(
                r + g for f, r, g in zip(grid, response, gain) if low <= f <= high
            )
            wanted = [offset - g for g in gain]
            wanted_gd = [-v for v in gain_gd]
        else:
            wanted, wanted_gd = response, gd
        correction = [
            min(-value, max_boost_db) if first < i < last else 0.0
            for i, value in enumerate(wanted)
        ]
        correction_gd = [
            -value if first < i < last else 0.0 for i, value in enumerate(wanted_gd)
        ]
        curves[measurement.channel] = SpeakerCurves(
            response, gd, correction, correction_gd
        )
    return curves, reference_spl


def _band(grid: list[float], low_hz: float, high_hz: float) -> tuple[int, int]:
    """First and last grid index of the correction band."""
    band = [i for i, f in enumerate(grid) if low_hz <= f <= high_hz]
    if len(band) < 3:
        raise ValueError("correction band must contain at least three frequency points")
    # The standard grid rarely lands exactly on the cutoffs. Keep the nearest
    # in-band samples flat, too, so interpolation from the outer zero samples
    # cannot extend correction below the low cutoff or above the high cutoff.
    return band[0], band[-1]


def _signed(value: float, correction: bool) -> str:
    """A correction curve holds ``value``, a measurement curve its negation.

    SoundID writes negated zero on correction curves.
    """
    value = value if correction else -value
    if value == 0:
        return "-0" if correction else "0"
    return repr(float(value))


def _channel_params(
    measurement: Measurement,
    layout: sid_layout.Layout,
    correction: bool,
    spot: dict | None = None,
) -> dict[str, Any]:
    """``spot``: {channel: (delay ms, gain dB)}, SoundID's listening spot adjustment."""
    delay_ms, gain_db = (spot or {}).get(measurement.channel, (0.0, 0.0))
    return {
        "ChannelDelayMs": _signed(delay_ms, correction),
        "ChannelGroup": layout.channels[measurement.index].group,
        "ChannelIndex": measurement.index,
        "ChannelName": measurement.channel,
        "Delay": "-0" if correction else "0",
        "Frequency": GRID_NAME,
        "Transfer": _signed(gain_db, correction),
    }


def _correction_type(measurement: Measurement) -> str:
    """SoundID names the correction of channels 0 and 1 only."""
    return {0: "CorrectionLeft", 1: "CorrectionRight"}.get(
        measurement.index, "Correction"
    )


def _side(measurement: Measurement) -> str:
    return ("Left", "Right")[measurement.index]


def _sonarworks_reference_params(
    measurement: Measurement, correction: bool, spot: dict | None = None
) -> dict[str, Any]:
    """Curve parameters of Sonarworks Reference 3 / 4: the listening spot gain (dB) and
    delay (seconds) on the correction curve only."""
    delay_ms, gain_db = (spot or {}).get(measurement.channel, (0.0, 0.0))
    return {
        "Delay": repr(delay_ms / 1000 if correction else 0.0),
        "Frequency": GRID_NAME,
        "Transfer": repr(float(gain_db) if correction else 0.0),
    }


def check_app(
    measurements: list[Measurement], layout: sid_layout.Layout, app: str
) -> None:
    """Sonarworks Reference 3 / 4 projects are stereo with both channels measured."""
    if app not in APPS:
        raise ValueError(f"app must be one of: {', '.join(APPS)}")
    if app == "sonarworks-reference" and (
        layout is not sid_layout.STEREO
        or sorted(m.index for m in measurements) != [0, 1]
    ):
        raise ValueError(
            "a Sonarworks Reference 3 / 4 project needs a stereo layout with Left and Right "
            "measured"
        )


def _channel_side(measurement: Measurement) -> str:
    """The ``Channel`` element: SoundID's enum has Left, Right and Other."""
    return {0: "Left", 1: "Right"}.get(measurement.index, "Other")


def test_signal_config(layout: sid_layout.Layout, sample_rate: int) -> str:
    """Base64 JSON ``TestSignalConfig``: SoundID finds the LFE channels by its
    ``lfeChannelMap`` only.  The other values are those of SoundID's own projects."""
    config = {
        "endFrequency": 22000.0,
        "fadeIn": 1440.0,
        "fadeOut": 240.0,
        "inputFadeIn": 480.0,
        "inputFadeOut": 480.0,
        "interSignalSilence": 31200.0,
        "lfeChannelMap": [c.is_lfe for c in layout.channels],
        "lfeEndFrequency": 22000.0,
        "name": "FFTResponseTestSignal",
        "roomReverb": 1440.5,
        "safeSilence": 720.0,
        "sampleRate": float(sample_rate),
        "startFrequency": 10.0,
        "sweepDuration": 48000.0,
        "version": "v1.0.0",
    }
    return base64.b64encode(json.dumps(config, separators=(",", ":")).encode()).decode()


def check_layout(measurements: list[Measurement], layout: sid_layout.Layout) -> None:
    """Each measurement must be a distinct channel of ``layout``, by index and name."""
    seen = set()
    for m in measurements:
        if not 0 <= m.index < len(layout.channels):
            raise ValueError(
                f"{m.channel}: layout {layout.name} has no channel {m.index}"
            )
        if m.channel != layout.channels[m.index].name:
            raise ValueError(
                f"{m.channel}: channel {m.index} of layout {layout.name} is "
                f"{layout.channels[m.index].name}"
            )
        if m.index in seen:
            raise ValueError(f"{m.channel}: channel {m.index} measured twice")
        seen.add(m.index)


# ---------------------------------------------------------------------------
# project XML
# ---------------------------------------------------------------------------
NS_SW = swproj.NS["s"]
NS_A = swproj.NS["a"]
NS_L = swproj.NS["l"]
NS_P = swproj.NS["p"]
for _prefix, _uri in (
    ("", NS_SW),
    ("i", swproj.NS["i"]),
    ("a", NS_A),
    ("l", NS_L),
    ("p", NS_P),
):
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


def _point_list(
    parent: ET.Element, grid: list[float], response: list[float], gd: list[float]
) -> None:
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
    layout: sid_layout.Layout = sid_layout.STEREO,
    spot: dict | None = None,
    app: str = "soundid",
) -> bytes:
    """``app`` ``sonarworks-reference``: Sonarworks Reference 3 / 4 curve types and parameters; no
    channel parameters, layout or version history."""
    sonarworks = app == "sonarworks-reference"
    root = ET.Element(f"{{{NS_SW}}}Project")
    _db_stamp(root)
    _element(root, "CurveCollections")
    curves = _element(root, "Curves")

    for measurement in measurements:
        speaker = corrected[measurement.channel]
        index = measurement.index
        if sonarworks:
            measurement_type = f"Measurement{_side(measurement)}"
            params = [
                _sonarworks_reference_params(measurement, c, spot)
                for c in (False, True)
            ]
        else:
            measurement_type = "Measurement"
            params = [
                _channel_params(measurement, layout, c, spot) for c in (False, True)
            ]
        _curve(
            curves,
            measurement_type,
            f"Balanced Measurement CH {index}",
            grid,
            speaker.response,
            speaker.group_delay,
            params[0].items(),
        )
        _curve(
            curves,
            _correction_type(measurement),
            f"Correction CH {index}",
            grid,
            speaker.correction,
            speaker.correction_group_delay,
            params[1].items(),
        )

    mic_freq = [p[0] for p in profile.points]
    mic_response = [p[1] for p in profile.points]
    _curve(
        curves,
        "Correction",
        f"{profile.name} {profile.angle}",
        mic_freq,
        mic_response,
        [0.0] * len(mic_freq),
        (("MicDegrees", profile.angle),),
    )

    frequency_collections = _element(root, "FrequencyCollections")
    frequency_collection = ET.SubElement(
        frequency_collections, f"{{{NS_P}}}FrequencyCollection"
    )
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
            *((("ChannelLayout", layout.id),) if not sonarworks else ()),
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
            # Written only with an LFE, so stereo projects stay as they were.
            *(
                (
                    (
                        "TestSignalConfig",
                        test_signal_config(layout, measurements[0].sample_rate),
                    ),
                )
                if any(c.is_lfe for c in layout.channels) and not sonarworks
                else ()
            ),
        ),
    )
    room_points = _element(rm, "Points")
    for measurement in measurements:
        point = _element(room_points, "RoomPoint")
        _db_stamp(point)
        _element(point, "Channel", _channel_side(measurement))
        _element(point, "Distance", "0")
        _element(point, "DistanceInSamples", "0")
        _element(point, "InputLevel", "0")
        _element(point, "OutputLevel", "0")
        _params(point, () if sonarworks else (("ChannelIndex", measurement.index),))

    for measurement in measurements:
        speaker = corrected[measurement.channel]
        m = _element(rms, "Measurement")
        _element(m, "Channel", _channel_side(measurement))
        _element(m, "Delay", "0")
        _params(m, () if sonarworks else (("ChannelIndex", measurement.index),))
        _point_list(m, grid, speaker.response, speaker.group_delay)
        _element(m, "RawData")  # optional audio samples intentionally omitted
        _element(m, "Time", measurement.timestamp)
        _element(m, "Transfer", "0")
    _element(
        rm,
        "Time",
        max((m.timestamp for m in measurements if m.timestamp), default=EPOCH),
    )

    values = _element(root, "Values")
    if not sonarworks:
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
    measurements: list[Measurement],
    grid: list[float],
    corrected: dict[str, SpeakerCurves],
    layout: sid_layout.Layout = sid_layout.STEREO,
    spot: dict | None = None,
) -> bytes:
    # PEQb v3.0.0.2 stores each speaker's Measurement together with its
    # correction curve. Use the same constrained correction
    # as the XML; do not invert the measurement again here, bypassing the limits.
    # Both curves must be present for the app to switch presets successfully.
    curves = []
    for measurement in measurements:
        speaker = corrected[measurement.channel]
        curves.append(
            peqb.Curve(
                peqb.CURVE_TYPE_ID["Measurement"],
                list(zip(grid, speaker.response, speaker.group_delay)),
                _channel_params(measurement, layout, False, spot),
                flags=peqb.F_FREQUENCY | peqb.F_RESPONSE | peqb.F_PARAMETERS,
            )
        )
        curves.append(
            peqb.Curve(
                peqb.CURVE_TYPE_ID[_correction_type(measurement)],
                list(zip(grid, speaker.correction, speaker.correction_group_delay)),
                _channel_params(measurement, layout, True, spot),
                flags=peqb.F_FREQUENCY
                | peqb.F_RESPONSE
                | peqb.F_GROUP_DELAY
                | peqb.F_PARAMETERS,
            )
        )
    return peqb.write(curves)


def build_sonarworks_reference_eqb(
    measurements: list[Measurement],
    grid: list[float],
    corrected: dict[str, SpeakerCurves],
    spot: dict | None = None,
) -> bytes:
    """PEQb 3.0.0.0, as Sonarworks Reference 4 Measure writes it: CorrectionLeft,
    CorrectionRight, MeasurementLeft, MeasurementRight.  A correction's
    ``transfer`` is its gain less the larger gain of the two; ``delay_ms`` is
    absolute."""
    by_side = {_side(m): m for m in measurements}
    spot = spot or {}
    gains = {side: spot.get(m.channel, (0.0, 0.0))[1] for side, m in by_side.items()}
    top = max(gains.values())
    curves = []
    for kind in ("Correction", "Measurement"):
        for side in ("Left", "Right"):
            m = by_side[side]
            speaker = corrected[m.channel]
            if kind == "Correction":
                points = zip(grid, speaker.correction, speaker.correction_group_delay)
                transfer, delay = gains[side] - top, spot.get(m.channel, (0.0, 0.0))[0]
            else:
                points = zip(grid, speaker.response, speaker.group_delay)
                transfer, delay = 0.0, 0.0
            curves.append(
                peqb.Curve(
                    peqb.CURVE_TYPE_ID[kind + side],
                    list(points),
                    transfer=transfer,
                    delay_ms=delay,
                )
            )
    return peqb.write_v1(curves, version=(3, 0, 0, 0))


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------
@dataclass
class Conversion:
    data: bytes  # complete .swproj file
    grid: list[float]
    curves: dict[str, SpeakerCurves]
    reference_spl: float  # calibrated SPL mapped to 0 dB


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
    lfe_high_cutoff_hz: float = DEFAULT_LFE_HIGH_CUTOFF_HZ,
    layout: sid_layout.Layout = sid_layout.STEREO,
    spot: dict | None = None,
    app: str = "soundid",
    filters: dict[str, tuple[list[float], list[float]]] | None = None,
) -> Conversion:
    """``measurements`` are channels of ``layout``: ``index`` and ``channel`` as in the layout.

    ``filters``: correction filters on the standard grid, see ``prepare_speaker_curves``.
    ``spot``: {channel: (delay ms, gain dB)}, the listening spot adjustment.
    ``app``: ``soundid`` (SoundID Reference) or ``sonarworks-reference`` (Sonarworks Reference 3 / 4).
    """
    check_layout(measurements, layout)
    check_app(measurements, layout, app)
    unknown = set(spot or {}) - {m.channel for m in measurements}
    if unknown:
        raise ValueError(
            f"listening spot of unmeasured channel(s): {', '.join(sorted(unknown))}"
        )
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
        lfe=[c.name for c in layout.channels if c.is_lfe],
        lfe_high_cutoff_hz=lfe_high_cutoff_hz,
        filters=filters,
    )
    xml = build_project_xml(
        measurements, grid, profile, corrected, name, layout, spot, app
    )
    if app == "sonarworks-reference":
        data = swproj.write(
            xml,
            build_sonarworks_reference_eqb(measurements, grid, corrected, spot),
            version=swproj.SONARWORKS_REFERENCE_VERSION,
        )
    else:
        data = swproj.write(xml, build_eqb(measurements, grid, corrected, layout, spot))
    return Conversion(data, grid, corrected, reference_spl)
