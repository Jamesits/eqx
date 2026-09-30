"""What every device correction reader returns, and its inspection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from .fileformat import Inspector, file_section
from .model import Correction
from .report import Section, Table


@dataclass
class Export:
    corrections: list[Correction]
    fields: list[tuple[str, Any]] = field(default_factory=list)     # file metadata
    encryption: str = ""                    # how the file was decrypted; "" if plain
    text: str = ""                          # the plaintext, if it is text

    def select(self, channel: str, sample_rate: float | None = None) -> Correction:
        """The correction of ``channel`` ("Left", "Right") at ``sample_rate``.

        Without ``sample_rate``: 48 kHz if present, else the lowest rate.
        """
        matches = [c for c in self.corrections if channel_name(c.channel) == channel]
        if not matches:
            names = sorted({c.channel for c in self.corrections})
            raise ValueError(f"no {channel} channel; available: {', '.join(names) or 'none'}")
        rates = sorted({c.sample_rate for c in matches if c.sample_rate})
        if sample_rate is None:
            sample_rate = 48000.0 if 48000.0 in rates else (rates[0] if rates else None)
        elif float(sample_rate) not in rates:
            raise ValueError(f"no {sample_rate:g} Hz filters; available: "
                             f"{', '.join(f'{r:g}' for r in rates) or 'none'}")
        return next(c for c in matches if c.sample_rate == sample_rate or not rates)


def channel_name(name: str) -> str:
    """Exports name the stereo channels "L"/"R" or "Left"/"Right"."""
    return {"l": "Left", "left": "Left", "r": "Right", "right": "Right"}.get(name.lower(), name)


def number(text: str, where: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {text!r} is not a number") from None


def correction_sections(corrections: list[Correction]) -> list[Section]:
    sections = []
    for c in corrections:
        title = c.channel + (f" {c.sample_rate:g} Hz" if c.sample_rate else "")
        fields: list[tuple[str, Any]] = [("gain dB", c.gain_db), ("delay ms", c.delay_ms)]
        if c.biquads:
            fields.append(("biquads", len(c.biquads)))
            table = Table(["b0", "b1", "b2", "a0", "a1", "a2"],
                          [(b.b0, b.b1, b.b2, b.a0, b.a1, b.a2) for b in c.biquads])
        elif c.peqs:
            fields.append(("filters", len(c.peqs)))
            table = Table(["type", "frequency Hz", "gain dB", "q"],
                          [(p.kind, p.frequency, p.gain_db, p.q) for p in c.peqs])
        else:
            fields.append(("points", len(c.points)))
            table = Table(["frequency Hz", "gain dB"], c.points)
        fields.append(("gain at 1 kHz dB", c.response([1000.0])[0]))
        sections.append(Section(f"channel {title}", fields, table))
    return sections


class ExportInspector(Inspector):
    """Inspector of a format module whose ``load(path, **options)`` returns an ``Export``."""

    load: ClassVar[Any]

    def __init__(self, **options):
        self.load_options = options

    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        export = type(self).load(path, **self.load_options)
        extra = [("encryption", export.encryption)] if export.encryption else []
        head = file_section(path, data, *extra)
        head.raw = export.text or None
        meta = [Section("export", export.fields)] if export.fields else []
        return [head, *meta, *correction_sections(export.corrections)]
