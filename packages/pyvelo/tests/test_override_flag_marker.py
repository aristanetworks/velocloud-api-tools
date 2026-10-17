"""Tests for the OverrideFlag Annotated marker."""

from typing import Annotated

from pyvelo.core import VcoModel
from pyvelo.core.fields import OverrideFlag, _ProcessedOverrideFlag


class HasOverride(VcoModel):
    override: Annotated[bool, OverrideFlag] = False
    name: str = ""
    value: int = 0


class NoOverride(VcoModel):
    name: str = ""


class TestOverrideFlagMarker:
    def test_processed_marker_in_metadata(self):
        fi = HasOverride.model_fields["override"]
        processed = [m for m in fi.metadata if isinstance(m, _ProcessedOverrideFlag)]
        assert len(processed) == 1

    def test_non_override_field_has_no_marker(self):
        fi = HasOverride.model_fields["name"]
        processed = [m for m in fi.metadata if isinstance(m, _ProcessedOverrideFlag)]
        assert len(processed) == 0

    def test_model_validates_normally(self):
        m = HasOverride(override=True, name="test", value=42)
        assert m.override is True

    def test_default_is_false(self):
        m = HasOverride()
        assert m.override is False
