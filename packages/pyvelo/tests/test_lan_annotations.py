"""Tests for LAN data model annotations."""

from pyvelo.client.models.configuration.device_settings.lan import (
    EdgeClientVisibility,
    EdgeLanDhcp,
    LanAddressingV6,
    LanDhcpServerV6,
    LanInterfaceBase,
    LanNetwork,
    LanOspf,
    LanSettings,
)
from pyvelo.core.fields import (
    get_key_field,
    get_resolvable,
    has_override_flag,
    is_edge_local,
)


class TestLanKeyFields:
    def test_interfaces_keyed_by_name(self):
        assert get_key_field(LanSettings.model_fields["interfaces"]) == "name"

    def test_networks_keyed_by_vlan_id(self):
        assert get_key_field(LanSettings.model_fields["networks"]) == "vlan_id"


class TestLanOverrideFlags:
    def test_lan_network_override(self):
        assert has_override_flag(LanNetwork.model_fields["override"])

    def test_dhcp_override(self):
        assert has_override_flag(EdgeLanDhcp.model_fields["override"])

    def test_ospf_override(self):
        assert has_override_flag(LanOspf.model_fields["override"])

    def test_interface_override(self):
        assert has_override_flag(LanInterfaceBase.model_fields["override"])

    def test_visibility_override(self):
        assert has_override_flag(EdgeClientVisibility.model_fields["override"])

    def test_override_none_coerced_to_false(self):
        n = LanNetwork.model_validate({"name": "test", "vlanId": 1, "override": None})
        assert n.override is False


class TestLanEdgeLocal:
    def test_cidr_ip_edge_local(self):
        assert is_edge_local(LanNetwork.model_fields["cidr_ip"])

    def test_cidr_prefix_edge_local(self):
        assert is_edge_local(LanNetwork.model_fields["cidr_prefix"])

    def test_netmask_edge_local(self):
        assert is_edge_local(LanNetwork.model_fields["netmask"])

    def test_interface_address_edge_local(self):
        assert is_edge_local(LanNetwork.model_fields["interface_address"])

    def test_fixed_ips_edge_local(self):
        assert is_edge_local(LanNetwork.model_fields["fixed_ips"])

    def test_secondary_ips_edge_local(self):
        assert is_edge_local(LanNetwork.model_fields["secondary_ips"])

    def test_v6_cidr_ip_edge_local(self):
        assert is_edge_local(LanAddressingV6.model_fields["cidr_ip"])

    def test_v6_cidr_prefix_edge_local(self):
        assert is_edge_local(LanAddressingV6.model_fields["cidr_prefix"])

    def test_v6_dhcp_fixed_ip_edge_local(self):
        assert is_edge_local(LanDhcpServerV6.model_fields["fixed_ip"])

    def test_advertise_not_edge_local(self):
        assert not is_edge_local(LanNetwork.model_fields["advertise"])


class TestLanResolvable:
    def test_segment_id_resolvable(self):
        r = get_resolvable(LanNetwork.model_fields["segment_id"])
        assert r is not None
        assert r.id_attr == "segment_id"
