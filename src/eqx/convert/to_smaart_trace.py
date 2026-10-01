"""AutoEq CSV -> Smaart transfer function trace (``.trf``), spectrum trace (``.srf``)."""

from __future__ import annotations

from pathlib import Path

from ..autoeq import response
from ..options import Option
from ..rationalacoustics import trace
from .base import Converter, Result
from .common import COLUMN_OPTION, DEFAULT_RATE, RATE_OPTION

FFT = 32768
FFT_OPTION = Option("--fft", type=int, help=f"FFT size, a power of two (default: {FFT})")


class AutoeqToSmaartTrace(Converter):
    """The curve on the FFT bins of a trace; the trace name is the input stem."""

    target_kind: int                        # trace.TRANSFER_FUNCTION or trace.SPECTRUM

    def __init__(self, column: str = response.RAW, rate: float = DEFAULT_RATE, fft: int = FFT):
        if rate <= 0 or rate != int(rate):
            raise ValueError("--rate must be a whole number of Hz")
        if fft < 256 or fft & (fft - 1):
            raise ValueError("--fft must be a power of two, at least 256")
        self.column = column
        self.rate = int(rate)
        self.fft = fft

    def data(self, points) -> tuple[trace.DataSet, dict]:
        """The data set and other trace fields."""
        raise NotImplementedError

    def _convert(self, path: Path) -> Result:
        data, fields = self.data(response.load(path).curve(self.column))
        t = trace.Trace(self.target_kind, self.rate, self.fft, data, name=path.stem,
                        comment=f"{path.name}, column {self.column}", **fields)
        return Result(trace.write(t), f"{path.stem}{trace.EXTENSIONS[self.target_kind]}",
                      [f"{len(data.frequencies)} bins, FFT {self.fft} at {self.rate} Hz"
                       + self.note(t)])

    def note(self, t: trace.Trace) -> str:
        return ""


class AutoeqToSmaartTrf(AutoeqToSmaartTrace):
    """The minimum-phase transfer function of the curve; coherence 1.  dB
    values are written as they are."""

    source = "autoeq"
    target = "smaart-trf"
    target_kind = trace.TRANSFER_FUNCTION
    description = "a curve as a Smaart transfer function trace"
    options = (COLUMN_OPTION, RATE_OPTION, FFT_OPTION)

    def data(self, points):
        return trace.transfer_function(points, self.rate, self.fft), {}

    def note(self, t):
        return ", minimum phase"


class AutoeqToSmaartSrf(AutoeqToSmaartTrace):
    """The power of the curve less the calibration offset; Smaart's Plot
    Calibrated Levels shows the curve's own dB values."""

    source = "autoeq"
    target = "smaart-srf"
    target_kind = trace.SPECTRUM
    description = "a curve as a Smaart spectrum trace"
    options = (COLUMN_OPTION, RATE_OPTION, FFT_OPTION,
               Option("--calibration-db", type=float,
                      help="calibration offset of the trace, dB (default: 0)"))

    def __init__(self, column: str = response.RAW, rate: float = DEFAULT_RATE, fft: int = FFT,
                 calibration_db: float = 0.0):
        super().__init__(column, rate, fft)
        self.calibration_db = calibration_db

    def data(self, points):
        return (trace.spectrum(points, self.rate, self.fft, self.calibration_db),
                {"calibration_db": self.calibration_db})

    def note(self, t):
        return f", calibration offset {self.calibration_db:g} dB"
