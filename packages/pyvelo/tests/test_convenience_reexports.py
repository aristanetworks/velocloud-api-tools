"""Tests for SDK-side convenience re-exports."""


class TestValidationReexports:
    def test_imports_from_config(self):
        from pyvelo.config import (
            ValidationResult,
            ValidatorRegistry,
            validator,
        )

        assert ValidationResult is not None
        assert ValidatorRegistry is not None
        assert validator is not None
