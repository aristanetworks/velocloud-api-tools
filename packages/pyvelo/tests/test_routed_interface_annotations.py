"""Tests for routed interface data model annotations."""

from pyvelo.client.models.configuration.device_settings.routed_interface import (
    RoutedInterfaceAddressing,
    RoutedInterfaceBase,
)
from pyvelo.core.fields import (
    get_resolvable,
    has_override_flag,
    is_edge_local,
)


class TestRoutedInterfaceOverrideFlag:
    def test_override_flag(self):
        assert has_override_flag(RoutedInterfaceBase.model_fields["override"])

    def test_override_none_coerced_to_false(self):
        ri = RoutedInterfaceBase.model_validate({"override": None})
        assert ri.override is False


class TestRoutedInterfaceEdgeLocal:
    def test_cidr_ip_edge_local(self):
        assert is_edge_local(RoutedInterfaceAddressing.model_fields["cidr_ip"])

    def test_cidr_prefix_edge_local(self):
        assert is_edge_local(RoutedInterfaceAddressing.model_fields["cidr_prefix"])

    def test_netmask_edge_local(self):
        assert is_edge_local(RoutedInterfaceAddressing.model_fields["netmask"])

    def test_gateway_edge_local(self):
        assert is_edge_local(RoutedInterfaceAddressing.model_fields["gateway"])

    def test_type_not_edge_local(self):
        assert not is_edge_local(RoutedInterfaceAddressing.model_fields["type_"])

    def test_dhcp_server_edge_local(self):
        assert is_edge_local(RoutedInterfaceBase.model_fields["dhcp_server"])

    def test_description_edge_local(self):
        assert is_edge_local(RoutedInterfaceBase.model_fields["description"])


class TestRoutedInterfaceResolvable:
    def test_segment_id_resolvable(self):
        r = get_resolvable(RoutedInterfaceBase.model_fields["segment_id"])
        assert r is not None
        assert r.id_attr == "segment_id"
