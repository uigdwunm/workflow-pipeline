"""Command registry that generates both argparse and dispatch."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommandSpec:
    name: str
    handler: Callable[[argparse.Namespace], Any]
    raw_output: bool = False


class CommandRegistry:
    """Own the complete command interface behind one registry seam."""

    def __init__(self, specs: Iterable[CommandSpec]) -> None:
        self.specs = tuple(specs)
        names = [spec.name for spec in self.specs]
        if len(names) != len(set(names)):
            raise ValueError("duplicate supervision command")
        self._by_name = {spec.name: spec for spec in self.specs}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.specs)

    def build_parser(self, *, description: str) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description=description)
        subparsers = parser.add_subparsers(dest="command", required=True)
        for spec in self.specs:
            subparsers.add_parser(spec.name)
        return parser

    def dispatch(self, arguments: argparse.Namespace) -> tuple[bool, Any]:
        spec = self._by_name[arguments.command]
        return spec.raw_output, spec.handler(arguments)
