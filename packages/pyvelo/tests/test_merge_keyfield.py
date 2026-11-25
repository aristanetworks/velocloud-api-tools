"""Tests for merge_patch with KeyField arrays."""

from typing import Annotated

import pytest
from pydantic import MISSING, Field
from pyvelo.core import PatchError, VcoModel, add, delete, move
from pyvelo.core.fields import ConfigContext, ContextDefault, KeyField


class SegmentMeta(VcoModel):
    name: str
    segment_id: int = 0


class Segment(VcoModel):
    meta: SegmentMeta
    value: int = 0


class Rule(VcoModel):
    name: str
    enabled: bool = True
    priority: int = 0


class Container(VcoModel):
    rules: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)


class NestedKeyContainer(VcoModel):
    segments: Annotated[list[Segment], KeyField("meta.name")] = Field(
        default_factory=list
    )


class TestKeyFieldMergeUpsert:
    def test_update_existing_item(self) -> None:
        m = Container(rules=[Rule(name="r1", priority=1), Rule(name="r2", priority=2)])
        _ = m.merge_patch({"rules": {"r1": {"priority": 10}}})
        assert m.rules[0].name == "r1"
        assert m.rules[0].priority == 10
        assert m.rules[1].priority == 2  # unchanged

    def test_add_new_item(self) -> None:
        m = Container(rules=[Rule(name="r1")])
        _ = m.merge_patch({"rules": {"r2": add({"name": "r2", "priority": 5})}})
        assert len(m.rules) == 2
        assert m.rules[1].name == "r2"
        assert m.rules[1].priority == 5

    def test_unmentioned_items_unchanged(self) -> None:
        m = Container(rules=[Rule(name="a"), Rule(name="b"), Rule(name="c")])
        _ = m.merge_patch({"rules": {"b": {"priority": 99}}})
        assert len(m.rules) == 3
        assert m.rules[0].name == "a"
        assert m.rules[1].priority == 99
        assert m.rules[2].name == "c"


class TestKeyFieldMergeDelete:
    def test_delete_removes_item(self) -> None:
        m = Container(rules=[Rule(name="r1"), Rule(name="r2"), Rule(name="r3")])
        _ = m.merge_patch({"rules": {"r2": delete()}})
        assert len(m.rules) == 2
        names = [r.name for r in m.rules]
        assert "r2" not in names

    def test_delete_nonexistent_raises(self) -> None:
        m = Container(rules=[Rule(name="r1")])
        with pytest.raises(PatchError):
            _ = m.merge_patch({"rules": {"nonexistent": delete()}})
        assert len(m.rules) == 1


class TestKeyFieldNestedKeyPath:
    def test_dotted_key_path(self) -> None:
        m = NestedKeyContainer(
            segments=[
                Segment(meta=SegmentMeta(name="Global"), value=1),
                Segment(meta=SegmentMeta(name="PCI"), value=2),
            ]
        )
        _ = m.merge_patch({"segments": {"Global": {"value": 99}}})
        assert m.segments[0].value == 99
        assert m.segments[1].value == 2  # unchanged


class TestKeyFieldMergePosition:
    def test_position_first(self) -> None:
        m = Container(rules=[Rule(name="a"), Rule(name="b")])
        _ = m.merge_patch({"rules": {"c": add({"priority": 1}, first=True)}})
        assert m.rules[0].name == "c"
        assert m.rules[1].name == "a"
        assert m.rules[2].name == "b"

    def test_position_last(self) -> None:
        m = Container(rules=[Rule(name="a"), Rule(name="b")])
        _ = m.merge_patch({"rules": {"c": add(last=True)}})
        assert [r.name for r in m.rules] == ["a", "b", "c"]

    def test_position_before(self) -> None:
        m = Container(rules=[Rule(name="a"), Rule(name="b")])
        _ = m.merge_patch({"rules": {"c": add(before="b")}})
        assert [r.name for r in m.rules] == ["a", "c", "b"]

    def test_position_after(self) -> None:
        m = Container(rules=[Rule(name="a"), Rule(name="b")])
        _ = m.merge_patch({"rules": {"c": add(after="a")}})
        assert [r.name for r in m.rules] == ["a", "c", "b"]

    def test_position_moves_existing(self) -> None:
        m = Container(rules=[Rule(name="a"), Rule(name="b"), Rule(name="c")])
        _ = m.merge_patch({"rules": {"c": move(first=True)}})
        assert [r.name for r in m.rules] == ["c", "a", "b"]


class _SubA(VcoModel):
    flag: bool = False


class _SubB(VcoModel):
    flag: bool = False


class _CtxItem(VcoModel):
    name: str
    plain: _SubA | MISSING = MISSING  # ty: ignore[invalid-type-form]
    profile_only: Annotated[_SubB | MISSING, ContextDefault(profile=_SubB)] = MISSING  # ty: ignore[invalid-type-form]


class _CtxContainer(VcoModel):
    items: Annotated[list[_CtxItem], KeyField("name")] = Field(default_factory=list)


class TestKeyFieldMissingPreservation:
    """Regression: a newly-added keyed-list item must not auto-fill MISSING fields.

    The exception is a field whose ContextDefaults specifies a default for the
    active config level (this was the DeviceSegment edge-merge bug).
    """

    def test_no_context_preserves_missing(self) -> None:
        m = _CtxContainer()
        _ = m.merge_patch({"items": {"a": add()}})
        assert m.items[0].plain is MISSING
        assert m.items[0].profile_only is MISSING

    def test_edge_context_preserves_missing(self) -> None:
        m = _CtxContainer().for_context(ConfigContext.EDGE)
        _ = m.merge_patch({"items": {"a": add()}})
        assert m.items[0].plain is MISSING
        assert m.items[0].profile_only is MISSING

    def test_profile_context_fills_only_declared_defaults(self) -> None:
        m = _CtxContainer().for_context(ConfigContext.PROFILE)
        _ = m.merge_patch({"items": {"a": add()}})
        # plain has no ContextDefault -> stays MISSING
        assert m.items[0].plain is MISSING
        # profile_only declares profile=_SubB -> filled
        assert isinstance(m.items[0].profile_only, _SubB)
