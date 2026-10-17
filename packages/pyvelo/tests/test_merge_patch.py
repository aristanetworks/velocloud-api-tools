"""Tests for VcoModel.merge_patch() — scalar and sub-model merging."""

from typing import Annotated

from pydantic import Field
from pydantic_core import MISSING
from pyvelo.core import Alias, VcoModel


class Inner(VcoModel):
    x: int = 0
    y: str = "default"


class Outer(VcoModel):
    name: str = ""
    value: int = 0
    inner: Inner = Field(default_factory=Inner)
    optional: str | MISSING = MISSING


class WithAlias(VcoModel):
    display_name: Annotated[str, Alias("displayName")] = ""
    count: int = 0


class TestScalarMerge:
    def test_merge_single_field(self):
        m = Outer(name="original", value=1)
        m.merge_patch({"name": "updated"})
        assert m.name == "updated"
        assert m.value == 1  # unchanged

    def test_merge_multiple_fields(self):
        m = Outer(name="a", value=1)
        m.merge_patch({"name": "b", "value": 2})
        assert m.name == "b"
        assert m.value == 2

    def test_absent_keys_unchanged(self):
        m = Outer(name="keep", value=42, optional="present")
        m.merge_patch({"value": 0})
        assert m.name == "keep"
        assert m.optional == "present"

    def test_null_sets_missing(self):
        m = Outer(optional="present")
        m.merge_patch({"optional": None})
        assert m.optional is MISSING

    def test_returns_self(self):
        m = Outer()
        result = m.merge_patch({"name": "x"})
        assert result is m

    def test_merge_with_snake_case_keys(self):
        m = WithAlias(display_name="old")
        m.merge_patch({"display_name": "new"})
        assert m.display_name == "new"

    def test_merge_with_camel_case_keys(self):
        m = WithAlias(display_name="old")
        m.merge_patch({"displayName": "new"})
        assert m.display_name == "new"


class TestSubModelMerge:
    def test_merge_into_missing_submodel_preserves_defaults(self):
        class OptionalOuter(VcoModel):
            inner: Inner | MISSING = MISSING

        m = OptionalOuter()
        m.merge_patch({"inner": {"x": 99}})
        assert isinstance(m.inner, Inner)
        assert m.inner.x == 99
        assert m.inner.y == "default"

    def test_dict_merges_into_sub_model(self):
        m = Outer(inner=Inner(x=1, y="keep"))
        m.merge_patch({"inner": {"x": 99}})
        assert m.inner.x == 99
        assert m.inner.y == "keep"  # unchanged

    def test_nested_merge_preserves_identity(self):
        m = Outer()
        original_inner = m.inner
        m.merge_patch({"inner": {"x": 5}})
        assert m.inner is original_inner  # same object, mutated in-place

    def test_deeply_nested_merge(self):
        class Deep(VcoModel):
            level: int = 0

        class Mid(VcoModel):
            deep: Deep = Field(default_factory=Deep)
            tag: str = ""

        class Top(VcoModel):
            mid: Mid = Field(default_factory=Mid)

        m = Top(mid=Mid(deep=Deep(level=1), tag="keep"))
        m.merge_patch({"mid": {"deep": {"level": 2}}})
        assert m.mid.deep.level == 2
        assert m.mid.tag == "keep"


class TestMergeDirectivesStripped:
    def test_underscore_keys_stripped(self):
        m = Outer(name="test")
        m.merge_patch({"name": "updated", "_position": 0, "_custom": "ignored"})
        assert m.name == "updated"
        assert not hasattr(m, "_position")
