"""Command-line options declared by inspectors and converters."""

from __future__ import annotations

from typing import Any


class Option:
    """A command-line option of an inspector or converter.

    ``kwargs`` are ``argparse.add_argument`` keyword arguments.  ``dest`` is
    the constructor keyword that receives the value; an option not given on
    the command line keeps the constructor default.
    """

    def __init__(self, *flags: str, **kwargs: Any):
        self.flags = flags
        self.kwargs = kwargs

    @property
    def dest(self) -> str:
        return self.kwargs.get("dest") or self.flags[-1].lstrip("-").replace("-", "_")

    def same_as(self, other: Option) -> bool:
        return self.flags == other.flags and self.kwargs == other.kwargs
