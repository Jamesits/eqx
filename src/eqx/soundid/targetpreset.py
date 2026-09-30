"""Reader for SoundID Custom Target Presets (``Custom Target Presets/*.json``).

A preset is a named list of filter groups.  Each group has a correction
cutoff band and a list of parametric EQ filters that shape the target curve.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .. import dsp
from ..fileformat import Format, Inspector, file_section
from ..report import Section, Table

# Values seen in presets written by SoundID.  Other values are kept as-is.
FILTER_TYPES = ("low-shelf", "bell", "high-shelf")
# "inner": correct only between left_freq and right_freq.
# "outer": correct everywhere except between left_freq and right_freq.
FLIP_STATES = ("inner", "outer")


@dataclass
class Cutoff:
    enabled: bool
    flip_state: str
    left_freq: float                        # Hz
    right_freq: float                       # Hz


@dataclass
class Filter:
    id: int
    type: str
    enabled: bool
    frequency: float                        # Hz
    gain: float                             # dB
    q: float
    color_id: int                           # UI colour only


@dataclass
class FilterGroup:
    cutoff: Cutoff
    filters: list[Filter] = field(default_factory=list)


@dataclass
class TargetPreset:
    name: str
    filter_groups: list[FilterGroup] = field(default_factory=list)


def _get(obj, key: str, kind, where: str):
    if not isinstance(obj, dict):
        raise ValueError(f"{where}: expected an object")
    if key not in obj:
        raise ValueError(f"{where}: missing {key!r}")
    value = obj[key]
    # bool is a subclass of int; do not accept it as a number.
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where}.{key}: expected a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{where}.{key}: not finite")
        return value
    if (kind is int and isinstance(value, bool)) or not isinstance(value, kind):
        raise ValueError(f"{where}.{key}: expected {kind.__name__}")
    return value


def _cutoff(obj, where: str) -> Cutoff:
    return Cutoff(
        enabled=_get(obj, "enabled", bool, where),
        flip_state=_get(obj, "flipState", str, where),
        left_freq=_get(obj, "leftFreq", float, where),
        right_freq=_get(obj, "rightFreq", float, where),
    )


def _filter(obj, where: str) -> Filter:
    return Filter(
        id=_get(obj, "id", int, where),
        type=_get(obj, "type", str, where),
        enabled=_get(obj, "enabled", bool, where),
        frequency=_get(obj, "frequency", float, where),
        gain=_get(obj, "gain", float, where),
        q=_get(obj, "q", float, where),
        color_id=_get(obj, "colorId", int, where),
    )


def read(data: str | bytes) -> TargetPreset:
    try:
        root = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read target preset: {exc}") from exc
    groups = []
    for i, group in enumerate(_get(root, "filterGroups", list, "preset")):
        where = f"filterGroups[{i}]"
        filters = _get(group, "filters", list, where)
        groups.append(FilterGroup(
            cutoff=_cutoff(_get(group, "cutoff", dict, where), f"{where}.cutoff"),
            filters=[_filter(f, f"{where}.filters[{j}]") for j, f in enumerate(filters)],
        ))
    return TargetPreset(_get(root, "name", str, "preset"), groups)


def load(path) -> TargetPreset:
    return read(Path(path).read_bytes())


# --------------------------------------------------------------------------
# target curve
# --------------------------------------------------------------------------
def _biquad(f: Filter) -> dsp.Biquad:
    """SoundID evaluates the filters as Audio EQ Cookbook biquads at 48 kHz.

    A shelf's q is the cookbook's shelf slope S, not Q.
    """
    if f.type == "bell":
        return dsp.bell(f.frequency, f.gain, f.q)
    if f.type not in ("low-shelf", "high-shelf"):
        raise ValueError(f"filter {f.id}: unknown type {f.type!r}")
    return dsp.shelf(f.type == "high-shelf", f.frequency, f.gain, f.q)


def filter_response(f: Filter, frequency: float) -> float:
    """Gain of one filter at ``frequency`` Hz, dB."""
    return _biquad(f).db(frequency, dsp.PEQ_SAMPLE_RATE)


def target_response(preset: TargetPreset, frequencies) -> list[float]:
    """The target curve, dB: the sum of the enabled filters of every group.

    The cutoff band limits the correction, not the target, so it is not applied.
    """
    filters = [f for g in preset.filter_groups for f in g.filters if f.enabled]
    return [sum(filter_response(f, x) for f in filters) + 0.0 for x in frequencies]


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
class TargetPresetInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        preset = read(data)
        sections = [file_section(path, data, ("preset", preset.name),
                                 ("filter groups", len(preset.filter_groups)))]
        for i, g in enumerate(preset.filter_groups):
            c = g.cutoff
            sections.append(Section(
                f"filter group [{i}]",
                [("cutoff enabled", c.enabled), ("cutoff flip state", c.flip_state),
                 ("cutoff band", f"{c.left_freq:g}-{c.right_freq:g} Hz"),
                 ("filters", len(g.filters))],
                Table(["id", "type", "enabled", "frequency Hz", "gain dB", "q", "color"],
                      [(f.id, f.type, f.enabled, f.frequency, f.gain, f.q, f.color_id)
                       for f in g.filters]),
            ))
        return sections


FORMAT = Format("targetpreset", (".json",), "SoundID Custom Target Preset",
                TargetPresetInspector)
