"""Running DRC: the input impulse response, the configuration's sample rate,
the program call.

DRC 3.2.3 is a console program: ``drc [--Name=value ...] config.drc``.
Command-line values override the configuration file.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import dsp
from . import pcm

DEFAULT_EXECUTABLE = "drc"
LOG_TAIL_LINES = 8


def input_taps(rate: float) -> int:
    """The power of two of at least 1 s: DRC wants an impulse response of 1 s
    or more."""
    return 1 << (int(rate) - 1).bit_length()


def input_ir(points, rate: float, level_db: float = 0.0) -> list[float]:
    """The minimum-phase impulse response of (frequency, dB) ``points`` less
    ``level_db``, ``input_taps`` long; flat beyond the ends."""
    points = [(f, v) for f, v in points if 0 < f < rate / 2]
    if len(points) < 2:
        raise ValueError(f"fewer than two response points below {rate / 2:g} Hz")
    return dsp.design_fir_points(
        points, rate, "minimum", input_taps(rate), level_db=level_db
    )


def config_value(text: str, name: str) -> str | None:
    """The last ``name = value`` of a configuration; ``#`` starts a comment."""
    value = None
    for line in text.splitlines():
        m = re.match(rf"\s*{name}\s*=\s*([^#]*)", line)
        if m:
            value = m.group(1).strip()
    return value


def config_rate(path) -> int:
    """``BCSampleRate`` of a configuration file."""
    path = Path(path)
    text = config_value(path.read_text(encoding="latin-1"), "BCSampleRate")
    try:
        rate = int(text or "")
    except ValueError:
        rate = 0
    if rate <= 0:
        raise ValueError(f"{path.name}: no valid BCSampleRate")
    return rate


def run(
    ir: list[float], config, executable: str = DEFAULT_EXECUTABLE
) -> tuple[list[float], str]:
    """(correction filter, DRC's output) of ``ir`` at the configuration's rate.

    DRC runs in the configuration's directory, so its relative file names
    (target, microphone) resolve as in a manual run.  The input and the
    filter are temporary files; the test convolution and the minimum-phase
    filter are not written.
    """
    config = Path(config).resolve()
    if not config.is_file():
        raise ValueError(f"DRC configuration {config} not found")
    # Resolved here: DRC runs in another directory.
    program = shutil.which(executable)
    if program is None:
        raise ValueError(f"DRC program {executable!r} not found")
    with tempfile.TemporaryDirectory(prefix="eqx-drc-") as tmp:
        source, target = Path(tmp) / "input.pcm", Path(tmp) / "filter.pcm"
        source.write_bytes(pcm.write(ir))
        args = [
            program,
            # A base directory would be prepended to the absolute paths below.
            "--BCBaseDir=",
            f"--BCInFile={source}",
            "--BCInFileType=F",
            # A fixed center from the configuration belongs to another input.
            "--BCImpulseCenterMode=A",
            f"--PSOutFile={target}",
            "--PSOutFileType=F",
            "--TCOutFile=",
            "--MSOutFile=",
            str(config),
        ]
        try:
            done = subprocess.run(
                args,
                cwd=config.parent,
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
            )
        except OSError as exc:
            raise ValueError(f"cannot run DRC ({program}): {exc}") from exc
        log = done.stdout + done.stderr
        if done.returncode != 0 or not target.is_file():
            tail = "\n".join(log.strip().splitlines()[-LOG_TAIL_LINES:])
            raise ValueError(f"DRC failed (exit status {done.returncode}):\n{tail}")
        return pcm.load(target), log
