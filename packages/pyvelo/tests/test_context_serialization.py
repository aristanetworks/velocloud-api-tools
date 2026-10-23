"""Tests for context-aware serialization and ContextView introspection."""

from typing import Annotated, Any, cast

from pydantic import MISSING, ConfigDict, Field
from pyvelo.core import (
    Alias,
    ConfigContext,
    ContextDefault,
    EdgeOnly,
    ProfileOnly,
    VcoModel,
)
from pyvelo.core.fields import _get_context_defaults

# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class Inner(VcoModel):
    x: int = 0
    y: str = "kept"


class EdgeProfileModel(VcoModel):
    name: str = "default_name"
    override: Annotated[bool | MISSING, EdgeOnly] = True
    enabled: bool = False
    tags: list[str] = Field(default_factory=list)
    inner: Inner = Field(default_factory=Inner)


class ProfileOnlyFields(VcoModel):
    rules: list[str] = Field(default_factory=list)
    defaults: Annotated[list[str] | MISSING, ProfileOnly] = MISSING


class ContextSpecificDefault(VcoModel):
    """Model where a field has different defaults per context."""

    routed_interfaces: Annotated[list[str] | None, ContextDefault(profile=None)] = (
        Field(default_factory=list)
    )


class BothMetadata(VcoModel):
    """Model with context_defaults where default handles omission."""

    port: Annotated[int, EdgeOnly] = -1


class AliasedContextModel(VcoModel):
    source_ip: Annotated[str, Alias("sip"), ContextDefault(profile=MISSING)] = "any"
    label: str = "default"


class NestedOuter(VcoModel):
    child: EdgeProfileModel = Field(default_factory=EdgeProfileModel)
    extra: str = ""


class FlexModel(VcoModel):
    model_config = ConfigDict(**cast(Any, {**VcoModel.model_config, "extra": "allow"}))
    name: str = "default"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compact(model, **extra_ctx):
    ctx = {"compact": True, **extra_ctx}
    return model.model_dump(by_alias=False, context=ctx)


def _compact_alias(model, **extra_ctx):
    ctx = {"compact": True, **extra_ctx}
    return model.model_dump(by_alias=True, context=ctx)


# ---------------------------------------------------------------------------
# Parity: model_dump(compact) matches old compact_dump behavior
# ---------------------------------------------------------------------------


class TestParity:
    def test_basic_compact_matches(self):
        m = EdgeProfileModel(name="test", override=True, enabled=True)
        d = _compact(m)
        assert d == {"name": "test", "enabled": True}

    def test_compact_with_missing(self):
        m = ProfileOnlyFields()
        d = _compact(m)
        assert d == {}

    def test_compact_with_values(self):
        m = ProfileOnlyFields(rules=["r1"], defaults=["d1"])
        d = _compact(m)
        assert d == {"rules": ["r1"], "defaults": ["d1"]}


# ---------------------------------------------------------------------------
# Alias control
# ---------------------------------------------------------------------------


class TestAliasControl:
    def test_by_alias_false_uses_python_names(self):
        m = AliasedContextModel(source_ip="10.0.0.1")
        d = _compact(m)
        assert "source_ip" in d
        assert "sip" not in d

    def test_by_alias_true_uses_camel_case(self):
        m = AliasedContextModel(source_ip="10.0.0.1")
        d = _compact_alias(m)
        assert "sip" in d
        assert "source_ip" not in d

    def test_non_aliased_fields_get_camel_case(self):
        m = EdgeProfileModel(name="test", enabled=True)
        d = _compact_alias(m)
        assert "enabled" in d  # "enabled" -> "enabled" (no change for single word)
        assert "name" in d


# ---------------------------------------------------------------------------
# No context: standard Pydantic output
# ---------------------------------------------------------------------------


class TestNoContext:
    def test_model_dump_without_context_is_standard(self):
        m = EdgeProfileModel(name="test", override=True, enabled=False)
        d = m.model_dump()
        # Standard Pydantic dump includes all fields with aliases
        assert "override" in d or "Override" in d  # by_alias=True by default
        assert "name" in d or "Name" in d

    def test_model_dump_includes_missing_as_excluded(self):
        """Standard model_dump with exclude_unset handles MISSING fields."""
        m = EdgeProfileModel()
        d = m.model_dump(exclude_unset=True)
        # This should still work via Pydantic's handler
        assert isinstance(d, dict)


# ---------------------------------------------------------------------------
# ContextDefaults storage
# ---------------------------------------------------------------------------


class TestContextDefaultsStorage:
    def test_edge_only_stores_context_defaults(self):
        fi = EdgeProfileModel.model_fields["override"]
        cd = _get_context_defaults(fi)
        assert cd is not None
        assert cd.profile is MISSING

    def test_alias_ctx_default_stores_context_defaults(self):
        fi = AliasedContextModel.model_fields["source_ip"]
        cd = _get_context_defaults(fi)
        assert cd is not None

    def test_context_defaults_stored(self):
        fi = BothMetadata.model_fields["port"]
        cd = _get_context_defaults(fi)
        assert cd is not None
        assert cd.profile is MISSING


# ---------------------------------------------------------------------------
# Context omission: edge-only fields omitted at profile level
# ---------------------------------------------------------------------------


class TestContextOmission:
    def test_edge_only_field_omitted_at_profile(self):
        m = EdgeProfileModel(override=True, enabled=True)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "override" not in d
        assert "enabled" in d

    def test_edge_only_field_kept_at_edge(self):
        m = EdgeProfileModel(override=False, enabled=True)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "override" in d
        assert d["override"] is False

    def test_edge_only_field_at_default_dropped_at_edge(self):
        """At edge level, override=True matches edge default -> omitted."""
        m = EdgeProfileModel(override=True)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "override" not in d

    def test_profile_only_field_omitted_at_edge(self):
        m = ProfileOnlyFields(defaults=["rule1"])
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "defaults" not in d

    def test_profile_only_field_kept_at_profile(self):
        m = ProfileOnlyFields(defaults=["rule1"])
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert d["defaults"] == ["rule1"]


# ---------------------------------------------------------------------------
# Context-specific defaults
# ---------------------------------------------------------------------------


class TestContextSpecificDefaults:
    def test_profile_context_uses_none_default(self):
        """At profile level, routed_interfaces default is None, so None is omitted."""
        m = ContextSpecificDefault(routed_interfaces=None)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "routed_interfaces" not in d

    def test_profile_context_keeps_non_default(self):
        m = ContextSpecificDefault(routed_interfaces=["GE1"])
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert d["routed_interfaces"] == ["GE1"]

    def test_edge_context_uses_field_default(self):
        """At edge level, no context default -> falls back to field default (list)."""
        m = ContextSpecificDefault(routed_interfaces=[])
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "routed_interfaces" not in d  # empty list == field default

    def test_edge_context_keeps_populated_list(self):
        m = ContextSpecificDefault(routed_interfaces=["GE1"])
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["routed_interfaces"] == ["GE1"]


# ---------------------------------------------------------------------------
# Nested propagation
# ---------------------------------------------------------------------------


class TestNestedPropagation:
    def test_context_flows_to_nested_model(self):
        child = EdgeProfileModel(override=True, enabled=True)
        m = NestedOuter(child=child, extra="hello")
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "override" not in d["child"]
        assert d["child"]["enabled"] is True
        assert d["extra"] == "hello"

    def test_context_flows_to_nested_at_edge(self):
        child = EdgeProfileModel(override=False, enabled=True)
        m = NestedOuter(child=child, extra="hello")
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["child"]["override"] is False


# ---------------------------------------------------------------------------
# model_extra preservation
# ---------------------------------------------------------------------------


class TestModelExtra:
    def test_extra_keys_preserved(self):
        m = FlexModel.model_validate({"name": "test", "unknownKey": 42})
        d = _compact(m)
        assert d["unknownKey"] == 42

    def test_extra_keys_preserved_with_context(self):
        m = FlexModel.model_validate({"name": "test", "unknownKey": 42})
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["unknownKey"] == 42


# ---------------------------------------------------------------------------
# ContextView
# ---------------------------------------------------------------------------


class TestContextView:
    def test_edge_field_present_at_edge(self):
        view = EdgeProfileModel.context(ConfigContext.EDGE)
        info = view.override
        assert info.is_present is True
        assert info.default is True

    def test_edge_field_absent_at_profile(self):
        view = EdgeProfileModel.context(ConfigContext.PROFILE)
        info = view.override
        assert info.is_present is False
        assert info.default is MISSING

    def test_regular_field_present_at_both(self):
        edge = EdgeProfileModel.context(ConfigContext.EDGE)
        profile = EdgeProfileModel.context(ConfigContext.PROFILE)
        assert edge.enabled.is_present is True
        assert profile.enabled.is_present is True

    def test_field_info_attached(self):
        view = EdgeProfileModel.context(ConfigContext.EDGE)
        assert view.override.field_info is EdgeProfileModel.model_fields["override"]


class TestContextViewIteration:
    def test_yields_all_fields(self):
        view = EdgeProfileModel.context(ConfigContext.EDGE)
        fields = dict(view)
        assert "override" in fields
        assert "enabled" in fields
        assert "name" in fields

    def test_profile_view_marks_override_absent(self):
        view = EdgeProfileModel.context(ConfigContext.PROFILE)
        present = {n for n, info in view if info.is_present}
        absent = {n for n, info in view if not info.is_present}
        assert "override" in absent
        assert "name" in present
        assert "enabled" in present


class TestContextViewErrors:
    def test_nonexistent_field_raises(self):
        view = EdgeProfileModel.context(ConfigContext.EDGE)
        try:
            _ = view.nonexistent_field
            raise AssertionError("Should have raised AttributeError")
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Round-trip with context
# ---------------------------------------------------------------------------


class TestContextRoundTrip:
    def test_compact_with_context_round_trips(self):
        original = EdgeProfileModel(
            name="my_edge", override=True, enabled=True, tags=["prod"]
        )
        compacted = _compact(original, config_level=ConfigContext.EDGE)
        restored = EdgeProfileModel.model_validate(compacted)
        assert restored.name == "my_edge"
        assert restored.enabled is True
        assert restored.tags == ["prod"]
        # override was at default, so omitted and restored to default
        assert restored.override is True

    def test_profile_round_trip_omits_edge_only(self):
        original = EdgeProfileModel(name="my_profile", override=True, enabled=True)
        compacted = _compact(original, config_level=ConfigContext.PROFILE)
        assert "override" not in compacted
        restored = EdgeProfileModel.model_validate(compacted)
        assert restored.name == "my_profile"
        assert restored.enabled is True


# ---------------------------------------------------------------------------
# Default + context interaction
# ---------------------------------------------------------------------------


class TestDefaultAndContext:
    def test_default_value_omitted_at_edge(self):
        """port=-1 matches field default, omitted at edge level."""
        m = BothMetadata(port=-1)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "port" not in d

    def test_context_absent_overrides_value(self):
        """If context says field absent, omit even if value is meaningful."""
        m = BothMetadata(port=8080)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "port" not in d

    def test_non_default_value_kept_at_edge(self):
        m = BothMetadata(port=8080)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["port"] == 8080


# ---------------------------------------------------------------------------
# for_context: instance-level config context
# ---------------------------------------------------------------------------


class TestForContext:
    def test_for_context_sets_instance_context(self):
        m = EdgeProfileModel(name="test", override=True, enabled=True)
        assert m._config_context is None
        m.for_edge()
        assert m._config_context is ConfigContext.EDGE

    def test_for_context_returns_self(self):
        m = EdgeProfileModel(name="test")
        result = m.for_edge()
        assert result is m

    def test_compact_uses_instance_context(self):
        """Compact serialization reads _config_context when no explicit config_level."""
        m = EdgeProfileModel(name="test", override=True, enabled=True)
        m.for_edge()
        d = _compact(m)
        # override=True is the edge default, so it should be omitted
        assert d == {"name": "test", "enabled": True}

    def test_compact_uses_instance_context_profile(self):
        """Profile context omits edge-only fields."""
        m = EdgeProfileModel(name="test", override=True, enabled=True)
        m.for_profile()
        d = _compact(m)
        # override has context_defaults=ContextDefaults(profile=MISSING) -> omitted
        assert "override" not in d
        assert d == {"name": "test", "enabled": True}

    def test_explicit_config_level_overrides_instance(self):
        """Explicit config_level in context dict takes precedence."""
        m = EdgeProfileModel(name="test", override=True, enabled=True)
        m.for_edge()
        # Explicitly pass profile context -> should override instance
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "override" not in d

    def test_for_context_propagates_to_nested(self):
        """Context propagates to nested VcoModel instances."""
        outer = NestedOuter(
            child=EdgeProfileModel(name="inner", override=True, enabled=True),
            extra="val",
        )
        outer.for_profile()
        assert outer._config_context is ConfigContext.PROFILE
        assert outer.child._config_context is ConfigContext.PROFILE

    def test_nested_compact_uses_propagated_context(self):
        """Nested models use propagated context during compact serialization."""
        outer = NestedOuter(
            child=EdgeProfileModel(name="inner", override=True, enabled=True),
            extra="val",
        )
        outer.for_profile()
        d = _compact(outer)
        # override should be omitted in nested child (profile=MISSING)
        assert "override" not in d["child"]

    def test_for_context_propagates_through_list(self):
        """Context propagates through list fields containing VcoModels."""

        class ListContainer(VcoModel):
            items: list[EdgeProfileModel] = Field(default_factory=list)

        container = ListContainer(
            items=[
                EdgeProfileModel(name="a", override=True),
                EdgeProfileModel(name="b", override=False),
            ]
        )
        container.for_profile()
        assert container.items[0]._config_context is ConfigContext.PROFILE
        assert container.items[1]._config_context is ConfigContext.PROFILE

    def test_chaining_for_context(self):
        """for_context can be chained with construction."""
        m = EdgeProfileModel(name="test", override=True, enabled=True).for_edge()
        d = _compact(m)
        assert d == {"name": "test", "enabled": True}

    def test_isolated_nested_model_with_context(self):
        """Extracted nested model retains context for independent serialization."""
        outer = NestedOuter(
            child=EdgeProfileModel(name="inner", override=True, enabled=True),
        )
        outer.for_profile()
        # Extract the child and serialize independently
        child = outer.child
        d = _compact(child)
        assert "override" not in d
        assert d == {"name": "inner", "enabled": True}

    def test_standalone_model_with_for_context(self):
        """Standalone model construction with for_context (isolated part use case)."""
        data = ContextSpecificDefault(routed_interfaces=["eth0"]).for_edge()
        d = _compact(data)
        assert d == {"routed_interfaces": ["eth0"]}

    def test_standalone_model_empty_list_at_edge(self):
        """Empty list matches edge default (list factory) -> omitted."""
        data = ContextSpecificDefault(routed_interfaces=[]).for_edge()
        d = _compact(data)
        assert "routed_interfaces" not in d

    def test_standalone_model_none_at_profile(self):
        """None matches profile default -> omitted."""
        data = ContextSpecificDefault(routed_interfaces=None).for_profile()
        d = _compact(data)
        assert "routed_interfaces" not in d


# ---------------------------------------------------------------------------
# model_validate with context: automatic context propagation during validation
# ---------------------------------------------------------------------------


class TestModelValidateContext:
    def test_model_validate_sets_context(self):
        """model_validate with config_level sets _config_context on the root."""
        m = EdgeProfileModel.model_validate(
            {"name": "test", "override": True},
            context={"config_level": ConfigContext.EDGE},
        )
        assert m._config_context is ConfigContext.EDGE

    def test_model_validate_propagates_to_nested(self):
        """Validation context propagates to nested VcoModel instances."""
        m = NestedOuter.model_validate(
            {"child": {"name": "inner", "override": True}, "extra": "val"},
            context={"config_level": ConfigContext.PROFILE},
        )
        assert m._config_context is ConfigContext.PROFILE
        assert m.child._config_context is ConfigContext.PROFILE

    def test_model_validate_compact_without_explicit_level(self):
        """After model_validate with context, compact serialization uses it."""
        m = EdgeProfileModel.model_validate(
            {"name": "test", "override": True, "enabled": True},
            context={"config_level": ConfigContext.PROFILE},
        )
        d = _compact(m)
        # override has context_defaults(profile=MISSING) -> omitted
        assert "override" not in d
        assert d == {"name": "test", "enabled": True}

    def test_model_validate_nested_compact(self):
        """Nested model extracted after context validation serializes correctly."""
        m = NestedOuter.model_validate(
            {"child": {"name": "inner", "override": True, "enabled": True}},
            context={"config_level": ConfigContext.PROFILE},
        )
        d = _compact(m.child)
        assert "override" not in d

    def test_model_validate_without_context_leaves_none(self):
        """model_validate without context leaves _config_context as None."""
        m = EdgeProfileModel.model_validate({"name": "test"})
        assert m._config_context is None

    def test_model_validate_json_sets_context(self):
        """model_validate_json also propagates context."""
        m = EdgeProfileModel.model_validate_json(
            '{"name": "test", "override": true}',
            context={"config_level": ConfigContext.EDGE},
        )
        assert m._config_context is ConfigContext.EDGE


# ---------------------------------------------------------------------------
# Union-typed field with per-context defaults (bool | list | None)
# ---------------------------------------------------------------------------


class UnionContextModel(VcoModel):
    """Field whose type and default differ between edge and profile contexts."""

    problematic_field: Annotated[
        bool | list[int] | None, ContextDefault(profile=False, edge=list)
    ]


class TestUnionContextDefaults:
    """Construction and compact serialization of a union-typed context field."""

    # -- Profile context -----------------------------------------------------

    def test_profile_false_matches_default_omitted(self):
        """False matches profile default (False) -> omitted."""
        m = UnionContextModel(problematic_field=False)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "problematic_field" not in d

    def test_profile_true_kept(self):
        m = UnionContextModel(problematic_field=True)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert d["problematic_field"] is True

    def test_profile_empty_list_kept(self):
        """Empty list does NOT match profile default (False) -> kept."""
        m = UnionContextModel(problematic_field=[])
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert d["problematic_field"] == []

    def test_profile_populated_list_kept(self):
        m = UnionContextModel(problematic_field=[1, 2, 3])
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert d["problematic_field"] == [1, 2, 3]

    def test_profile_none_kept(self):
        m = UnionContextModel(problematic_field=None)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert d["problematic_field"] is None

    # -- Edge context --------------------------------------------------------

    def test_edge_empty_list_matches_default_omitted(self):
        """Empty list matches edge default (list factory -> []) -> omitted."""
        m = UnionContextModel(problematic_field=[])
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "problematic_field" not in d

    def test_edge_populated_list_kept(self):
        m = UnionContextModel(problematic_field=[10, 20])
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["problematic_field"] == [10, 20]

    def test_edge_false_kept(self):
        """False does NOT match edge default ([]) -> kept."""
        m = UnionContextModel(problematic_field=False)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["problematic_field"] is False

    def test_edge_true_kept(self):
        m = UnionContextModel(problematic_field=True)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["problematic_field"] is True

    def test_edge_none_kept(self):
        m = UnionContextModel(problematic_field=None)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["problematic_field"] is None

    # -- for_context construction patterns -----------------------------------

    def test_for_context_edge_chained(self):
        m = UnionContextModel(problematic_field=[]).for_edge()
        d = _compact(m)
        assert "problematic_field" not in d

    def test_for_context_profile_chained(self):
        m = UnionContextModel(problematic_field=False).for_profile()
        d = _compact(m)
        assert "problematic_field" not in d

    def test_for_context_edge_non_default_kept(self):
        m = UnionContextModel(problematic_field=False).for_edge()
        d = _compact(m)
        assert d["problematic_field"] is False

    def test_for_context_profile_non_default_kept(self):
        m = UnionContextModel(problematic_field=[42]).for_profile()
        d = _compact(m)
        assert d["problematic_field"] == [42]


# ---------------------------------------------------------------------------
# VCO-format serialization (by_alias=True, defaults preserved)
# ---------------------------------------------------------------------------


class VcoFormatModel(VcoModel):
    """Model with edge-only, profile-only, shared, and aliased fields."""

    # Edge-only: omitted at profile level
    edge_override: Annotated[bool, EdgeOnly] = True
    # Profile-only: omitted at edge level
    profile_baseline: Annotated[str | MISSING, ProfileOnly] = MISSING
    # Present in both, different defaults per context
    log_level: Annotated[int, ContextDefault(profile=0, edge=1)] = 0
    # Shared field with no context_defaults (always present)
    description: str = "default"
    # Aliased field, edge-only
    source_port: Annotated[int, Alias("sport"), ContextDefault(profile=MISSING)] = -1
    # Aliased field, profile-only
    default_action: Annotated[str, Alias("defAction"), ContextDefault(edge=MISSING)] = (
        "allow"
    )


def _vco_dump(model):
    """VCO-format: by_alias=True, no compact omission, context exclusion only."""
    return model.model_dump(by_alias=True)


class TestVcoFormatSerialization:
    """VCO-format output: camelCase aliases, all defaults included, only
    context-absent (MISSING) fields excluded."""

    # -- Edge serialization --------------------------------------------------

    def test_edge_includes_edge_only_fields(self):
        m = VcoFormatModel(
            edge_override=False, log_level=3, description="test"
        ).for_edge()
        d = _vco_dump(m)
        assert d["edgeOverride"] is False
        assert d["logLevel"] == 3
        assert d["description"] == "test"

    def test_edge_omits_profile_only_fields(self):
        """Profile-only fields (context_defaults edge=MISSING) are excluded."""
        m = VcoFormatModel(edge_override=False, description="test").for_edge()
        d = _vco_dump(m)
        assert "profileBaseline" not in d
        assert "defAction" not in d

    def test_edge_default_value_included(self):
        """edge_override=True is the field default but VCO format keeps it."""
        m = VcoFormatModel(description="test").for_edge()
        d = _vco_dump(m)
        assert d["edgeOverride"] is True

    def test_edge_alias_sport_included(self):
        m = VcoFormatModel(source_port=443, description="test").for_edge()
        d = _vco_dump(m)
        assert d["sport"] == 443
        assert "sourcePort" not in d  # alias takes precedence

    def test_edge_alias_sport_default_included(self):
        """sport=-1 is the field default but VCO format keeps it."""
        m = VcoFormatModel(description="test").for_edge()
        d = _vco_dump(m)
        assert d["sport"] == -1

    # -- Profile serialization -----------------------------------------------

    def test_profile_includes_profile_only_fields(self):
        m = VcoFormatModel(
            profile_baseline="strict", log_level=5, description="test"
        ).for_profile()
        d = _vco_dump(m)
        assert d["profileBaseline"] == "strict"
        assert d["logLevel"] == 5
        assert d["description"] == "test"

    def test_profile_omits_edge_only_fields(self):
        """Edge-only fields (context_defaults profile=MISSING) are excluded."""
        m = VcoFormatModel(profile_baseline="strict", description="test").for_profile()
        d = _vco_dump(m)
        assert "edgeOverride" not in d
        assert "sport" not in d

    def test_profile_alias_def_action_included(self):
        m = VcoFormatModel(default_action="deny", description="test").for_profile()
        d = _vco_dump(m)
        assert d["defAction"] == "deny"
        assert "defaultAction" not in d  # alias takes precedence

    def test_profile_alias_def_action_default_included(self):
        """defAction='allow' is the field default but VCO format keeps it."""
        m = VcoFormatModel(description="test").for_profile()
        d = _vco_dump(m)
        assert d["defAction"] == "allow"

    # -- Defaults are preserved (not omitted like compact) -------------------

    def test_edge_log_level_at_context_default_included(self):
        """log_level=1 matches edge context default but VCO format keeps it."""
        m = VcoFormatModel(log_level=1, description="test").for_edge()
        d = _vco_dump(m)
        assert d["logLevel"] == 1

    def test_profile_log_level_at_context_default_included(self):
        """log_level=0 matches profile context default but VCO format keeps it."""
        m = VcoFormatModel(log_level=0, description="test").for_profile()
        d = _vco_dump(m)
        assert d["logLevel"] == 0

    def test_shared_field_default_included(self):
        """Shared field at default value is still included in VCO format."""
        m = VcoFormatModel().for_edge()
        d = _vco_dump(m)
        assert d["description"] == "default"

    # -- MISSING-valued fields excluded --------------------------------------

    def test_missing_valued_field_excluded(self):
        """profile_baseline defaults to MISSING -> excluded from VCO output."""
        m = VcoFormatModel(description="test").for_profile()
        d = _vco_dump(m)
        assert "profileBaseline" not in d

    # -- Full round-trip: edge vs profile produce disjoint key sets ----------

    def test_edge_and_profile_key_sets_disjoint_for_exclusive_fields(self):
        """Edge-only and profile-only keys never appear in the other context."""
        edge = VcoFormatModel(
            edge_override=False, source_port=80, log_level=2, description="e"
        ).for_edge()
        profile = VcoFormatModel(
            profile_baseline="strict",
            default_action="deny",
            log_level=2,
            description="p",
        ).for_profile()
        d_edge = _vco_dump(edge)
        d_profile = _vco_dump(profile)

        edge_only_keys = {"edgeOverride", "sport"}
        profile_only_keys = {"profileBaseline", "defAction"}

        assert edge_only_keys.isdisjoint(d_profile.keys())
        assert profile_only_keys.isdisjoint(d_edge.keys())
