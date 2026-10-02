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
CONVERT_HELP = "convert a file to another format"


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


def _ids(ids: Iterable[str]) -> list[str]:
    """``ids`` once each, in registry order."""
    ids = set(ids)
    return [f for f in formats.FORMATS if f in ids]


def _sources(target: str | None = None) -> list[str]:
    """The formats converting to ``target`` (default: to any)."""
    return _ids(s for s, t in convert.CONVERTERS if target in (None, t))


def _targets(sources: Iterable[str]) -> list[str]:
    """The formats one of ``sources`` converts to."""
    sources = set(sources)
    return _ids(t for s, t in convert.CONVERTERS if s in sources)


def _detect(path: Path, allowed: list[str]) -> str | None:
    try:
        return formats.detect(path, allowed)
    except ValueError:
        return None


def _by_extension(path: Path | None, allowed: list[str]) -> list[str]:
    """The formats in ``allowed`` with the extension of ``path``; all of them
    if none has it."""
    suffix = path.suffix.lower() if path is not None else None
    return [f for f in allowed if suffix in formats.FORMATS[f].extensions] or allowed


def _conversion(args) -> tuple[str | None, str | None]:
    """(source, target) of a conversion; None if not determined."""
    source, target = args.source, args.target
    if source is None and args.input:
        allowed = _sources(target)
        detected = {_detect(path, allowed) for path in args.input}
        known = sorted(detected - {None})
        if len(known) > 1:
            raise ValueError(
                f"inputs of different formats: {', '.join(known)}; specify --from"
            )
        if None not in detected:
            source = known[0]
    if target is None:
        allowed = _targets([source] if source is not None else _sources())
        if args.output is not None:
            target = _detect(args.output, allowed)
        elif source is not None and len(allowed) == 1:
            target = allowed[0]
    return source, target


def _candidates(
    args, source: str | None, target: str | None
) -> tuple[list[str], list[str], Path | None]:
    """The --from and --to values left to choose from; and the first input
    whose format is not determined."""
    path = None
    if source is not None:
        froms = [source]
    else:
        allowed = _sources(target)
        path = next((p for p in args.input or () if _detect(p, allowed) is None), None)
        froms = _by_extension(path, allowed)
    tos = (
        [target] if target is not None else _by_extension(args.output, _targets(froms))
    )
    return froms, tos, path


def _pair_title(cls) -> str:
    return f"{cls.source} -> {cls.target}"


def _convert_args(
    parser: argparse.ArgumentParser, groups: Iterable[tuple[str, tuple]]
) -> dict:
    """Add the common arguments and the option ``groups``; return {dest: flag}."""
    parser.add_argument(
        "-h",
        "--help",
        action="store_true",
        help="show the common arguments and the conversions; with the formats "
        "known, the options of the conversion, else the formats to choose from",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        action="append",
        help="input file (required); repeatable, in the order the conversion "
        "takes them",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file (default: next to the first INPUT, named by the conversion)",
    )
    parser.add_argument(
        "--from",
        dest="source",
        choices=formats.FORMATS,
        metavar="FORMAT",
        help="input format (default: by INPUT extension, then content)",
    )
    parser.add_argument(
        "--to",
        dest="target",
        choices=formats.FORMATS,
        metavar="FORMAT",
        help="output format (default: by OUTPUT extension, or the only "
        "conversion of the input format)",
    )
    return _add_options(parser, groups)


def convert_help(args, source: str | None, target: str | None) -> str:
    """The common arguments, and what ``args`` leaves to choose: all
    conversions, the --from / --to values, or the options of the pair."""
    parser = argparse.ArgumentParser(
        prog=f"{PROG} convert",
        description=CONVERT_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    if source is not None and target is not None:
        cls = convert.find(source, target)
        _convert_args(parser, [(f"{_pair_title(cls)} options", cls.options)])
        parser.epilog = f"{_pair_title(cls)}: {cls.description}"
    elif not (args.input or args.output or args.source or args.target):
        _convert_args(parser, ())
        parser.epilog = (
            "conversions:\n"
            + "\n".join(
                f"  {_pair_title(c)}: {c.description}"
                for c in convert.CONVERTERS.values()
            )
            + "\n\n"
            + _format_list()
        )
    else:
        _convert_args(parser, ())
        froms, tos, path = _candidates(args, source, target)
        sections = []
        if source is None:
            of = f" for {path.name!r}" if path is not None else ""
            sections.append(f"--from values{of}:\n" + _format_table(froms))
        if target is None:
            of = f" for {args.output.name!r}" if args.output is not None else ""
            sections.append(f"--to values{of}:\n" + _format_table(tos))
        parser.epilog = "\n\n".join(sections)
    return parser.format_help()


def cmd_convert(args) -> int:
    source, target = _conversion(args)
    if args.help:
        sys.stdout.write(convert_help(args, source, target))
        return 0
    if not args.input:
        raise ValueError("-i/--input is required")
    if source is None or target is None:
        froms, tos, path = _candidates(args, source, target)
        if source is None:
            raise ValueError(
                f"cannot detect the format of {path.name!r}; "
                f"specify --from (one of: {', '.join(froms)})"
            )
        if args.output is not None:
            raise ValueError(
                f"cannot detect the format of {args.output.name!r}; "
                f"specify --to (one of: {', '.join(tos)})"
            )
        raise ValueError(
            f"{len(tos)} converters from {source}; "
            f"specify --to (one of: {', '.join(tos)})"
        )
    cls = convert.find(source, target)
    converter = cls(**_class_options(args, args.option_dests, cls, _pair_title(cls)))
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
def _format_table(ids: Iterable[str]) -> str:
    rows = [formats.FORMATS[f] for f in ids]
    width = max(len(f.id) for f in rows)
    return "\n".join(
        f"  {f.id:<{width}} {' '.join(f.extensions):<14} {f.description}" for f in rows
    )


def _format_list() -> str:
    return "formats (detected by extension, then by content):\n" + _format_table(
        formats.FORMATS
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

    # Parses the options of every pair, so one of another pair is named in the
    # error; its own --help shows only those of the pair given.
    p = sub.add_parser("convert", help=CONVERT_HELP, add_help=False)
    dests = _convert_args(
        p,
        ((f"{_pair_title(c)} options", c.options) for c in convert.CONVERTERS.values()),
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
