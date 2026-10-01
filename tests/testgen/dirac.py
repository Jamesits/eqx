"""Dirac Live target curve and Dirac Live Processor filter slots."""

from __future__ import annotations

import struct

from eqx.dirac import filterslot, targetcurve

TARGETCURVE_DIR = "dirac/targetcurve"
DIRAC_FILTER_DIR = "dirac/dirac-filter"


def write_targetcurve() -> bytes:
    """A house curve: bass shelf up, treble tilted down."""
    return targetcurve.write(
        targetcurve.TargetCurve(
            "Tilt",
            "",
            [(20.0, 6.0), (100.0, 4.0), (300.0, 0.0), (1000.0, 0.0), (20000.0, -4.5)],
            20.0,
            20000.0,
        )
    ).encode()


# A parallel IIR section: (b0 + b1 z^-1) / (1 + a1 z^-1 + a2 z^-2).
FIIR_SECTION = {"a1": -1.5, "a2": 0.625, "b0": 0.25, "b1": -0.125}


def fiir_slot() -> dict:
    """A stereo FIIR slot at 48 kHz: a short FIR and one IIR section per
    channel, a cross-term cell, and gain and delay on the right output."""
    cells = [
        {
            "input_idx": i,
            "output_idx": i,
            "fiir": {
                "fir": {"fir_taps": [1.0, 0.5 * (i + 1), 0.25]},
                "iir": {"biquads": [FIIR_SECTION]},
            },
        }
        for i in range(2)
    ]
    cells.append(
        {"input_idx": 0, "output_idx": 1, "fiir": {"fir": {"fir_taps": [0.5]}}}
    )
    return {
        "info": {"name": "FIIR", "num_inputs": 2, "num_outputs": 2, "num_sections": 1},
        "sections": {
            0: {
                "disposition": filterslot.LIVE,
                "filter_type": filterslot.FILTER_TYPES.index("FIIR_FILTER"),
                "rates": {48000: {"cells": cells}},
                "num_inputs": 2,
                "num_outputs": 2,
            }
        },
        "gain_delay": [{}, {"input_idx": 1, "gain_db": -6.0, "delay_ms": 0.5}],
    }


def write_signed_slot() -> bytes:
    """The FIIR slot in a signed container with a zero signature: the format
    of a cloud-calculated filter, rejected by the processor."""
    payload = filterslot.protobuf.encode(filterslot.SCHEMA, "FilterSlot", fiir_slot())
    return (
        filterslot.MAGIC
        + filterslot.PRODUCT
        + struct.pack("<II", 2, 1)
        + filterslot.SIGNED_MAGIC
        + bytes([1])
        + struct.pack(">H", filterslot.SIGNATURE_SIZE)
        + bytes(filterslot.SIGNATURE_SIZE)
        + struct.pack(">I", 1)
        + payload
    )


def files() -> dict[str, bytes]:
    return {
        f"{TARGETCURVE_DIR}/Tilt.targetcurve": write_targetcurve(),
        f"{DIRAC_FILTER_DIR}/FIIR.bin": filterslot.write(fiir_slot()),
        f"{DIRAC_FILTER_DIR}/FIIR signed.bin": write_signed_slot(),
    }
