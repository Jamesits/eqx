"""Inspect and convert audio correction files."""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from . import convert, formats
from .options import Option
from .report import Section
from .soundid import computerid

PROG = "eqx"
PREVIEW_HEAD = 3
PREVIEW_TAIL = 2
PREVIEW_WIDTH = 100


# --------------------------------------------------------------------------
# per-class options
# --------------------------------------------------------------------------
def _add_options(parser: argparse.ArgumentParser, groups: Iterable[tuple[str, tuple]]) -> dict:
    """Add every class option to ``parser``; return {dest: flag}.

    Values default to absent, so only given options reach the constructor.
    Classes may share an option only if it is declared identically.
    """
    added: dict[str, Option] = {}
    for title, options in groups:
        group = None
        for option in options:
            if option.dest in added:
                if not option.same_as(added[option.dest]):
                    raise ValueError(f"conflicting declarations of {option.flags[0]}")
                continue
            if group is None:
                group = parser.add_argument_group(title)
            group.add_argument(*option.flags, default=argparse.SUPPRESS, **option.kwargs)
            added[option.dest] = option
    return {dest: option.flags[0] for dest, option in added.items()}


def _class_options(args: argparse.Namespace, dests: dict, cls, what: str) -> dict[str, Any]:
    own = {option.dest for option in cls.options}
    given = {dest: value for dest, value in vars(args).items() if dest in dests}
    wrong = [dests[dest] for dest in given if dest not in own]
    if wrong:
        raise ValueError(f"{', '.join(wrong)} does not apply to {what}")
    return given


# --------------------------------------------------------------------------
# inspect
# --------------------------------------------------------------------------
def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return format(value, ".6g")
    return str(value).replace("\n", "\\n")


def _clip(text: str, full: bool) -> str:
    if full or len(text) <= PREVIEW_WIDTH:
        return text
    return text[:PREVIEW_WIDTH - 3] + "..."


def render(sections: list[Section], full: bool = False) -> str:
    out: list[str] = []
    for section in sections:
        out.append(f"== {section.title}")
        # A preview leaves out empty values.
        fields = [(k, _cell(v)) for k, v in section.fields if full or _cell(v)]
        width = max((len(k) for k, _ in fields), default=0)
        out += [f"  {k:<{width}} : {_clip(v, full)}" for k, v in fields]
        table = section.table
        if table is not None and table.rows:
            rows = [[_cell(v) for v in row] for row in table.rows]
            hidden = 0
            if not full and len(rows) > PREVIEW_HEAD + PREVIEW_TAIL + 1:
                hidden = len(rows) - PREVIEW_HEAD - PREVIEW_TAIL
                rows = rows[:PREVIEW_HEAD] + [["..."] * len(table.columns)] + rows[-PREVIEW_TAIL:]
            widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(table.columns)]
            line = lambda cells: "  " + "  ".join(f"{c:>{w}}" for c, w in zip(cells, widths))
            out.append(line(table.columns))
            out += [line(r) for r in rows]
            if hidden:
                out.append(f"  ({hidden} of {len(table.rows)} rows hidden; --full shows all)")
        if full and section.raw:
            out.append("  -- raw")
            out += [f"  {line}" for line in section.raw.splitlines()]
        out.append("")
    return "\n".join(out)


def cmd_inspect(args) -> int:
    kind = args.format or formats.detect(args.file)
    cls = formats.FORMATS[kind].inspector
    inspector = cls(**_class_options(args, args.option_dests, cls, kind))
    sys.stdout.write(render(inspector.inspect(args.file), args.full))
    return 0


# --------------------------------------------------------------------------
# convert
# --------------------------------------------------------------------------
def cmd_convert(args) -> int:
    source = args.source or formats.detect(args.input)
    target = args.target
    if target is None and args.output is not None:
        target = formats.detect(args.output, (t for s, t in convert.CONVERTERS if s == source))
    cls = convert.find(source, target)
    converter = cls(**_class_options(args, args.option_dests, cls,
                                     f"{cls.source} -> {cls.target}"))
    result = converter.convert(args.input)
    output = args.output or args.input.with_name(result.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.data)
    print(f"wrote {output} ({len(result.data):,} bytes)")
    for note in result.notes:
        print(note)
    return 0


# --------------------------------------------------------------------------
# computer-id
# --------------------------------------------------------------------------
def cmd_computer_id(args) -> int:
    cid = computerid.local()
    print(f"cpu          : {cid.cpu.decode('latin-1')}")
    print(f"disk serial  : {cid.disk_serial.decode('latin-1')!r}")
    print(f"board serial : {cid.board_serial.decode('latin-1')!r}")
    print(f"computer id  : {cid.value}")
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def _format_list() -> str:
    return "formats (detected by extension):\n" + "\n".join(
        f"  {f.id:<13} {' '.join(f.extensions):<14} {f.description}"
        for f in formats.FORMATS.values())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    raw = argparse.RawDescriptionHelpFormatter

    p = sub.add_parser("inspect", help="print the sections of a file with a preview of the values",
                       formatter_class=raw, epilog=_format_list())
    p.add_argument("file", type=Path)
    p.add_argument("--format", choices=formats.FORMATS, help="file format (default: by extension)")
    p.add_argument("--full", action="store_true",
                   help="print every value and the raw data, not a preview")
    dests = _add_options(p, ((f"{f.id} options", f.inspector.options)
                             for f in formats.FORMATS.values()))
    p.set_defaults(func=cmd_inspect, option_dests=dests)

    p = sub.add_parser(
        "convert", help="convert a file to another format", formatter_class=raw,
        epilog="conversions:\n" + "\n".join(
            f"  {s} -> {t}: {c.description}" for (s, t), c in convert.CONVERTERS.items())
        + "\n\n" + _format_list())
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path,
                   help="output file (default: next to INPUT, named by the conversion)")
    p.add_argument("--from", dest="source", choices=formats.FORMATS,
                   help="input format (default: by INPUT extension)")
    p.add_argument("--to", dest="target", choices=formats.FORMATS,
                   help="output format (default: by OUTPUT extension, or the only "
                        "conversion of the input format)")
    dests = _add_options(p, ((f"{c.source} -> {c.target} options", c.options)
                             for c in convert.CONVERTERS.values()))
    p.set_defaults(func=cmd_convert, option_dests=dests)

    p = sub.add_parser("computer-id",
                       help="print this machine's SoundID computer ID (the .swhp password)")
    p.set_defaults(func=cmd_computer_id)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # The reader (e.g. ``head``) closed stdout; silence the exit-time flush error.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except (OSError, ValueError, ET.ParseError) as exc:
        parser.exit(1, f"{PROG}: error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
