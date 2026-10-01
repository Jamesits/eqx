"""Base class of a converter from one file format to another."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Sequence

from ..options import Option


@dataclass
class Result:
    data: bytes                             # complete output file
    name: str                               # default output file name
    notes: list[str] = field(default_factory=list)


class Converter:
    """Converts ``source`` format files to one ``target`` format file.

    Subclasses declare their settings in ``options``; each option's ``dest``
    is a keyword argument of ``__init__``.  They implement ``_convert``,
    which takes the input files as positional arguments.
    """

    source: ClassVar[str]                   # format id, see ``eqx.formats``
    target: ClassVar[str]
    description: ClassVar[str]
    options: ClassVar[tuple[Option, ...]] = ()
    inputs: ClassVar[int] = 1               # maximum number of input files

    def convert(self, paths: Sequence[Path]) -> Result:
        """Convert 1 to ``inputs`` input files, in order."""
        paths = [Path(p) for p in paths]
        if not 1 <= len(paths) <= self.inputs:
            expected = "1 input file" if self.inputs == 1 else f"1 to {self.inputs} input files"
            raise ValueError(f"{self.source} -> {self.target} takes {expected}, "
                             f"got {len(paths)}")
        return self._convert(*paths)

    def _convert(self, *paths: Path) -> Result:
        raise NotImplementedError
