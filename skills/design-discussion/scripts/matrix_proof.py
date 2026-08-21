#!/usr/bin/env python3

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


TestMethod = TypeVar("TestMethod", bound=Callable[..., object])


def matrix_proof(*cells: str) -> Callable[[TestMethod], TestMethod]:
    if not cells or any(":" not in cell for cell in cells):
        raise ValueError("matrix proof cells must use section:id identities")

    def decorate(method: TestMethod) -> TestMethod:
        setattr(method, "matrix_proof_cells", frozenset(cells))
        return method

    return decorate
