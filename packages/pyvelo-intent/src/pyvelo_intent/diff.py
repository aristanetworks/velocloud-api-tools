"""Generic diff engine for intent model lists."""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import cast

from pydantic import BaseModel


@dataclass(frozen=True)
class FieldChange:
    """A single field that differs between current and desired state."""

    path: str
    current: object
    desired: object


@dataclass(frozen=True)
class Added:
    """Item exists in desired but not in current."""

    key: object
    item: BaseModel


@dataclass(frozen=True)
class Removed:
    """Item exists in current but not in desired."""

    key: object
    item: BaseModel


@dataclass(frozen=True)
class Modified:
    """Item exists in both but has field-level differences."""

    key: object
    current: BaseModel
    desired: BaseModel
    changes: list[FieldChange]


@dataclass(frozen=True)
class Unchanged:
    """Item is identical in both current and desired."""

    key: object
    item: BaseModel


type Change = Added | Removed | Modified | Unchanged


@dataclass
class DiffResult:
    """Result of diffing two intent model lists."""

    changes: list[Change]

    @cached_property
    def added(self) -> list[Added]:
        return [c for c in self.changes if isinstance(c, Added)]

    @cached_property
    def removed(self) -> list[Removed]:
        return [c for c in self.changes if isinstance(c, Removed)]

    @cached_property
    def modified(self) -> list[Modified]:
        return [c for c in self.changes if isinstance(c, Modified)]

    @cached_property
    def unchanged(self) -> list[Unchanged]:
        return [c for c in self.changes if isinstance(c, Unchanged)]

    @property
    def has_changes(self) -> bool:
        return any(not isinstance(c, Unchanged) for c in self.changes)


def _diff_values(
    current: dict[str, object],
    desired: dict[str, object],
    prefix: str = "",
) -> list[FieldChange]:
    """Recursively diff two model_dump dicts, producing dot-separated field paths."""
    changes: list[FieldChange] = []
    for key in sorted(current.keys() | desired.keys()):
        path = f"{prefix}.{key}" if prefix else key
        cur = current.get(key)
        des = desired.get(key)
        if cur == des:
            continue
        if isinstance(cur, dict) and isinstance(des, dict):
            changes.extend(
                _diff_values(
                    cast(dict[str, object], cur),
                    cast(dict[str, object], des),
                    path,
                )
            )
        else:
            changes.append(
                FieldChange(
                    path=path,
                    current=cast(object, cur),
                    desired=cast(object, des),
                )
            )
    return changes


def diff_intents(
    current: Sequence[BaseModel],
    desired: Sequence[BaseModel],
    key_field: str,
) -> DiffResult:
    """Compare two lists of intent models by key field.

    Returns a DiffResult with Added, Removed, Modified, and Unchanged entries.
    Uses model_dump(mode="json") for symmetric field-level comparison.
    """
    current_by_key: dict[object, BaseModel] = {
        cast(object, getattr(m, key_field)): m for m in current
    }
    desired_by_key: dict[object, BaseModel] = {
        cast(object, getattr(m, key_field)): m for m in desired
    }

    changes: list[Change] = []

    for key, item in desired_by_key.items():
        if key not in current_by_key:
            changes.append(Added(key=key, item=item))

    for key, item in current_by_key.items():
        if key not in desired_by_key:
            changes.append(Removed(key=key, item=item))

    for key in sorted(current_by_key.keys() & desired_by_key.keys(), key=repr):
        cur = current_by_key[key]
        des = desired_by_key[key]
        cur_d = cast(dict[str, object], cur.model_dump(mode="json"))
        des_d = cast(dict[str, object], des.model_dump(mode="json"))
        if cur_d == des_d:
            changes.append(Unchanged(key=key, item=cur))
        else:
            field_changes = _diff_values(cur_d, des_d)
            changes.append(
                Modified(key=key, current=cur, desired=des, changes=field_changes)
            )

    return DiffResult(changes)
