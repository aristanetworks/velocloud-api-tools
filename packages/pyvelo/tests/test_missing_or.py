"""Tests for missing_or utility function."""

from pydantic import MISSING
from pyvelo.core import VcoModel, missing_or


class TestMissingOr:
    def test_returns_value_when_present(self):
        assert missing_or(42, 0) == 42

    def test_returns_default_when_missing(self):
        assert missing_or(MISSING, 0) == 0

    def test_none_is_not_missing(self):
        assert missing_or(None, "fallback") is None

    def test_empty_string_is_not_missing(self):
        assert missing_or("", "fallback") == ""

    def test_false_is_not_missing(self):
        assert missing_or(False, True) is False

    def test_zero_is_not_missing(self):
        assert missing_or(0, 99) == 0

    def test_default_can_be_any_type(self):
        assert missing_or(MISSING, [1, 2, 3]) == [1, 2, 3]
        assert missing_or(MISSING, {"key": "val"}) == {"key": "val"}
        assert missing_or(MISSING, None) is None

    def test_with_vco_model_field(self):
        class Sample(VcoModel):
            val: int | MISSING = MISSING

        obj = Sample()
        assert missing_or(obj.val, 10) == 10

        obj2 = Sample(val=5)
        assert missing_or(obj2.val, 10) == 5
