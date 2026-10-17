"""Tests for the KeyField Annotated marker."""

from typing import Annotated

from pydantic import Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField, _ProcessedKeyField


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
    def test_processed_marker_in_metadata(self):
        fi = HasKeyField.model_fields["segments"]
        processed = [m for m in fi.metadata if isinstance(m, _ProcessedKeyField)]
        assert len(processed) == 1
        assert processed[0].key_path == "name"

    def test_nested_key_path(self):
        fi = HasDottedKeyField.model_fields["segments"]
        processed = [m for m in fi.metadata if isinstance(m, _ProcessedKeyField)]
        assert len(processed) == 1
        assert processed[0].key_path == "meta.name"

    def test_model_still_validates_list(self):
        m = HasKeyField(segments=[SegmentMeta(name="Global", segment_id=0)])
        assert len(m.segments) == 1
        assert m.segments[0].name == "Global"

    def test_model_validates_from_dict(self):
        m = HasKeyField.model_validate(
            {"segments": [{"name": "Global", "segmentId": 0}]}
        )
        assert m.segments[0].name == "Global"
