"""Converters between file formats, one class per (source, target) pair."""

from __future__ import annotations

from .arc4_swproj import Arc4ToSwproj
from .arcx_swproj import ArcxToSwproj
from .base import Converter, Result
from .dirac import AutoeqToDiracFilter, DiracFilterToAutoeq, DiracFilterToFir, FirToDiracFilter
from .fir import AutoeqToFir, FirToAutoeq, PeqbToFir, SwprojToFir
from .from_autoeq import (AutoeqToArcx, AutoeqToMdat, AutoeqToPeqb, AutoeqToRewcal,
                          AutoeqToSoundsource, AutoeqToSwproj, AutoeqToTargetcurve,
                          AutoeqToTmreq)
from .mdat_swproj import MdatToSwproj
from .rewcal_swmicpkg import RewcalToSwmicpkg
from .swmicpkg_rewcal import SwmicpkgToRewcal
from .to_autoeq import (Arc4ToAutoeq, ArcxToAutoeq, MdatToAutoeq, PeqbToAutoeq,
                        SoundidExportBiquadJsonToAutoeq, SoundidExportBiquadXmlToAutoeq,
                        SoundidExportLvndToAutoeq,
                        SoundidExportPeqJsonToAutoeq, SoundidExportTxtToAutoeq,
                        SoundsourceToAutoeq, SwprojToAutoeq, TargetcurveToAutoeq,
                        TargetpresetToAutoeq, TmreqToAutoeq)

CONVERTERS: dict[tuple[str, str], type[Converter]] = {
    (c.source, c.target): c for c in (
        MdatToSwproj, SwmicpkgToRewcal,
        MdatToAutoeq, SwprojToAutoeq, PeqbToAutoeq, TargetpresetToAutoeq,
        AutoeqToRewcal, AutoeqToSwproj, AutoeqToPeqb, AutoeqToMdat, AutoeqToTmreq, AutoeqToArcx,
        AutoeqToSoundsource,
        RewcalToSwmicpkg,
        SoundidExportBiquadJsonToAutoeq, SoundidExportPeqJsonToAutoeq,
        SoundidExportBiquadXmlToAutoeq, SoundidExportLvndToAutoeq, SoundidExportTxtToAutoeq,
        TmreqToAutoeq, SoundsourceToAutoeq,
        ArcxToAutoeq, ArcxToSwproj, Arc4ToAutoeq, Arc4ToSwproj,
        PeqbToFir, SwprojToFir, AutoeqToFir, FirToAutoeq,
        TargetcurveToAutoeq, AutoeqToTargetcurve,
        DiracFilterToAutoeq, DiracFilterToFir, AutoeqToDiracFilter, FirToDiracFilter,
    )
}


def find(source: str, target: str | None = None) -> type[Converter]:
    """The converter for ``source``; ``target`` None selects the only one."""
    if target is not None:
        if (source, target) not in CONVERTERS:
            raise ValueError(f"no converter from {source} to {target}; "
                             f"available: {pairs()}")
        return CONVERTERS[source, target]
    matches = [c for (s, _), c in CONVERTERS.items() if s == source]
    if len(matches) != 1:
        raise ValueError(f"{len(matches) or 'no'} converters from {source}; "
                         f"specify the target (available: {pairs()})")
    return matches[0]


def pairs() -> str:
    return ", ".join(f"{s}->{t}" for s, t in CONVERTERS)


__all__ = ["CONVERTERS", "Converter", "Result", "find", "pairs"]
