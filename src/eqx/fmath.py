"""Correctly rounded math on floats and complex numbers (MPFR/MPC via gmpy2).

``math``, ``cmath`` and ``float ** float`` use the C library, whose results
differ in the last bit between Windows, Linux and macOS.  CPython's complex
``*`` and ``/`` may be compiled to fused multiply-adds (Python 3.10 on arm64
macOS), which round differently.  Written files must be the same bytes
everywhere, so every transcendental function and every complex product and
quotient goes through here.  Float ``+ - * /`` and ``math.sqrt`` are
correctly rounded already.
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
    return complex(float(_C.mul(m, _C.cos(z.imag))), float(_C.mul(m, _C.sin(z.imag))))


def cabs(z: complex) -> float:
    return float(_C.hypot(z.real, z.imag))


def phase(z: complex) -> float:
    return atan2(z.imag, z.real)


def cmul(a: complex, b: complex) -> complex:
    """a * b."""
    return complex(_C.mul(a, b))


def cdiv(a: complex, b: complex) -> complex:
    """a / b."""
    return complex(_C.div(a, b))
