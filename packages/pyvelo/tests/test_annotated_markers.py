"""Tests for composable Annotated markers (Alias, EdgeOnly, ProfileOnly, Normalizer, ContextDefault)."""

from typing import Annotated

from pydantic import Field
from pydantic_core import MISSING
from pyvelo.core import (
    Alias,
    ConfigContext,
    ContextDefault,
    EdgeOnly,
    NoneIsFalse,
    Normalizer,
    ProfileOnly,
    VcoModel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMPACT = {"compact": True}


def _compact(model, **extra_ctx):
    ctx = {**_COMPACT, **extra_ctx}
    return model.model_dump(by_alias=False, context=ctx)


def _compact_alias(model, **extra_ctx):
    ctx = {**_COMPACT, **extra_ctx}
    return model.model_dump(context=ctx)


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class AliasModel(VcoModel):
    type_: Annotated[str, Alias("type")] = "Radius"
    dh_group: Annotated[int | MISSING, Alias("DHGroup")] = MISSING


class EdgeOnlyModel(VcoModel):
    override: Annotated[bool, EdgeOnly] = True
    enabled: Annotated[bool, EdgeOnly] = False
    name: str = "default"


class ProfileOnlyModel(VcoModel):
    defaults: Annotated[list[str] | MISSING, ProfileOnly] = MISSING
    shared: str = "ok"


class NormModel(VcoModel):
    area: Annotated[
        list[int], Normalizer(lambda v: [0] if v in ("", "0", 0, [], [0]) else v)
    ] = Field(default_factory=lambda: [0])


class CtxDefaultModel(VcoModel):
    routed_interfaces: Annotated[list[str] | None, ContextDefault(profile=None)] = (
        Field(default_factory=list)
    )


class ComposedModel(VcoModel):
    override: Annotated[bool, NoneIsFalse, EdgeOnly] = True
    enabled: Annotated[bool, EdgeOnly] = False


class AliasEdgeOnly(VcoModel):
    base_dhcp_addr: Annotated[int, Alias("baseDhcpAddr"), EdgeOnly] = 13


class AliasNormalizer(VcoModel):
    ssm: Annotated[
        str, Alias("ssm"), Normalizer(lambda v: "255.255.255.255" if v == "any" else v)
    ] = "255.255.255.255"


# ---------------------------------------------------------------------------
# TestAliasMarker
# ---------------------------------------------------------------------------


class TestAliasMarker:
    def test_validates_from_vco_key(self):
        m = AliasModel.model_validate({"type": "LDAP", "DHGroup": 14})
        assert m.type_ == "LDAP"
        assert m.dh_group == 14

    def test_validates_from_python_name(self):
        m = AliasModel(type_="LDAP", dh_group=14)
        assert m.type_ == "LDAP"
        assert m.dh_group == 14

    def test_serializes_to_vco_key(self):
        m = AliasModel(type_="LDAP", dh_group=14)
        d = m.model_dump()
        assert "type" in d
        assert "DHGroup" in d
        assert d["type"] == "LDAP"
        assert d["DHGroup"] == 14

    def test_round_trip(self):
        original = {"type": "LDAP", "DHGroup": 14}
        m = AliasModel.model_validate(original)
        dumped = m.model_dump()
        assert dumped["type"] == "LDAP"
        assert dumped["DHGroup"] == 14

    def test_missing_default(self):
        m = AliasModel()
        assert m.dh_group is MISSING

    def test_compact_drops_defaults(self):
        m = AliasModel()
        d = _compact(m)
        assert "type_" not in d
        assert "dh_group" not in d

    def test_compact_keeps_non_defaults(self):
        m = AliasModel(type_="LDAP", dh_group=14)
        d = _compact(m)
        assert d["type_"] == "LDAP"
        assert d["dh_group"] == 14


# ---------------------------------------------------------------------------
# TestEdgeOnlyMarker
# ---------------------------------------------------------------------------


class TestEdgeOnlyMarker:
    def test_omitted_at_profile(self):
        m = EdgeOnlyModel(override=True, enabled=True)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "override" not in d
        assert "enabled" not in d

    def test_kept_at_edge(self):
        m = EdgeOnlyModel(override=False, enabled=True)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["override"] is False
        assert d["enabled"] is True

    def test_default_dropped_at_edge(self):
        m = EdgeOnlyModel()
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "override" not in d
        assert "enabled" not in d

    def test_vco_format_omits_at_profile(self):
        m = EdgeOnlyModel(override=True, enabled=True).for_profile()
        d = m.model_dump(by_alias=False)
        assert "override" not in d
        assert "enabled" not in d
        assert d["name"] == "default"


# ---------------------------------------------------------------------------
# TestProfileOnlyMarker
# ---------------------------------------------------------------------------


class TestProfileOnlyMarker:
    def test_omitted_at_edge(self):
        m = ProfileOnlyModel(defaults=["rule1"])
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "defaults" not in d

    def test_kept_at_profile(self):
        m = ProfileOnlyModel(defaults=["rule1"])
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert d["defaults"] == ["rule1"]


# ---------------------------------------------------------------------------
# TestNormMarker
# ---------------------------------------------------------------------------


class TestNormMarker:
    def test_equivalent_representations_dropped(self):
        # Normalizers run at compact serialization time, not validation time.
        # Only test values that are valid for list[int].
        for equivalent in ([], [0]):
            m = NormModel.model_validate({"area": equivalent})
            d = _compact(m)
            assert "area" not in d, (
                f"Expected area to be dropped for value {equivalent!r}"
            )

    def test_default_factory_value_dropped(self):
        m = NormModel()
        d = _compact(m)
        assert "area" not in d

    def test_real_value_kept(self):
        m = NormModel(area=[1, 2])
        d = _compact(m)
        assert d["area"] == [1, 2]


# ---------------------------------------------------------------------------
# TestCtxDefaultMarker
# ---------------------------------------------------------------------------


class TestCtxDefaultMarker:
    def test_profile_uses_none_default(self):
        m = CtxDefaultModel()
        d = _compact(m, config_level=ConfigContext.PROFILE)
        # default_factory=list produces [], but profile default is None, so [] != None -> kept
        assert d["routed_interfaces"] == []

    def test_profile_none_matches_ctx_default(self):
        m = CtxDefaultModel(routed_interfaces=None)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "routed_interfaces" not in d

    def test_edge_empty_list_matches_field_default(self):
        m = CtxDefaultModel()
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert "routed_interfaces" not in d

    def test_edge_populated_list_kept(self):
        m = CtxDefaultModel(routed_interfaces=["eth0"])
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["routed_interfaces"] == ["eth0"]


# ---------------------------------------------------------------------------
# TestComposition
# ---------------------------------------------------------------------------


class TestComposition:
    def test_none_is_false_plus_edge_only(self):
        m = ComposedModel.model_validate({"override": None})
        assert m.override is False

    def test_edge_only_omits_at_profile(self):
        m = ComposedModel(override=False, enabled=True)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "override" not in d
        assert "enabled" not in d

    def test_edge_only_keeps_non_default_at_edge(self):
        m = ComposedModel(override=False, enabled=True)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["override"] is False
        assert d["enabled"] is True

    def test_alias_plus_edge_only(self):
        m = AliasEdgeOnly(base_dhcp_addr=42)
        d = _compact(m, config_level=ConfigContext.EDGE)
        assert d["base_dhcp_addr"] == 42

    def test_alias_plus_edge_only_omits_at_profile(self):
        m = AliasEdgeOnly(base_dhcp_addr=42)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "base_dhcp_addr" not in d

    def test_alias_plus_edge_only_serializes_vco_key(self):
        m = AliasEdgeOnly(base_dhcp_addr=42)
        d = m.model_dump()
        assert "baseDhcpAddr" in d

    def test_alias_plus_norm(self):
        m = AliasNormalizer.model_validate({"ssm": "any"})
        d = _compact(m)
        assert "ssm" not in d

    def test_alias_plus_norm_real_value_kept(self):
        m = AliasNormalizer(ssm="10.0.0.1")
        d = _compact(m)
        assert d["ssm"] == "10.0.0.1"


# ---------------------------------------------------------------------------
# TestInheritance
# ---------------------------------------------------------------------------


class ParentModel(VcoModel):
    aliased: Annotated[str, Alias("aliasedKey")] = "parent"
    edge: Annotated[bool, EdgeOnly] = True


class ChildModel(ParentModel):
    child_field: str = "child"


class TestInheritance:
    def test_parent_alias_works_in_child(self):
        m = ChildModel.model_validate({"aliasedKey": "override"})
        assert m.aliased == "override"

    def test_parent_edge_only_works_in_child(self):
        m = ChildModel(edge=False)
        d = _compact(m, config_level=ConfigContext.PROFILE)
        assert "edge" not in d

    def test_child_serializes_parent_alias(self):
        m = ChildModel(aliased="val")
        d = m.model_dump()
        assert d["aliasedKey"] == "val"

    def test_child_compact_drops_parent_defaults(self):
        m = ChildModel()
        d = _compact(m)
        assert "aliased" not in d
        assert "child_field" not in d
