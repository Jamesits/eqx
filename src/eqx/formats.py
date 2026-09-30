"""Registry of the file formats; detection by file extension."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .autoeq import response
from .fileformat import Format
from .rew import cal, mdat
from .soundid import peqb, swmicpkg, swproj, targetpreset

FORMATS: dict[str, Format] = {m.FORMAT.id: m.FORMAT for m in (
    swproj, peqb, swmicpkg, targetpreset, mdat, cal, response,
)}


def detect(path: Path, allowed: Iterable[str] = ()) -> str:
    """The format of ``path`` by extension.

    Several formats may share an extension (e.g. ``.txt``); ``allowed``, the
    formats valid in this place, then picks one.
    """
    suffix = Path(path).suffix.lower()
    matches = [f.id for f in FORMATS.values() if suffix in f.extensions]
    if len(matches) > 1:
        allowed = set(allowed)
        narrowed = [m for m in matches if m in allowed]
        if len(narrowed) == 1:
            return narrowed[0]
        raise ValueError(f"the extension of {Path(path).name!r} is ambiguous; "
                         f"specify its format (one of: {', '.join(matches)})")
    if matches:
        return matches[0]
    raise ValueError(f"cannot detect the format of {Path(path).name!r} from its extension; "
                     f"specify it (one of: {', '.join(FORMATS)})")
