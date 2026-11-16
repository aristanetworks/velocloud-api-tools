"""Tests for patch schema generation."""

from typing import Annotated, TypeGuard, cast

import pytest
from pydantic import MISSING, Field
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.core.base import VcoModel
from pyvelo.core.fields import KeyField, OverrideFlag, Resolvable
from pyvelo.core.identity import SegmentIdentity
from pyvelo_intent.config.aspect import Aspect, AspectRegistry
from pyvelo_intent.config.schema import generate_patch_schema, resolve_model_at_path


class InnerItem(VcoModel):
    name: str
    value: int = 0


class SubModel(VcoModel):
    override: Annotated[bool, OverrideFlag] = False
    items: Annotated[list[InnerItem], KeyField("name")] = Field(default_factory=list)
    label: str = ""


class RootModel(VcoModel):
    sub: SubModel = Field(default_factory=SubModel)
    count: int = 0
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0


class MissingUnionRoot(VcoModel):
    sub: SubModel | MISSING = MISSING


class NullableUnionRoot(VcoModel):
    sub: SubModel | None = None


class OptionalListRoot(VcoModel):
    sub: list[SubModel] | MISSING = MISSING


def _is_schema_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(
        isinstance(key, str) for key in cast(dict[object, object], value)
    )


def _is_schema_dict_list(value: object) -> TypeGuard[list[dict[str, object]]]:
    return isinstance(value, list) and all(
        _is_schema_dict(item) for item in cast(list[object], value)
    )


class TestResolveModelAtPath:
    @pytest.mark.parametrize(
        "model",
        [MissingUnionRoot, NullableUnionRoot, OptionalListRoot],
        ids=["missing", "nullable", "optional-list"],
    )
    def test_union_submodel_path(self, model: type[VcoModel]) -> None:
        assert resolve_model_at_path(model, "sub") is SubModel
        assert generate_patch_schema(model, "sub")["title"] == "SubModel"

    def test_none_path(self):
        assert resolve_model_at_path(RootModel, None) is RootModel

    def test_empty_path(self):
        assert resolve_model_at_path(RootModel, "") is RootModel

    def test_single_level(self):
        result = resolve_model_at_path(RootModel, "sub")
        assert result is SubModel

    def test_unknown_field(self):
        with pytest.raises(ValueError, match="has no field"):
            _ = resolve_model_at_path(RootModel, "nonexistent")


class TestGeneratePatchSchema:
    def test_all_properties_optional(self):
        schema = generate_patch_schema(RootModel)
        assert "required" not in schema

    def test_nested_defs_optional(self):
        schema = generate_patch_schema(RootModel)
        for defn in schema.get("$defs", {}).values():
            assert _is_schema_dict(defn)
            assert "required" not in defn

    def test_override_fields_removed(self):
        schema = generate_patch_schema(RootModel, "sub")
        props = schema.get("properties", {})
        assert _is_schema_dict(props)
        assert "override" not in props

    def test_keyfield_array_to_object(self):
        schema = generate_patch_schema(RootModel, "sub")
        props = schema.get("properties", {})
        assert _is_schema_dict(props)
        items_prop = props.get("items")
        assert _is_schema_dict(items_prop)
        assert items_prop is not None
        assert items_prop["type"] == "object"
        assert "additionalProperties" in items_prop

    def test_resolvable_accepts_string(self):
        schema = generate_patch_schema(RootModel)
        props = schema.get("properties", {})
        assert _is_schema_dict(props)
        seg_prop = props.get("segmentId")
        assert _is_schema_dict(seg_prop)
        assert seg_prop is not None
        # Should have anyOf with string option
        any_of = seg_prop.get("anyOf")
        assert _is_schema_dict_list(any_of)
        types_in_any_of = [item.get("type") for item in any_of]
        assert "string" in types_in_any_of

    def test_path_navigation(self):
        schema = generate_patch_schema(RootModel, "sub")
        assert schema["title"] == "SubModel"

    def test_real_firewall_data(self):
        from pyvelo.client.models.configuration.firewall.root import FirewallData

        schema = generate_patch_schema(FirewallData)
        assert "required" not in schema
        props = schema.get("properties", {})
        assert _is_schema_dict(props)
        # segments should be transformed from array to object
        seg_prop = props.get("segments")
        assert _is_schema_dict(seg_prop)
        assert seg_prop is not None
        assert seg_prop["type"] == "object"
        assert "additionalProperties" in seg_prop

    def test_real_ntp_model(self):
        from pyvelo.client.models.configuration.device_settings.root import Ntp

        schema = generate_patch_schema(Ntp)
        props = schema.get("properties", {})
        assert _is_schema_dict(props)
        # Override should be removed
        assert "override" not in props
        # servers (KeyField) should be object
        servers_prop = props.get("servers")
        assert _is_schema_dict(servers_prop)
        assert servers_prop is not None
        assert servers_prop["type"] == "object"


class TestAspectRegistrySchemas:
    def _make_registry(
        self,
    ) -> tuple[AspectRegistry, dict[ConfigurationModuleName, type[RootModel]]]:
        reg = AspectRegistry()
        reg.register(
            Aspect(
                name="sub",
                module=ConfigurationModuleName.META_DATA,
                path="sub",
                description="Sub model",
                tags=["sub"],
            )
        )
        models = {ConfigurationModuleName.META_DATA: RootModel}
        return reg, models

    def test_get_patch_schema(self):
        reg, models = self._make_registry()
        schema = reg.get_patch_schema("sub", module_models=models)
        assert schema["title"] == "SubModel"
        assert "required" not in schema

    def test_get_read_schema(self):
        reg, models = self._make_registry()
        schema = reg.get_read_schema("sub", module_models=models)
        assert schema["title"] == "SubModel"

    def test_unknown_module(self):
        reg = AspectRegistry()
        reg.register(
            Aspect(
                name="x",
                module=ConfigurationModuleName.ATP_METADATA,
                path="x",
                description="X",
                tags=[],
            )
        )
        with pytest.raises(ValueError, match="No model registered"):
            _ = reg.get_patch_schema("x", module_models={})
