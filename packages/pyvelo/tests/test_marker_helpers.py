"""Tests for marker introspection helpers."""

from typing import Annotated

from pydantic import Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import (
    EdgeLocal,
    KeyField,
    OverrideFlag,
    Resolvable,
    get_key_field,
    get_resolvable,
    has_override_flag,
    is_edge_local,
)
from pyvelo.core.identity import SegmentIdentity


class Item(VcoModel):
    name: str


class TestModel(VcoModel):
    override: Annotated[bool, OverrideFlag] = False
    items: Annotated[list[Item], KeyField("name")] = Field(default_factory=list)
    cidr_ip: Annotated[str, EdgeLocal] = ""
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0
    plain: str = ""


class TestGetKeyField:
    def test_returns_key_path_for_keyfield(self):
        fi = TestModel.model_fields["items"]
        assert get_key_field(fi) == "name"

    def test_returns_none_for_non_keyfield(self):
        fi = TestModel.model_fields["plain"]
        assert get_key_field(fi) is None


class TestHasOverrideFlag:
    def test_true_for_override_field(self):
        fi = TestModel.model_fields["override"]
        assert has_override_flag(fi) is True

    def test_false_for_normal_field(self):
        fi = TestModel.model_fields["plain"]
        assert has_override_flag(fi) is False


class TestIsEdgeLocal:
    def test_true_for_edge_local_field(self):
        fi = TestModel.model_fields["cidr_ip"]
        assert is_edge_local(fi) is True

    def test_false_for_normal_field(self):
        fi = TestModel.model_fields["plain"]
        assert is_edge_local(fi) is False


class TestGetResolvable:
    def test_returns_info_for_resolvable_field(self):
        fi = TestModel.model_fields["segment_id"]
        r = get_resolvable(fi)
        assert r is not None
        assert r.identity_type is SegmentIdentity
        assert r.id_attr == "segment_id"

    def test_returns_none_for_non_resolvable(self):
        fi = TestModel.model_fields["plain"]
        assert get_resolvable(fi) is None
