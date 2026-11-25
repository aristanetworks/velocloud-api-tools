"""Tests for the KeyField Annotated marker."""

from typing import Annotated

from pydantic import Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField, get_key_spec


class SegmentMeta(VcoModel):
    name: str
    segment_id: int = 0


class Rule(VcoModel):
    name: str
    enabled: bool = True


class HasKeyField(VcoModel):
    segments: Annotated[list[SegmentMeta], KeyField("name")] = Field(
        default_factory=list
    )


class HasSegmentMeta(VcoModel):
    meta: SegmentMeta
    value: int = 0


class HasNestedKeyField(VcoModel):
    items: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)


class HasDottedKeyField(VcoModel):
    segments: Annotated[list[HasSegmentMeta], KeyField("meta.name")] = Field(
        default_factory=list
    )


class TestKeyFieldMarker:
    def test_processed_marker_in_metadata(self) -> None:
        fi = HasKeyField.model_fields["segments"]
        spec = get_key_spec(fi)
        assert spec is not None
        assert spec.paths == ("name",)

    def test_nested_key_path(self) -> None:
        fi = HasDottedKeyField.model_fields["segments"]
        spec = get_key_spec(fi)
        assert spec is not None
        assert spec.paths == ("meta.name",)

    def test_model_still_validates_list(self) -> None:
        m = HasKeyField(segments=[SegmentMeta(name="Global", segment_id=0)])
        assert len(m.segments) == 1
        assert m.segments[0].name == "Global"

    def test_model_validates_from_dict(self) -> None:
        m = HasKeyField.model_validate(
            {"segments": [{"name": "Global", "segmentId": 0}]}
        )
        assert m.segments[0].name == "Global"
