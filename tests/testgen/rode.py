"""FuzzMeasure 4, 3 and 2 documents."""

from __future__ import annotations

import struct

from eqx import dsp, keyedarchive
from eqx.keyedarchive import Instance, Ref
from eqx.rode import fuzzmeasure

from .common import bells, impulse_response, subwoofer

FUZZMEASURE_DIR = "rode/fuzzmeasure"
# Time of flight, samples, and level, per measurement.
FM_FLIGHT = {"Left": 420, "Right": 430, "Sub": 450}
FM_GAIN_DB = {"Left": 0.0, "Right": -1.0, "Sub": -6.0}
# The microphone of the calibrated measurements: a treble rise.
FM_MIC = ("TILT01", 12000.0, 3.0, 0.7)
FM2_IR_LENGTH = 8192


def fm_mic(rate: float) -> list:
    _, f0, gain, q = FM_MIC
    return [dsp.bell(f0, gain, q, rate)]


def fm_filters(name: str, rate: float) -> list:
    """Left, Right: the device export bells; Sub: a low pass."""
    if name == "Sub":
        return subwoofer(rate)
    return bells(name, rate)


def fm_ir(
    name: str, rate: float, length: int = fuzzmeasure.IR_LENGTH, mic: bool = False
) -> list[float]:
    return impulse_response(
        length,
        FM_FLIGHT[name],
        FM_GAIN_DB[name],
        fm_filters(name, rate) + (fm_mic(rate) if mic else []),
    )


def fm_calibration(rate: float) -> fuzzmeasure.Calibration:
    """The microphone's response, 1/6 octave from 10 Hz to 21 kHz."""
    serial = FM_MIC[0]
    grid = [10 * 2 ** (k / 6) for k in range(67)]
    return fuzzmeasure.Calibration(
        f"{serial} mic",
        [(f, dsp.cascade_db(fm_mic(rate), f, rate)) for f in grid],
        [(f, 0.0) for f in grid],
        serial,
        -40.0,
        fuzzmeasure.record_uuid(f"calibration/{serial}"),
    )


def write_fume4() -> dict[str, bytes]:
    """Left: a negative window begin; Right: through the microphone, calibrated, with an
    SPL reference; Sub: 44.1 kHz, normalized, a Bingham window."""

    def record(name, rate, **settings):
        mic = settings.get("use_calibration", False)
        return fuzzmeasure.Record(
            name,
            rate,
            fm_ir(name, rate, mic=mic),
            fuzzmeasure.record_uuid(f"fume4/{name}"),
            start_hz=20.0,
            end_hz=20000.0,
            color=fuzzmeasure.COLORS[list(FM_FLIGHT).index(name)],
            **settings,
        )

    return fuzzmeasure.write(
        [
            record("Left", 48000, window=(-512, 16384, 0), notes="Left speaker, 3 m"),
            record(
                "Right",
                48000,
                window=(0, 16384, 0),
                calibration=fm_calibration(48000),
                use_calibration=True,
                use_spl=True,
                spl_reference=-20.0,
            ),
            record("Sub", 44100, window=(-4096, 12288, 5), normalized=True),
        ]
    )


def _fm3_vector(values: list[float]) -> Instance:
    """SMUGRealVector of FuzzMeasure 3: big-endian data, no byte order."""
    return Instance(
        "SMUGRealVector", {"VectorData": Ref(struct.pack(f">{len(values)}f", *values))}
    )


def _fm3_spline(points) -> Instance:
    return Instance(
        "SMUGSpline",
        {
            "SMUGSplineVersion": "1.0",
            "X": _fm3_vector([f for f, _ in points]),
            "Y": _fm3_vector([v for _, v in points]),
        },
    )


def _fm3_record(
    name: str,
    rate: int,
    key: str,
    window: tuple,
    inline=None,
    calibration: fuzzmeasure.Calibration | None = None,
) -> Instance:
    """A record as FuzzMeasure 3 encodes it; ``inline``: the impulse response object."""
    fields = {
        "ImpulseResponseWindow": Instance(
            "SMUGWindow",
            {"Version": "2.0", "Begin": window[0], "End": window[1], "Type": window[2]},
        )
    }
    if inline is not None:
        fields["ImpulseResponse"] = inline
    fields.update(
        {
            "ImpulseUUID": key,
            "Date": fuzzmeasure.EPOCH,
            "Comment": name,
            "Notes": "",
            "Thumbnail": None,
            "SampleRate": rate,
            "PlotColor": Instance("NSColor", {"NSColorSpace": 3, "NSWhite": b"0\0"}),
            "LogSweepSettings": Instance(
                "SMUGLogSweepSettings", dict(fuzzmeasure.SWEEP)
            ),
            "SynchronousAverages": 4,
            "CorrectionEnabled": int(calibration is not None),
            "CorrectionRecord": None
            if calibration is None
            else Instance(
                "SMUGCorrectionRecord",
                {
                    "SMUGCorrectionRecord": "1.0",
                    "Name": calibration.name,
                    "MagnitudeSpline": _fm3_spline(calibration.points),
                    "PhaseSpline": _fm3_spline(calibration.phase),
                },
            ),
            "GraphAdjustments": {},
            "PlugInDictionary": {},
            "CompatibilityMode": False,
            "Normalized": False,
            "SPLReferenceLevel": 1.0,
            "startFrequency": Ref(20.0),
            "endFrequency": Ref(20000.0),
        }
    )
    return Instance("SMUGMeasurementRecord", fields)


def write_fume3() -> dict[str, bytes]:
    """44.1 kHz; Left through the microphone, calibrated; impulse responses in archives."""
    files, records = {}, []
    for name in ("Left", "Right"):
        key = fuzzmeasure.record_uuid(f"fume3/{name}")
        mic = name == "Left"
        files[key] = keyedarchive.archive(
            {fuzzmeasure.UNKEYED: _fm3_vector(fm_ir(name, 44100, mic=mic))}
        )
        records.append(
            _fm3_record(
                name,
                44100,
                key,
                (0, 16384, 0),
                calibration=fm_calibration(44100) if mic else None,
            )
        )
    top = {
        "FUMEVersion": "3.0",
        "MeasurementRecords": records,
        "FrequencyDisplayOptions": {
            "displayType": Ref(0),
            "smoothingFraction": Ref(0.0),
        },
        "ImpulseDisplayOptions": {"displayType": Ref(0)},
        "inspectorVisible": Ref(True),
        "ColorIndex": 2,
    }
    return {fuzzmeasure.TOP_LEVEL: keyedarchive.archive(top), **files}


def write_fume2() -> bytes:
    """Left: an inline SMUGRealVector, a half Hann window; Right: FuzzMeasure 1 data."""

    def ir(name):
        return fm_ir(name, 48000, FM2_IR_LENGTH)

    records = [
        _fm3_record(
            "Left",
            48000,
            fuzzmeasure.record_uuid("fume2/Left"),
            (0, 4096, 4),
            inline=_fm3_vector(ir("Left")),
        ),
        _fm3_record(
            "Right",
            48000,
            fuzzmeasure.record_uuid("fume2/Right"),
            (0, 4096, 0),
            inline=Ref(struct.pack(f">{FM2_IR_LENGTH}f", *ir("Right"))),
        ),
    ]
    return keyedarchive.archive(
        {"FUMEVersion": "2.0", "MeasurementRecords": records, "ColorIndex": 2}
    )


def files() -> dict[str, bytes]:
    out = {f"{FUZZMEASURE_DIR}/Fm2.fume": write_fume2()}
    for name, package in (("Fm4.fume4", write_fume4()), ("Fm3.fume3", write_fume3())):
        out.update({f"{FUZZMEASURE_DIR}/{name}/{k}": v for k, v in package.items()})
    return out
