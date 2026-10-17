"""Tests for merge_patch with KeyField arrays."""

from typing import Annotated

from pydantic import Field
from pydantic_core import MISSING
from pyvelo.core import VcoModel
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
    def test_update_existing_item(self):
        m = Container(rules=[Rule(name="r1", priority=1), Rule(name="r2", priority=2)])
        m.merge_patch({"rules": {"r1": {"priority": 10}}})
        assert m.rules[0].name == "r1"
        assert m.rules[0].priority == 10
        assert m.rules[1].priority == 2  # unchanged

    def test_add_new_item(self):
        m = Container(rules=[Rule(name="r1")])
        m.merge_patch({"rules": {"r2": {"name": "r2", "priority": 5}}})
        assert len(m.rules) == 2
        assert m.rules[1].name == "r2"
        assert m.rules[1].priority == 5

    def test_unmentioned_items_unchanged(self):
        m = Container(rules=[Rule(name="a"), Rule(name="b"), Rule(name="c")])
        m.merge_patch({"rules": {"b": {"priority": 99}}})
        assert len(m.rules) == 3
        assert m.rules[0].name == "a"
        assert m.rules[1].priority == 99
        assert m.rules[2].name == "c"


class TestKeyFieldMergeDelete:
    def test_null_deletes_item(self):
        m = Container(rules=[Rule(name="r1"), Rule(name="r2"), Rule(name="r3")])
        m.merge_patch({"rules": {"r2": None}})
        assert len(m.rules) == 2
        names = [r.name for r in m.rules]
        assert "r2" not in names

    def test_delete_nonexistent_is_noop(self):
        m = Container(rules=[Rule(name="r1")])
        m.merge_patch({"rules": {"nonexistent": None}})
        assert len(m.rules) == 1


class TestKeyFieldNestedKeyPath:
    def test_dotted_key_path(self):
        m = NestedKeyContainer(
            segments=[
                Segment(meta=SegmentMeta(name="Global"), value=1),
                Segment(meta=SegmentMeta(name="PCI"), value=2),
            ]
        )
        m.merge_patch({"segments": {"Global": {"value": 99}}})
        assert m.segments[0].value == 99
        assert m.segments[1].value == 2  # unchanged


class TestKeyFieldMergePosition:
    def test_position_numeric(self):
        m = Container(rules=[Rule(name="a"), Rule(name="b")])
        m.merge_patch({"rules": {"c": {"name": "c", "priority": 1, "_position": 0}}})
        assert m.rules[0].name == "c"
        assert m.rules[1].name == "a"
        assert m.rules[2].name == "b"

    def test_position_before(self):
        m = Container(rules=[Rule(name="a"), Rule(name="b")])
        m.merge_patch({"rules": {"c": {"name": "c", "_position": "before:b"}}})
        assert [r.name for r in m.rules] == ["a", "c", "b"]

    def test_position_after(self):
        m = Container(rules=[Rule(name="a"), Rule(name="b")])
        m.merge_patch({"rules": {"c": {"name": "c", "_position": "after:a"}}})
        assert [r.name for r in m.rules] == ["a", "c", "b"]

    def test_position_moves_existing(self):
        m = Container(rules=[Rule(name="a"), Rule(name="b"), Rule(name="c")])
        m.merge_patch({"rules": {"c": {"_position": 0}}})
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
    """Regression: newly-added keyed-list items must not auto-fill MISSING
    fields unless the field's ContextDefaults specifies a default for the
    active config level (was the DeviceSegment edge-merge bug)."""

    def test_no_context_preserves_missing(self):
        m = _CtxContainer()
        m.merge_patch({"items": {"a": {"name": "a"}}})
        assert m.items[0].plain is MISSING
        assert m.items[0].profile_only is MISSING

    def test_edge_context_preserves_missing(self):
        m = _CtxContainer().for_context(ConfigContext.EDGE)
        m.merge_patch({"items": {"a": {"name": "a"}}})
        assert m.items[0].plain is MISSING
        assert m.items[0].profile_only is MISSING

    def test_profile_context_fills_only_declared_defaults(self):
        m = _CtxContainer().for_context(ConfigContext.PROFILE)
        m.merge_patch({"items": {"a": {"name": "a"}}})
        # plain has no ContextDefault -> stays MISSING
        assert m.items[0].plain is MISSING
        # profile_only declares profile=_SubB -> filled
        assert isinstance(m.items[0].profile_only, _SubB)
