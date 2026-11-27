"""Tests for SDK-side convenience re-exports."""

import pyvelo
import pyvelo.client
import pyvelo.config
import pyvelo.core
from pyvelo.client import exceptions
from pyvelo.core import base, fields, mending, merge, patch, rebase


class TestValidationReexports:
    def test_imports_from_config(self) -> None:
        from pyvelo.config import (  # noqa: PLC0415 (the import is what this test checks)
            ValidationResult,
            ValidatorRegistry,
            validator,
        )

        assert ValidationResult is not None
        assert ValidatorRegistry is not None
        assert validator is not None


class TestSaveErrorReexports:
    def test_save_errors_are_importable_from_client_and_top_level(self) -> None:
        for name in (
            "ComposedModuleError",
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
    def test_the_names_a_tool_over_patches_uses_come_from_pyvelo_core(self) -> None:
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

    def test_the_kind_of_a_patch_error_comes_from_pyvelo_core(self) -> None:
        assert pyvelo.core.PatchErrorKind is patch.PatchErrorKind
        assert "PatchErrorKind" in pyvelo.core.__all__


class TestMendingReexports:
    def test_the_mending_names_come_from_pyvelo_core_and_the_type_from_pyvelo_config(
        self,
    ) -> None:
        for name in ("MendingKind", "MendingPatch", "backhaul_hub_mending"):
            assert getattr(pyvelo.core, name) is getattr(mending, name), name
            assert name in pyvelo.core.__all__, name
        assert pyvelo.config.MendingPatch is mending.MendingPatch
        assert "MendingPatch" in pyvelo.config.__all__
