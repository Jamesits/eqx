"""Correctly rounded elementary functions on floats (MPFR via gmpy2).

``math``, ``cmath`` and ``float ** float`` use the C library, whose results
differ in the last bit between Windows, Linux and macOS.  Written files must
be the same bytes everywhere, so every transcendental function goes through
here.  ``+ - * /`` and ``math.sqrt`` are correctly rounded already.
"""

from __future__ import annotations

import gmpy2

# IEEE 754 binary64: 53-bit precision, subnormals, round to nearest.
_C = gmpy2.ieee(64)


def exp(x: float) -> float:
    return float(_C.exp(x))


def log(x: float) -> float:
    return float(_C.log(x))


def log10(x: float) -> float:
    return float(_C.log10(x))


def log2(x: float) -> float:
    return float(_C.log2(x))


def pow(x: float, y: float) -> float:
    return float(_C.pow(x, y))


def sin(x: float) -> float:
    return float(_C.sin(x))


def cos(x: float) -> float:
    return float(_C.cos(x))


def atan2(y: float, x: float) -> float:
    return float(_C.atan2(y, x))


def cexp(z: complex) -> complex:
    """e ** z."""
    m = _C.exp(z.real)
    return complex(float(m * _C.cos(z.imag)), float(m * _C.sin(z.imag)))


def cabs(z: complex) -> float:
    return float(_C.hypot(z.real, z.imag))


def phase(z: complex) -> float:
    return atan2(z.imag, z.real)
