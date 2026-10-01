"""Assertions and values shared by the tests."""

from __future__ import annotations

import math
import unittest

from eqx import dsp
from eqx.autoeq import response
from eqx.convert import Result
from testgen import common

# Frequencies at which the device export bells are compared.
FREQUENCIES = [20, 60, 250, 1000, 4000, 8000, 16000]


def analytic(side: str, rate: float = dsp.PEQ_SAMPLE_RATE) -> list[float]:
    """The gain of a side's device export bells at ``FREQUENCIES``, dB."""
    return [dsp.cascade_db(common.bells(side, rate), f, rate) for f in FREQUENCIES]


def csv_points(result: Result, column: str = response.RAW) -> list:
    """The curve of an AutoEq CSV conversion result."""
    return response.read(result.data.decode()).curve(column)


def assert_filters(test: unittest.TestCase, measured, filters, rate: float, gains_db,
                   low: float, high: float, delta: float, label: str) -> None:
    """``measured`` (frequencies, dB, ...) is the gain of the biquads plus the power
    mean of ``gains_db`` within ``low``-``high`` Hz."""
    mean = 10 * math.log10(sum(10 ** (g / 10) for g in gains_db) / len(gains_db))
    for f, db in zip(*measured[:2]):
        if low <= f <= high:
            test.assertAlmostEqual(db, dsp.cascade_db(filters, f, rate) + mean,
                                   delta=delta, msg=f"{label} {f:g} Hz")
