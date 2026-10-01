"""SoundID Reference speaker layouts: the ``ChannelLayout`` of a project.

Copied from the ``SpeakerConfig_*`` classes of Reference Measure 5.13.  A
channel's index in the layout is its ``ChannelIndex``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Channel:
    name: str  # ChannelName
    short: str  # abbreviation
    group: str  # ChannelGroup

    @property
    def is_lfe(self) -> bool:
        return self.short == "LFE"


@dataclass(frozen=True)
class Layout:
    id: int  # ChannelLayout
    name: str
    channels: tuple[Channel, ...]

    def index(self, short: str) -> int:
        """Index of the channel abbreviated ``short``."""
        for i, c in enumerate(self.channels):
            if c.short == short:
                return i
        raise ValueError(f"layout {self.name} has no {short} channel")


def _pair(name: str, short: str, group: str) -> tuple[Channel, Channel]:
    return (
        Channel(f"Left {name}", f"L{short}", group),
        Channel(f"Right {name}", f"R{short}", group),
    )


L, R = Channel("Left", "L", "Front"), Channel("Right", "R", "Front")
C = Channel("Center", "C", "Center")
LFE = Channel("Low freq. effects", "LFE", "Sub")
CS = Channel("Center Surround", "Cs", "Surround")
WIDE = _pair("Wide", "w", "Wide")
SURROUND = _pair("Surround", "s", "Surround")
REAR = _pair("Rear Surround", "rs", "Rear Surround")
TOP_FRONT = _pair("Top Front", "tf", "Top Front")
TOP_MIDDLE = _pair("Top Middle", "tm", "Top Middle")
TOP_REAR = _pair("Top Rear", "tr", "Top Rear")
FRONT_HEIGHT = _pair("Front Height", "fh", "Front Height")
REAR_HEIGHT = _pair("Rear Height", "rh", "Rear Height")


def _regroup(pair: tuple[Channel, Channel], group: str) -> tuple[Channel, Channel]:
    return tuple(replace(c, group=group) for c in pair)


# The overhead 7.1.6 and 9.1.6 put the top front and rear speakers in the
# height groups; the mounted ones put the height speakers in the top groups.
OVERHEAD = (
    *_regroup(TOP_FRONT, "Front Height"),
    *TOP_MIDDLE,
    *_regroup(TOP_REAR, "Rear Height"),
)
MOUNTED = (
    *_regroup(FRONT_HEIGHT, "Top Front"),
    *TOP_MIDDLE,
    *_regroup(REAR_HEIGHT, "Top Rear"),
)
BED_7_1 = (L, R, C, LFE, *SURROUND, *REAR)
BED_9_1 = (L, R, C, LFE, *WIDE, *SURROUND, *REAR)

LAYOUTS = {
    layout.id: layout
    for layout in (
        Layout(0, "2.0 (Stereo)", (L, R)),
        Layout(1, "2.1", (L, R, LFE)),
        Layout(2, "2.1.2", (L, R, LFE, *TOP_MIDDLE)),
        Layout(3, "3.0", (L, R, C)),
        Layout(4, "3.1", (L, R, C, LFE)),
        # SoundID puts this center in the front group.
        Layout(5, "3.1.2", (L, R, replace(C, group="Front"), LFE, *TOP_MIDDLE)),
        Layout(6, "4.0", (L, R, *SURROUND)),
        Layout(7, "4.1", (L, R, LFE, *SURROUND)),
        Layout(8, "4.1.2", (L, R, LFE, *SURROUND, *TOP_MIDDLE)),
        Layout(9, "4.1.4", (L, R, LFE, *SURROUND, *TOP_FRONT, *TOP_REAR)),
        Layout(10, "5.0", (L, R, C, *SURROUND)),
        Layout(11, "5.1", (L, R, C, LFE, *SURROUND)),
        Layout(12, "5.1.2", (L, R, C, LFE, *SURROUND, *TOP_MIDDLE)),
        Layout(13, "5.1.4", (L, R, C, LFE, *SURROUND, *TOP_FRONT, *TOP_REAR)),
        Layout(25, "6.1", (L, R, C, LFE, *SURROUND, CS)),
        Layout(14, "7.1", BED_7_1),
        Layout(15, "7.1.2", (*BED_7_1, *TOP_MIDDLE)),
        Layout(16, "7.1.4", (*BED_7_1, *TOP_FRONT, *TOP_REAR)),
        Layout(17, "7.1.6 Overhead", (*BED_7_1, *OVERHEAD)),
        Layout(21, "7.1.6 Overhead-Mounted", (*BED_7_1, *MOUNTED)),
        Layout(18, "9.1.2", (*BED_9_1, *TOP_MIDDLE)),
        Layout(19, "9.1.4", (*BED_9_1, *TOP_FRONT, *TOP_REAR)),
        Layout(20, "9.1.6 Overhead", (*BED_9_1, *OVERHEAD)),
        Layout(22, "9.1.6 Overhead-Mounted", (*BED_9_1, *MOUNTED)),
    )
}
STEREO = LAYOUTS[0]
