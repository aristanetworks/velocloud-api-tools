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

        for name in (
            "ConfigConflictError",
            "DanglingReferenceError",
            "FixedListError",
            "GroupOwnershipError",
            "HubListError",
            "LayerUnknownError",
            "LossyFieldError",
            "ModuleWriteError",
            "RefsDroppedError",
            "RefsNotLoadedError",
            "SegmentSetError",
            "UnboundError",
            "WriteNotAppliedError",
            "WriteOutcomeUnknownError",
        ):
            error = getattr(exceptions, name)
            assert getattr(pyvelo.client, name) is error
            assert getattr(pyvelo, name) is error
            assert name in pyvelo.client.__all__
            assert name in pyvelo.__all__


class TestPatchToolReexports:
    def test_the_names_a_tool_over_patches_uses_come_from_pyvelo_core(self):
        import pyvelo.core
        from pyvelo.core import base, fields, merge, patch, rebase

        homes = {
            "list_kind": base,
            "ListKind": base,
            "select_items": merge,
            "nest_patch": merge,
            "position_of": patch,
            "Path": rebase,
            "Key": rebase,
            "key_text": rebase,
            "item_key_text": rebase,
            "is_positional": fields,
            "get_name_of": fields,
        }
        for name, home in homes.items():
            assert getattr(pyvelo.core, name) is getattr(home, name), name
            assert name in pyvelo.core.__all__, name

    def test_the_kind_of_a_patch_error_comes_from_pyvelo_core(self):
        import pyvelo.core
        from pyvelo.core import patch

        assert pyvelo.core.PatchErrorKind is patch.PatchErrorKind
        assert "PatchErrorKind" in pyvelo.core.__all__
