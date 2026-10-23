"""Tests for the Resolvable Annotated marker."""

import uuid
from typing import Annotated

import pytest
from pyvelo.core import VcoModel
from pyvelo.core.fields import Alias, Resolvable, get_resolvable
from pyvelo.core.identity import (
    ApplicationIdentity,
    SegmentIdentity,
)


class HasResolvable(VcoModel):
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0
    segment_logical_id: Annotated[
        uuid.UUID, Resolvable(SegmentIdentity, "logical_id")
    ] = uuid.UUID(int=0)
    app_id: Annotated[int, Resolvable(ApplicationIdentity, "id")] = -1


class TestResolvableMarker:
    def test_processed_marker_in_metadata(self):
        fi = HasResolvable.model_fields["segment_id"]
        processed = get_resolvable(fi)
        assert processed is not None
        assert processed.identity_type is SegmentIdentity
        assert processed.id_attr == "segment_id"

    def test_different_id_attr_for_same_identity(self):
        fi = HasResolvable.model_fields["segment_logical_id"]
        processed = get_resolvable(fi)
        assert processed is not None
        assert processed.id_attr == "logical_id"

    def test_different_identity_type(self):
        fi = HasResolvable.model_fields["app_id"]
        processed = get_resolvable(fi)
        assert processed is not None
        assert processed.identity_type is ApplicationIdentity
        assert processed.id_attr == "id"

    def test_invalid_id_attr_raises(self):
        with pytest.raises(TypeError, match="has no field"):

            class Bad(VcoModel):
                x: Annotated[int, Resolvable(SegmentIdentity, "nonexistent")] = 0

            _ = Bad

    def test_model_validates_normally(self):
        m = HasResolvable(segment_id=42)
        assert m.segment_id == 42


class TestAliasWithResolvableOnSubclass:
    """Regression: Alias must be applied even when a parent already processed Resolvable."""

    def test_subclass_alias_applied_alongside_inherited_resolvable(self):
        class Parent(VcoModel):
            app_id: Annotated[int, Resolvable(ApplicationIdentity, "id")] = -1

        class Child(Parent):
            app_id: Annotated[
                int, Alias("appid"), Resolvable(ApplicationIdentity, "id")
            ] = -1

        fi = Child.model_fields["app_id"]
        assert fi.validation_alias == "appid"
        assert fi.serialization_alias == "appid"
        processed = get_resolvable(fi)
        assert processed is not None

    def test_alias_roundtrip_on_subclass(self):
        class Parent(VcoModel):
            app_id: Annotated[int, Resolvable(ApplicationIdentity, "id")] = -1

        class Child(Parent):
            app_id: Annotated[
                int, Alias("appid"), Resolvable(ApplicationIdentity, "id")
            ] = -1

        obj = Child.model_validate({"appid": 42})
        assert obj.app_id == 42
        assert obj.model_dump(by_alias=True) == {"appid": 42}
