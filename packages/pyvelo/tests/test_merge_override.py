"""Tests for OverrideFlag auto-setting during merge_patch."""

from typing import Annotated

from pydantic import Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import EdgeLocal, OverrideFlag


class Addressing(VcoModel):
    cidr_ip: Annotated[str, EdgeLocal] = ""
    cidr_prefix: Annotated[int, EdgeLocal] = 24
    gateway: str = ""


class V6Detail(VcoModel):
    addressing: Addressing = Field(default_factory=Addressing)
    other_setting: str = ""


class Network(VcoModel):
    override: Annotated[bool, OverrideFlag] = False
    name: str = ""
    cidr_ip: Annotated[str, EdgeLocal] = ""
    cidr_prefix: Annotated[int, EdgeLocal] = 24
    advertise: bool = True
    v6_detail: V6Detail = Field(default_factory=V6Detail)


class HasMultipleOverrideScopes(VcoModel):
    dns_override: Annotated[bool, OverrideFlag] = False
    primary_dns: str = ""
    networks: list[Network] = Field(default_factory=list)


class TestOverrideFlagAutoSet:
    def test_non_edge_local_change_triggers_override(self):
        m = Network(override=False, advertise=True)
        _ = m.merge_patch({"advertise": False})
        assert m.override is True

    def test_edge_local_change_does_not_trigger_override(self):
        m = Network(override=False, cidr_ip="10.0.0.1")
        _ = m.merge_patch({"cidr_ip": "10.0.0.2"})
        assert m.override is False

    def test_mixed_changes_triggers_override(self):
        m = Network(override=False)
        _ = m.merge_patch({"cidr_ip": "10.0.0.2", "advertise": False})
        assert m.override is True

    def test_only_edge_local_changes_no_trigger(self):
        m = Network(override=False)
        _ = m.merge_patch({"cidr_ip": "10.0.0.2", "cidr_prefix": 16})
        assert m.override is False


class TestOverrideFlagDeepAncestorWalk:
    def test_deep_non_edge_local_triggers_ancestor_override(self):
        m = Network(override=False)
        _ = m.merge_patch({"v6_detail": {"addressing": {"gateway": "10.0.0.1"}}})
        assert m.override is True

    def test_deep_edge_local_does_not_trigger(self):
        m = Network(override=False)
        _ = m.merge_patch({"v6_detail": {"addressing": {"cidr_ip": "10.0.0.2"}}})
        assert m.override is False

    def test_deep_non_edge_local_in_non_override_container(self):
        m = Network(override=False)
        _ = m.merge_patch({"v6_detail": {"other_setting": "changed"}})
        assert m.override is True


class TestOverrideFlagMultipleScopes:
    def test_change_in_dns_scope_triggers_dns_override(self):
        m = HasMultipleOverrideScopes(dns_override=False, primary_dns="8.8.8.8")
        _ = m.merge_patch({"primary_dns": "1.1.1.1"})
        assert m.dns_override is True

    def test_change_in_network_does_not_trigger_parent_override(self):
        net = Network(name="test", override=False, advertise=True)
        m = HasMultipleOverrideScopes(dns_override=False, networks=[net])
        # Directly mutate the network — this tests that override scoping
        # is per-model, not global. The network's override should change,
        # not the parent's dns_override.
        _ = net.merge_patch({"advertise": False})
        assert net.override is True
        assert m.dns_override is False
