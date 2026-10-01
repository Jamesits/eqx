"""Audyssey MultEQ-X project."""

from __future__ import annotations

import json

from eqx import dsp
from eqx.audyssey import mqx

from .common import bells, impulse_response, subwoofer

MQX_DIR = "audyssey/mqx"
# Level offset dB of each position.
MQX_POSITION_GAINS = (0.0, -1.0, 2.0)
# Time of flight, samples, per channel.
MQX_FLIGHT = {"FL": 420, "FR": 430, "C": 410, "SLA": 300, "SRA": 310, "SW1": 450}
# (channel, position) excluded from the aggregate.
MQX_DISABLED = ("C", 2)


def mqx_filters(designation: str) -> list:
    """FL, FR: the device export bells; SW1: a low pass; others: one bell."""
    if designation == "SW1":
        return subwoofer(mqx.SAMPLE_RATE)
    if designation in ("FL", "FR"):
        return bells("Left" if designation == "FL" else "Right", mqx.SAMPLE_RATE)
    return [
        dsp.bell(
            200.0 * (list(MQX_FLIGHT).index(designation) + 1), 4.0, 1.0, mqx.SAMPLE_RATE
        )
    ]


def mqx_ir(designation: str, gain_db: float) -> list[float]:
    return impulse_response(
        mqx.IR_LENGTH,
        mqx.SYSTEM_DELAY + MQX_FLIGHT[designation],
        gain_db,
        mqx_filters(designation),
    )


MQX_TARGETS = (
    *mqx.DEFAULT_TARGETS,
    mqx.TargetItem(
        "biquad",
        "Bass",
        {
            "MinGain": -25.0,
            "MaxGain": 25.0,
            "MinQ": 0.1,
            "MaxQ": 20.0,
            "MinFreq": 10.0,
            "MaxFreq": 22000.0,
            "Frequency": 60.0,
            "Gain": 3.0,
            "Q": 0.7,
            "Type": 3,
        },
        flat=False,
        all=False,
        channels=[mqx.channel_guid("FL"), mqx.channel_guid("FR")],
    ),
    mqx.TargetItem(
        "tilt",
        "Tilt",
        {"DecibelsPerOctave": -0.5, "PivotFrequency": 1000.0, "Frequency": 1000.0},
        excluded=[mqx.channel_guid("SW1")],
    ),
    mqx.TargetItem(
        "custom",
        "Treble",
        {
            "Points": [
                {"X": 0.0, "Y": 1.0},
                {"X": 1000.0, "Y": 1.0},
                {"X": 10000.0, "Y": 0.5},
                {"X": 24000.0, "Y": 0.5},
            ],
            "DisplayName": "Treble",
        },
        reference=False,
        all=False,
        channels=[mqx.channel_guid("C")],
    ),
)


def write_mqx() -> bytes:
    """5.1, every position; one measurement excluded from the aggregate."""
    channels = [(d, [mqx_ir(d, g) for g in MQX_POSITION_GAINS]) for d in MQX_FLIGHT]
    data = json.loads(mqx.write(channels, MQX_TARGETS))
    designation, position = MQX_DISABLED
    for m in data["_measurements"]:
        if m["ChannelGuid"] == mqx.channel_guid(designation) and m[
            "PositionGuid"
        ] == mqx.guid("position", str(position)):
            m["Enabled"] = False
    return mqx.dumps(data)


def files() -> dict[str, bytes]:
    return {f"{MQX_DIR}/Mqx 5.1.mqx": write_mqx()}
