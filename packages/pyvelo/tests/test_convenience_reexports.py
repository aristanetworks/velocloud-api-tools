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


class TestSaveErrorReexports:
    def test_save_errors_are_importable_from_client_and_top_level(self):
        import pyvelo
        import pyvelo.client
        from pyvelo.client import exceptions

        for name in ("ConfigConflictError", "LossyFieldError", "UnboundError"):
            error = getattr(exceptions, name)
            assert getattr(pyvelo.client, name) is error
            assert getattr(pyvelo, name) is error
            assert name in pyvelo.client.__all__
            assert name in pyvelo.__all__
