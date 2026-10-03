"""Converters between file formats, one class per (source, target) pair.

Each ``to_<format>`` module holds the converters to that format.
"""

from __future__ import annotations

from .base import Converter, Result
from .to_arcx import AutoeqToArcx
from .to_autoeq import (
    Arc4ToAutoeq,
    ArcxToAutoeq,
    DearvrHpcToAutoeq,
    DiracFilterToAutoeq,
    FirToAutoeq,
    FuzzmeasureToAutoeq,
    MdatToAutoeq,
    MqxToAutoeq,
    PeqbToAutoeq,
    SmaartAsciiToAutoeq,
    SmaartCurveToAutoeq,
    SmaartRefToAutoeq,
    SmaartSrfToAutoeq,
    SmaartTrfToAutoeq,
    SoundidExportBiquadJsonToAutoeq,
    SoundidExportBiquadXmlToAutoeq,
    SoundidExportLvndToAutoeq,
    SoundidExportPeqJsonToAutoeq,
    SoundidExportTxtToAutoeq,
    SoundsourceToAutoeq,
    SwprojToAutoeq,
    TargetcurveToAutoeq,
    TargetpresetToAutoeq,
    TmreqToAutoeq,
)
from .to_dirac_filter import AutoeqToDiracFilter, FirToDiracFilter
from .to_drc import MdatToDrc
from .to_fir import (
    AutoeqToFir,
    DearvrHpcToFir,
    DiracFilterToFir,
    PeqbToFir,
    SwprojToFir,
)
from .to_fuzzmeasure import AutoeqToFuzzmeasure
from .to_mdat import AutoeqToMdat
from .to_mqx import AutoeqToMqx
from .to_peqb import AutoeqToPeqb
from .to_rewcal import AutoeqToRewcal, SwmicpkgToRewcal, SwmicToRewcal
from .to_smaart_ascii import AutoeqToSmaartAscii
from .to_smaart_curve import AutoeqToSmaartCurve
from .to_smaart_trace import AutoeqToSmaartSrf, AutoeqToSmaartTrf
from .to_soundsource import AutoeqToSoundsource
from .to_swmicpkg import (
    DaytonToSwmicpkg,
    RewcalToSwmicpkg,
    SwmicToSwmicpkg,
    UmikToSwmicpkg,
)
from .to_swproj import (
    Arc4ToSwproj,
    ArcxToSwproj,
    AutoeqToSwproj,
    DrcToSwproj,
    MdatToSwproj,
    MqxToSwproj,
)
from .to_targetcurve import AutoeqToTargetcurve
from .to_tmreq import AutoeqToTmreq

CONVERTERS: dict[tuple[str, str], type[Converter]] = {
    (c.source, c.target): c
    for c in (
        MdatToSwproj,
        SwmicpkgToRewcal,
        SwmicToRewcal,
        MdatToAutoeq,
        SwprojToAutoeq,
        PeqbToAutoeq,
        TargetpresetToAutoeq,
        AutoeqToRewcal,
        AutoeqToSwproj,
        AutoeqToPeqb,
        AutoeqToMdat,
        AutoeqToTmreq,
        AutoeqToArcx,
        AutoeqToSoundsource,
        RewcalToSwmicpkg,
        UmikToSwmicpkg,
        DaytonToSwmicpkg,
        SwmicToSwmicpkg,
        SoundidExportBiquadJsonToAutoeq,
        SoundidExportPeqJsonToAutoeq,
        SoundidExportBiquadXmlToAutoeq,
        SoundidExportLvndToAutoeq,
        SoundidExportTxtToAutoeq,
        TmreqToAutoeq,
        SoundsourceToAutoeq,
        ArcxToAutoeq,
        ArcxToSwproj,
        Arc4ToAutoeq,
        Arc4ToSwproj,
        PeqbToFir,
        SwprojToFir,
        AutoeqToFir,
        FirToAutoeq,
        TargetcurveToAutoeq,
        AutoeqToTargetcurve,
        DiracFilterToAutoeq,
        DiracFilterToFir,
        AutoeqToDiracFilter,
        FirToDiracFilter,
        MqxToAutoeq,
        MqxToSwproj,
        AutoeqToMqx,
        FuzzmeasureToAutoeq,
        AutoeqToFuzzmeasure,
        SmaartTrfToAutoeq,
        AutoeqToSmaartTrf,
        SmaartSrfToAutoeq,
        AutoeqToSmaartSrf,
        SmaartRefToAutoeq,
        SmaartAsciiToAutoeq,
        AutoeqToSmaartAscii,
        SmaartCurveToAutoeq,
        AutoeqToSmaartCurve,
        DearvrHpcToAutoeq,
        DearvrHpcToFir,
        MdatToDrc,
        DrcToSwproj,
    )
}


def find(source: str, target: str | None = None) -> type[Converter]:
    """The converter for ``source``; ``target`` None selects the only one."""
    if target is not None:
        if (source, target) not in CONVERTERS:
            raise ValueError(
                f"no converter from {source} to {target}; available: {pairs()}"
            )
        return CONVERTERS[source, target]
    matches = [c for (s, _), c in CONVERTERS.items() if s == source]
    if len(matches) != 1:
        raise ValueError(
            f"{len(matches) or 'no'} converters from {source}; "
            f"specify the target (available: {pairs()})"
        )
    return matches[0]


def pairs() -> str:
    return ", ".join(f"{s}->{t}" for s, t in CONVERTERS)


__all__ = ["CONVERTERS", "Converter", "Result", "find", "pairs"]
