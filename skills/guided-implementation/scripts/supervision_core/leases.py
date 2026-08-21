"""Shared structural policy for bounded CAS lease holders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Collection


@dataclass(frozen=True)
class LeaseHolderPolicy:
    """Validate only the holder structure common to document and Git leases.

    Domain-specific stage and purpose vocabularies remain parameters owned by the
    calling lease module; this policy does not merge their protocol authority.
    """

    stages: Collection[str]
    purposes: Collection[str]
    protocol_error: type[Exception]
    expect_object: Callable[[Any, str], dict[str, Any]]
    expect_keys: Callable[[dict[str, Any], set[str], str], None]
    expect_int: Callable[[Any, str, int, int], int]
    expect_string: Callable[..., str]
    expect_lease_id: Callable[[Any, str], str]
    verbose_vocab_errors: bool = False
    vocabulary_order: tuple[str, str] = ("stage", "purpose")

    def validate(self, value: Any, label: str) -> dict[str, Any]:
        holder = self.expect_object(value, label)
        self.expect_keys(
            holder,
            {
                "acquired_at_epoch", "expires_at_epoch", "lease_id",
                "owner_host_id", "owner_task_id", "purpose", "stage",
            },
            label,
        )
        acquired = self.expect_int(
            holder["acquired_at_epoch"], f"{label}.acquired_at_epoch", 0, 2**63 - 1
        )
        expires = self.expect_int(
            holder["expires_at_epoch"], f"{label}.expires_at_epoch", 1, 2**63 - 1
        )
        if expires <= acquired:
            raise self.protocol_error(
                f"{label}.expires_at_epoch must be later than acquired_at_epoch"
            )
        vocabularies = {"stage": self.stages, "purpose": self.purposes}
        parsed: dict[str, str] = {}
        for field in self.vocabulary_order:
            value = self.expect_string(
                holder[field], f"{label}.{field}", max_bytes=128
            )
            parsed[field] = value
            allowed = vocabularies[field]
            if value not in allowed:
                message = (
                    f"{label}.{field} is unsupported; expected one of "
                    f"{sorted(allowed)!r}; observed={value!r}"
                    if self.verbose_vocab_errors
                    else f"{label}.{field} is unsupported: {value!r}"
                )
                raise self.protocol_error(message)
        stage = parsed["stage"]
        purpose = parsed["purpose"]
        return {
            "acquired_at_epoch": acquired,
            "expires_at_epoch": expires,
            "lease_id": self.expect_lease_id(holder["lease_id"], f"{label}.lease_id"),
            "owner_host_id": self.expect_string(
                holder["owner_host_id"], f"{label}.owner_host_id", max_bytes=256
            ),
            "owner_task_id": self.expect_string(
                holder["owner_task_id"], f"{label}.owner_task_id", max_bytes=256
            ),
            "purpose": purpose,
            "stage": stage,
        }
