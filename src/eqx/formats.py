"""Registry of the file formats; detection by file extension and content."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .audyssey import mqx
from .autoeq import response
from .daytonaudio import mic
from .dirac import filterslot, targetcurve
from .fileformat import Format
from .ik import arc4, arcx
from .minidsp import umik
from .rationalacoustics import ascii, crv, trace
from .rew import cal, mdat
from .rme import tmreq
from .rode import fuzzmeasure
from .rogueamoeba import soundsource
from .sennheiser import hpc
from .soundid import (
    export_biquad_json,
    export_biquad_xml,
    export_lvnd,
    export_peq_json,
    export_txt,
    peqb,
    swmicpkg,
    swproj,
    targetpreset,
)
from .wav import fir

FORMATS: dict[str, Format] = {
    f.id: f
    for m in (
        # dayton, umik and ascii before cal: their files are also REW
        # calibration files; dayton before umik: its USB files have the UMIK header.
        swproj,
        peqb,
        swmicpkg,
        targetpreset,
        mdat,
        mic,
        umik,
        ascii,
        cal,
        response,
        # Content checks run in this order: the cheap magics before the
        # MERGING key search of an encrypted export.
        filterslot,
        export_lvnd,
        export_peq_json,
        export_biquad_json,
        export_biquad_xml,
        export_txt,
        soundsource,
        tmreq,
        arcx,
        arc4,
        fir,
        targetcurve,
        mqx,
        fuzzmeasure,
        trace,
        crv,
        hpc,
    )
    for f in getattr(m, "FORMATS", None) or (m.FORMAT,)
}


def detect(path: Path, allowed: Iterable[str] | None = None) -> str:
    """The format of ``path`` by extension.

    Several formats may share an extension (e.g. ``.txt``); ``allowed``, the
    formats valid in this place, then picks one, else the first format whose
    content check accepts the existing file.
    """
    suffix = Path(path).suffix.lower()
    matches = [f.id for f in FORMATS.values() if suffix in f.extensions]
    if len(matches) > 1:
        narrowed = matches
        if allowed is not None:
            allowed = set(allowed)
            narrowed = [m for m in matches if m in allowed]
        if len(narrowed) == 1:
            return narrowed[0]
        sniffed = _sniff(Path(path), narrowed)
        if sniffed is not None:
            return sniffed
        raise ValueError(
            f"the extension of {Path(path).name!r} is ambiguous; "
            f"specify its format (one of: {', '.join(matches)})"
        )
    if matches:
        return matches[0]
    raise ValueError(
        f"cannot detect the format of {Path(path).name!r} from its extension; "
        f"specify it (one of: {', '.join(FORMATS)})"
    )


def _sniff(path: Path, candidates: list[str]) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return next(
        (
            c
            for c in candidates
            if FORMATS[c].sniff is not None and FORMATS[c].sniff(data)
        ),
        None,
    )
