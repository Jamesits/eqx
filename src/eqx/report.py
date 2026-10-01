"""Structured output of an inspection; the CLI renders it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Table:
    columns: list[str]
    rows: list[tuple]


@dataclass
class Section:
    title: str
    fields: list[tuple[str, Any]] = field(default_factory=list)
    table: Table | None = None
    raw: str | None = None                  # verbatim text, shown in full mode only
    curve: Table | None = None              # computed response, graphed instead of the table
