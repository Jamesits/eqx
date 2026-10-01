"""Inspect and convert audio correction files."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import convert, formats, graph
from .options import Option
from .report import Section
from .soundid import computerid

PROG = "eqx"
PREVIEW_HEAD = 3
PREVIEW_TAIL = 2
PREVIEW_WIDTH = 100
GRAPH_HEIGHT = 14
GRAPH_MAX_WIDTH = 120


# --------------------------------------------------------------------------
# per-class options
# --------------------------------------------------------------------------
def _add_options(
    parser: argparse.ArgumentParser, groups: Iterable[tuple[str, tuple]]
) -> dict:
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
            group.add_argument(
                *option.flags, default=argparse.SUPPRESS, **option.kwargs
            )
            added[option.dest] = option
    return {dest: option.flags[0] for dest, option in added.items()}


def _class_options(
    args: argparse.Namespace, dests: dict, cls, what: str
) -> dict[str, Any]:
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
    return text[: PREVIEW_WIDTH - 3] + "..."


def render(
    sections: list[Section],
    full: bool = False,
    graphs: bool = False,
    color: bool = False,
    graph_width: int = 80,
) -> str:
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
                rows = (
                    rows[:PREVIEW_HEAD]
                    + [["..."] * len(table.columns)]
                    + rows[-PREVIEW_TAIL:]
                )
            widths = [
                max(len(c), *(len(r[i]) for r in rows))
                for i, c in enumerate(table.columns)
            ]
            line = lambda cells, widths=widths: (
                "  " + "  ".join(f"{c:>{w}}" for c, w in zip(cells, widths))
            )
            out.append(line(table.columns))
            out += [line(r) for r in rows]
            if hidden:
                out.append(
                    f"  ({hidden} of {len(table.rows)} rows hidden; --full shows all)"
                )
        curve = section.curve or table
        if graphs and curve is not None:
            for name, points in graph.curves(curve):
                out.append(f"  -- graph: {name}")
                text = graph.plot(points, graph_width - 2, GRAPH_HEIGHT, color)
                out += [f"  {line}".rstrip() for line in text.splitlines()]
        if full and section.raw:
            out.append("  -- raw")
            out += [f"  {line}" for line in section.raw.splitlines()]
        out.append("")
    return "\n".join(out)


def cmd_inspect(args) -> int:
    kind = args.format or formats.detect(args.file)
    cls = formats.FORMATS[kind].inspector
    inspector = cls(**_class_options(args, args.option_dests, cls, kind))
    tty = sys.stdout.isatty()
    supported = graph.supported(sys.stdout.encoding)
    graphs = {"on": True, "off": False}.get(args.graph, tty and supported)
    if graphs and not supported and hasattr(sys.stdout, "reconfigure"):
        # Forced on: a pipe on Windows defaults to the ANSI code page.
        sys.stdout.reconfigure(encoding="utf-8")
    width = min(shutil.get_terminal_size().columns, GRAPH_MAX_WIDTH)
    sys.stdout.write(
        render(inspector.inspect(args.file), args.full, graphs, tty, width)
    )
    return 0


# --------------------------------------------------------------------------
# convert
# --------------------------------------------------------------------------
def write_output(path: Path, data: bytes | dict[str, bytes]) -> int:
    """Write a file, or a package directory; return the byte count.

    A package replaces its own files in an existing directory and leaves the
    others.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
        return len(data)
    if path.exists() and not path.is_dir():
        raise ValueError(f"{path} exists and is not a directory")
    path.mkdir(exist_ok=True)
    for name, contents in data.items():
        (path / name).write_bytes(contents)
    return sum(len(contents) for contents in data.values())


def cmd_convert(args) -> int:
    source = args.source
    if source is None:
        sources = {formats.detect(path) for path in args.input}
        if len(sources) > 1:
            raise ValueError(
                f"inputs of different formats: {', '.join(sorted(sources))}; "
                "specify --from"
            )
        source = sources.pop()
    target = args.target
    if target is None and args.output is not None:
        target = formats.detect(
            args.output, (t for s, t in convert.CONVERTERS if s == source)
        )
    cls = convert.find(source, target)
    converter = cls(
        **_class_options(args, args.option_dests, cls, f"{cls.source} -> {cls.target}")
    )
    result = converter.convert(args.input)
    output = args.output or args.input[0].with_name(result.name)
    size = write_output(output, result.data)
    print(f"wrote {output} ({size:,} bytes)")
    for note in result.notes:
        print(note)
    return 0


# --------------------------------------------------------------------------
# computer-id
# --------------------------------------------------------------------------
def cmd_computer_id(args) -> int:
    cid = computerid.local()
    for label, value in cid.parts():
        print(f"{label:<13}: {value}")
    print(f"{'computer id':<13}: {cid.value}")
    for app, value in cid.values().items():
        print(f"  {app + ':':<30}{value}")
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def _format_list() -> str:
    width = max(len(f) for f in formats.FORMATS)
    return "formats (detected by extension, then by content):\n" + "\n".join(
        f"  {f.id:<{width}} {' '.join(f.extensions):<14} {f.description}"
        for f in formats.FORMATS.values()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    raw = argparse.RawDescriptionHelpFormatter

    p = sub.add_parser(
        "inspect",
        help="print the sections of a file with a preview of the values",
        formatter_class=raw,
        epilog=_format_list(),
    )
    p.add_argument("file", type=Path)
    p.add_argument(
        "--format", choices=formats.FORMATS, help="file format (default: by extension)"
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="print every value and the raw data, not a preview",
    )
    p.add_argument(
        "--graph",
        choices=("auto", "on", "off"),
        default="auto",
        help="draw each curve (default: auto, on a terminal that can show it)",
    )
    dests = _add_options(
        p, ((f"{f.id} options", f.inspector.options) for f in formats.FORMATS.values())
    )
    p.set_defaults(func=cmd_inspect, option_dests=dests)

    p = sub.add_parser(
        "convert",
        help="convert a file to another format",
        formatter_class=raw,
        epilog="conversions:\n"
        + "\n".join(
            f"  {s} -> {t}: {c.description}" for (s, t), c in convert.CONVERTERS.items()
        )
        + "\n\n"
        + _format_list(),
    )
    p.add_argument(
        "-i",
        "--input",
        type=Path,
        action="append",
        required=True,
        help="input file; repeatable, in the order the conversion takes them",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file (default: next to the first INPUT, named by the conversion)",
    )
    p.add_argument(
        "--from",
        dest="source",
        choices=formats.FORMATS,
        help="input format (default: by INPUT extension)",
    )
    p.add_argument(
        "--to",
        dest="target",
        choices=formats.FORMATS,
        help="output format (default: by OUTPUT extension, or the only "
        "conversion of the input format)",
    )
    dests = _add_options(
        p,
        (
            (f"{c.source} -> {c.target} options", c.options)
            for c in convert.CONVERTERS.values()
        ),
    )
    p.set_defaults(func=cmd_convert, option_dests=dests)

    p = sub.add_parser(
        "computer-id",
        help="print this machine's Sonarworks computer IDs (the .swhp passwords)",
    )
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
