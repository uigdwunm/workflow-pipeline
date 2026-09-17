"""Validated request envelope shared by every discussion operation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Collection


class RequestContext(dict[str, Any]):
    """Dictionary-compatible request with common fields parsed exactly once.

    Existing operation implementations intentionally continue to receive a mapping so
    their observable validation and errors remain stable.  Common path, identity and
    conversation fields are also available as parsed attributes to deeper handlers.
    """

    operation: Any

    @classmethod
    def parse(
        cls,
        request: Any,
        *,
        protocol_version: int,
        error_type: type[Exception],
        parse_project_path: Callable[[Any], Path],
        parse_string: Callable[..., str],
        known_operations: Collection[str],
    ) -> "RequestContext":
        if not isinstance(request, dict):
            raise error_type("invalid_request", "request must be a JSON object")
        if request.get("protocol_version") != protocol_version:
            raise error_type(
                "unsupported_protocol_version",
                f"protocol_version must be {protocol_version}",
            )
        operation = request.get("operation")
        context = cls(request)
        context.operation = operation
        context._known = isinstance(operation, str) and operation in known_operations
        context._parse_project_path = parse_project_path
        context._parse_string = parse_string
        context._parsed: dict[str, Any] = {}
        return context

    def _string(self, field: str, *, max_bytes: int = 512) -> str | None:
        if not self._known or field not in self:
            return None
        if field not in self._parsed:
            self._parsed[field] = self._parse_string(
                self[field], field, max_bytes=max_bytes
            )
        return self._parsed[field]

    @property
    def project_path(self) -> Path | None:
        if not self._known or "project_path" not in self:
            return None
        if "project_path" not in self._parsed:
            self._parsed["project_path"] = self._parse_project_path(self["project_path"])
        return self._parsed["project_path"]

    @property
    def project_id(self) -> str | None:
        return self._string("project_id")

    @property
    def tree_id(self) -> str | None:
        return self._string("tree_id")

    @property
    def actor_topic_id(self) -> str | None:
        return self._string("actor_topic_id")

    @property
    def actor_conversation_ref(self) -> str | None:
        return self._string("actor_conversation_ref", max_bytes=1024)

    @property
    def conversation_ref(self) -> str | None:
        return self._string("conversation_ref", max_bytes=1024)
