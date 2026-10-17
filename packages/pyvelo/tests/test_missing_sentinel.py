"""Tests for the MISSING sentinel behavior."""

import copy
import json

from pydantic_core import MISSING
from pyvelo.core import VcoModel


class SampleModel(VcoModel):
    name: str
    optional_field: str | None | MISSING = MISSING
    required_field: str = "default"


class TestMissingSentinel:
    def test_missing_excluded_from_serialization(self):
        """MISSING fields should not appear in serialized output."""
        obj = SampleModel(name="test")
        dumped = obj.model_dump(by_alias=True, exclude_unset=True)
        assert "optionalField" not in dumped
        assert "name" in dumped

    def test_missing_excluded_from_json_serialization(self):
        """MISSING fields should not appear in JSON output."""
        obj = SampleModel(name="test")
        raw = obj.model_dump_json(by_alias=True, exclude_unset=True)
        parsed = json.loads(raw)
        assert "optionalField" not in parsed

    def test_none_preserved_in_serialization(self):
        """Explicit None should be preserved (distinct from MISSING)."""
        obj = SampleModel(name="test", optional_field=None)
        dumped = obj.model_dump(by_alias=True, exclude_unset=True)
        assert "optionalField" in dumped
        assert dumped["optionalField"] is None

    def test_missing_vs_none_distinction(self):
        """MISSING and None are semantically different."""
        missing_obj = SampleModel(name="test")
        none_obj = SampleModel(name="test", optional_field=None)

        assert missing_obj.optional_field is MISSING
        assert none_obj.optional_field is None
        assert missing_obj.optional_field is not none_obj.optional_field

    def test_missing_identity_check(self):
        """MISSING should be checked with `is`, not ==."""
        obj = SampleModel(name="test")
        assert obj.optional_field is MISSING

    def test_deepcopy_preserves_missing(self):
        """Deep copy should preserve MISSING sentinel."""
        obj = SampleModel(name="test")
        copied = copy.deepcopy(obj)
        assert copied.optional_field is MISSING

    def test_model_copy_preserves_missing(self):
        """Pydantic model_copy should preserve MISSING."""
        obj = SampleModel(name="test")
        copied = obj.model_copy()
        assert copied.optional_field is MISSING

    def test_validate_from_api_response_with_field_absent(self):
        """Validating from camelCase API data with field absent -> MISSING."""
        data = {"name": "test", "requiredField": "val"}
        obj = SampleModel.model_validate(data)
        assert obj.optional_field is MISSING

    def test_validate_from_api_response_with_field_null(self):
        """Validating from camelCase API data with field null -> None."""
        data = {"name": "test", "requiredField": "val", "optionalField": None}
        obj = SampleModel.model_validate(data)
        assert obj.optional_field is None

    def test_validate_from_api_response_with_field_present(self):
        """Validating from camelCase API data with field present -> value."""
        data = {"name": "test", "requiredField": "val", "optionalField": "hello"}
        obj = SampleModel.model_validate(data)
        assert obj.optional_field == "hello"
