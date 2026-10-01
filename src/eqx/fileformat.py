"""What each format module declares: its ``Format`` and its ``Inspector``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .options import Option
from .report import Section, Table


class Inspector:
    """Describes one file as a list of sections.

    Subclasses declare their settings in ``options``; each option's ``dest``
    is a keyword argument of ``__init__``.
    """

    options: ClassVar[tuple[Option, ...]] = ()

    def inspect(self, path: Path) -> list[Section]:
        raise NotImplementedError


@dataclass(frozen=True)
class Format:
    id: str
    extensions: tuple[str, ...]  # lower case
    description: str
    inspector: type[Inspector]
    # Content check; picks the format when several share an extension.
    sniff: Callable[[bytes], bool] | None = None


def file_section(path: Path, data: bytes, *fields) -> Section:
    return Section(
        "file", [("name", Path(path).name), ("size", f"{len(data):,} bytes"), *fields]
    )


def head_text(data: bytes) -> str:
    """The start of ``data`` as text, for a content check."""
    return data[:4096].decode("utf-8", errors="replace")


def fixed(value: float, digits: int = 2) -> str:
    """``value`` with ``digits`` decimals, without a negative zero."""
    text = f"{value:.{digits}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def frequency_range(points) -> str:
    return f"{points[0][0]:g}-{points[-1][0]:g} Hz" if points else "-"


RESPONSE_COLUMNS = ["frequency Hz", "dB", "group delay s"]


def response_section(title: str, fields: list, compute: Callable[[], tuple]) -> Section:
    """``fields``, the range and the table of ``compute()``: (frequencies, dB[, group
    delay s]).  A ValueError of ``compute`` becomes the ``response`` field."""
    try:
        columns = compute()
    except ValueError as exc:
        return Section(title, [*fields, ("response", str(exc))])
    frequencies, db = columns[:2]
    return Section(
        title,
        [*fields, ("range", frequency_range(list(zip(frequencies, db))))],
        Table(RESPONSE_COLUMNS[: len(columns)], list(zip(*columns))),
    )
