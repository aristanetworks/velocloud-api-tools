"""Tests for CoerceInt validator."""

from typing import Annotated

import pytest
from pydantic import MISSING, ValidationError
from pyvelo.core import CoerceInt, VcoModel


class _Sample(VcoModel):
    count: Annotated[int | None | MISSING, CoerceInt] = MISSING


class TestCoerceInt:
    def test_int_preserved(self):
        assert _Sample(count=42).count == 42

    def test_quoted_int_coerced(self):
        assert _Sample.model_validate({"count": "42"}).count == 42

    def test_zero_coerced(self):
        assert _Sample.model_validate({"count": "0"}).count == 0

    def test_negative_coerced(self):
        assert _Sample.model_validate({"count": "-7"}).count == -7

    def test_none_preserved(self):
        assert _Sample.model_validate({"count": None}).count is None

    def test_missing_preserved(self):
        assert _Sample().count is MISSING

    def test_non_numeric_string_raises(self):
        # Pass-through lets pydantic surface a clean int-parse error.
        with pytest.raises(ValidationError):
            _ = _Sample.model_validate({"count": "not a number"})

    def test_whitespace_only_string_falls_through(self):
        # Whitespace-only strings are not coerced (defensive: never int(''),
        # never int('  ')); pydantic then rejects them.
        with pytest.raises(ValidationError):
            _ = _Sample.model_validate({"count": "   "})
