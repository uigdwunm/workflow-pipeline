"""Single-authority operation dispatch for the discussion protocol."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .request import RequestContext


Handler = Callable[[RequestContext], dict[str, Any]]


class OperationRegistry:
    """Own the complete operation-to-handler interface behind one small seam."""

    def __init__(self, entries: Iterable[tuple[str, Handler]]) -> None:
        handlers: dict[str, Handler] = {}
        for name, handler in entries:
            if name in handlers:
                raise ValueError(f"duplicate discussion operation: {name}")
            handlers[name] = handler
        self._handlers = handlers

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def dispatch(
        self,
        context: RequestContext,
        *,
        unsupported: Callable[[], Exception],
    ) -> dict[str, Any]:
        handler = (
            self._handlers.get(context.operation)
            if isinstance(context.operation, str)
            else None
        )
        if handler is None:
            raise unsupported()
        return handler(context)
