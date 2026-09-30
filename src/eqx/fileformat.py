"""What each format module declares: its ``Format`` and its ``Inspector``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar

from .options import Option
from .report import Section


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
    extensions: tuple[str, ...]             # lower case
    description: str
    inspector: type[Inspector]
    # Content check; picks the format when several share an extension.
    sniff: Callable[[bytes], bool] | None = None


def file_section(path: Path, data: bytes, *fields) -> Section:
    return Section("file", [("name", Path(path).name), ("size", f"{len(data):,} bytes"), *fields])


def frequency_range(points) -> str:
    return f"{points[0][0]:g}-{points[-1][0]:g} Hz" if points else "-"
