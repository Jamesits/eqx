"""Base class of a converter from one file format to another."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ..options import Option


@dataclass
class Result:
    data: bytes                             # complete output file
    name: str                               # default output file name
    notes: list[str] = field(default_factory=list)


class Converter:
    """Converts one ``source`` format file to one ``target`` format file.

    Subclasses declare their settings in ``options``; each option's ``dest``
    is a keyword argument of ``__init__``.
    """

    source: ClassVar[str]                   # format id, see ``eqx.formats``
    target: ClassVar[str]
    description: ClassVar[str]
    options: ClassVar[tuple[Option, ...]] = ()

    def convert(self, path: Path) -> Result:
        raise NotImplementedError
