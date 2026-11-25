"""Tests for the generic intent diff engine."""

import pytest
from pydantic import BaseModel
from pyvelo.config.diff import (
    Added,
    DiffResult,
    FieldChange,
    Removed,
    Unchanged,
    diff_intents,
)

# --- Simple test model ---


class Item(BaseModel):
    name: str
    value: int = 0
    tag: str = "default"


class Nested(BaseModel):
    name: str
    inner: Item = Item(name="x")


# --- Empty lists ---


class TestEmptyLists:
    def test_both_empty(self) -> None:
        result = diff_intents([], [], "name")
        assert result.changes == []
        assert not result.has_changes

    def test_current_empty(self) -> None:
        desired = [Item(name="a", value=1)]
        result = diff_intents([], desired, "name")
        assert len(result.added) == 1
        assert result.added[0].key == "a"
        assert result.added[0].item == desired[0]
        assert result.has_changes

    def test_desired_empty(self) -> None:
        current = [Item(name="a", value=1)]
        result = diff_intents(current, [], "name")
        assert len(result.removed) == 1
        assert result.removed[0].key == "a"
        assert result.removed[0].item == current[0]
        assert result.has_changes


# --- Basic change types ---


class TestChangeTypes:
    def test_added(self) -> None:
        desired = [Item(name="new", value=42)]
        result = diff_intents([], desired, "name")
        assert len(result.changes) == 1
        added = result.added[0]
        assert isinstance(added, Added)
        assert added.key == "new"

    def test_removed(self) -> None:
        current = [Item(name="old", value=1)]
        result = diff_intents(current, [], "name")
        assert len(result.changes) == 1
        removed = result.removed[0]
        assert isinstance(removed, Removed)
        assert removed.key == "old"

    def test_unchanged(self) -> None:
        item = Item(name="same", value=5, tag="x")
        result = diff_intents([item], [Item(name="same", value=5, tag="x")], "name")
        assert len(result.changes) == 1
        assert isinstance(result.unchanged[0], Unchanged)
        assert not result.has_changes

    def test_modified_single_field(self) -> None:
        current = [Item(name="a", value=1)]
        desired = [Item(name="a", value=2)]
        result = diff_intents(current, desired, "name")
        assert len(result.modified) == 1
        mod = result.modified[0]
        assert mod.key == "a"
        assert mod.current == current[0]
        assert mod.desired == desired[0]
        assert len(mod.changes) == 1
        assert mod.changes[0] == FieldChange(path="value", current=1, desired=2)

    def test_modified_multiple_fields(self) -> None:
        current = [Item(name="a", value=1, tag="old")]
        desired = [Item(name="a", value=2, tag="new")]
        result = diff_intents(current, desired, "name")
        mod = result.modified[0]
        assert len(mod.changes) == 2
        paths = {c.path for c in mod.changes}
        assert paths == {"tag", "value"}


# --- Nested models ---


class TestNestedDiff:
    def test_nested_field_change(self) -> None:
        current = [Nested(name="n", inner=Item(name="x", value=1))]
        desired = [Nested(name="n", inner=Item(name="x", value=2))]
        result = diff_intents(current, desired, "name")
        mod = result.modified[0]
        assert len(mod.changes) == 1
        assert mod.changes[0] == FieldChange(path="inner.value", current=1, desired=2)

    def test_nested_multiple_changes(self) -> None:
        current = [Nested(name="n", inner=Item(name="x", value=1, tag="a"))]
        desired = [Nested(name="n", inner=Item(name="y", value=2, tag="a"))]
        result = diff_intents(current, desired, "name")
        mod = result.modified[0]
        paths = {c.path for c in mod.changes}
        assert paths == {"inner.name", "inner.value"}

    def test_nested_unchanged(self) -> None:
        item = Nested(name="n", inner=Item(name="x", value=1))
        result = diff_intents(
            [item], [Nested(name="n", inner=Item(name="x", value=1))], "name"
        )
        assert not result.has_changes


# --- Mixed changes ---


class TestMixedChanges:
    def test_mixed(self) -> None:
        current = [
            Item(name="keep", value=1),
            Item(name="change", value=1),
            Item(name="remove", value=1),
        ]
        desired = [
            Item(name="keep", value=1),
            Item(name="change", value=2),
            Item(name="add", value=3),
        ]
        result = diff_intents(current, desired, "name")
        assert len(result.added) == 1
        assert result.added[0].key == "add"
        assert len(result.removed) == 1
        assert result.removed[0].key == "remove"
        assert len(result.modified) == 1
        assert result.modified[0].key == "change"
        assert len(result.unchanged) == 1
        assert result.unchanged[0].key == "keep"
        assert result.has_changes


# --- DiffResult properties ---


class TestDiffResult:
    def test_all_unchanged(self) -> None:
        items = [Item(name="a", value=1), Item(name="b", value=2)]
        result = diff_intents(
            items, [Item(name="a", value=1), Item(name="b", value=2)], "name"
        )
        assert not result.has_changes
        assert len(result.unchanged) == 2
        assert result.added == []
        assert result.removed == []
        assert result.modified == []

    def test_empty_result(self) -> None:
        result = DiffResult(changes=[])
        assert not result.has_changes
        assert result.added == []
        assert result.removed == []
        assert result.modified == []
        assert result.unchanged == []


# --- Integer key field ---


class TestIntegerKeyField:
    def test_integer_key(self) -> None:
        current = [Item(name="a", value=1), Item(name="b", value=2)]
        desired = [Item(name="a", value=1), Item(name="c", value=3)]
        result = diff_intents(current, desired, "value")
        assert len(result.added) == 1
        assert result.added[0].key == 3
        assert len(result.removed) == 1
        assert result.removed[0].key == 2
        assert len(result.unchanged) == 1
        assert result.unchanged[0].key == 1


class TestSharedKeys:
    def test_items_that_share_a_key_are_refused(self) -> None:
        current = [Item(name="web", value=80)]
        desired = [
            Item(name="web", value=8080),
            Item(name="web", value=80),
            Item(name="ssh", value=22),
            Item(name="ssh", value=2222),
        ]
        with pytest.raises(ValueError, match="the desired items share a name") as exc:
            _ = diff_intents(current, desired, "name")
        assert str(exc.value) == (
            "the desired items share a name: 'web', 'ssh'. diff_intents pairs "
            "items by that field, so each value must be there once; diff by a "
            "field that is unique, or remove the repeats"
        )
        with pytest.raises(ValueError, match="the current items share a name: 'web'"):
            _ = diff_intents([*current, *current], [], "name")
