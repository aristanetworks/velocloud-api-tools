"""Tests for LAN data model annotations."""

import copy
from typing import Any

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
from pyvelo.core import ConfigContext
from pyvelo.core.fields import (
    get_key_spec,
    get_resolvable,
    has_override_flag,
    is_edge_local,
)
from pyvelo.core.rebase import raw_changes, rebase


class TestLanKeyFields:
    def test_interfaces_keyed_by_name(self) -> None:
        spec = get_key_spec(LanSettings.model_fields["interfaces"])
        assert spec is not None
        assert spec.paths == ("name",)

    def test_networks_keyed_by_vlan_id(self) -> None:
        spec = get_key_spec(LanSettings.model_fields["networks"])
        assert spec is not None
        assert spec.paths == ("vlan_id",)


class TestLanOverrideFlags:
    def test_lan_network_override(self) -> None:
        assert has_override_flag(LanNetwork.model_fields["override"])

    def test_dhcp_override(self) -> None:
        assert has_override_flag(EdgeLanDhcp.model_fields["override"])

    def test_ospf_override(self) -> None:
        assert has_override_flag(LanOspf.model_fields["override"])

    def test_interface_override(self) -> None:
        assert has_override_flag(LanInterfaceBase.model_fields["override"])

    def test_visibility_override(self) -> None:
        assert has_override_flag(EdgeClientVisibility.model_fields["override"])

    def test_override_none_coerced_to_false(self) -> None:
        n = LanNetwork.model_validate({"name": "test", "vlanId": 1, "override": None})
        assert n.override is False


class TestLanEdgeLocal:
    def test_cidr_ip_edge_local(self) -> None:
        assert is_edge_local(LanNetwork.model_fields["cidr_ip"])

    def test_cidr_prefix_edge_local(self) -> None:
        assert is_edge_local(LanNetwork.model_fields["cidr_prefix"])

    def test_netmask_edge_local(self) -> None:
        assert is_edge_local(LanNetwork.model_fields["netmask"])

    def test_interface_address_edge_local(self) -> None:
        assert is_edge_local(LanNetwork.model_fields["interface_address"])

    def test_fixed_ips_edge_local(self) -> None:
        assert is_edge_local(LanNetwork.model_fields["fixed_ips"])

    def test_secondary_ips_edge_local(self) -> None:
        assert is_edge_local(LanNetwork.model_fields["secondary_ips"])

    def test_v6_cidr_ip_edge_local(self) -> None:
        assert is_edge_local(LanAddressingV6.model_fields["cidr_ip"])

    def test_v6_cidr_prefix_edge_local(self) -> None:
        assert is_edge_local(LanAddressingV6.model_fields["cidr_prefix"])

    def test_v6_dhcp_fixed_ip_edge_local(self) -> None:
        assert is_edge_local(LanDhcpServerV6.model_fields["fixed_ip"])

    def test_advertise_not_edge_local(self) -> None:
        assert not is_edge_local(LanNetwork.model_fields["advertise"])


class TestLanResolvable:
    def test_segment_id_resolvable(self) -> None:
        r = get_resolvable(LanNetwork.model_fields["segment_id"])
        assert r is not None
        assert r.attribute == "segment_id"


MAC_1, MAC_2 = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"
NETWORK: dict[str, Any] = {
    "cidrIp": "10.0.0.1",
    "cidrPrefix": 24,
    "vlanId": 1,
    "name": "v",
    "fixedIp": [
        {"macAddress": MAC_1, "lanIp": "10.0.0.10", "description": "A", "u": "A-raw"},
        {"macAddress": MAC_2, "lanIp": "10.0.0.20", "description": "B", "u": "B-raw"},
    ],
}


def network() -> LanNetwork:
    return LanNetwork.model_validate(
        copy.deepcopy(NETWORK), context={"config_level": ConfigContext.EDGE}
    )


def saved(now: LanNetwork) -> tuple[list[Any], list[str]]:
    """The reservations a save of *now* writes, and the paths of its changes."""
    before = network().model_dump(mode="json", by_alias=True)
    after = now.model_dump(mode="json", by_alias=True)
    out = rebase(copy.deepcopy(NETWORK), before, after, LanNetwork)
    assert isinstance(out, dict)
    return out["fixedIp"], [str(c.path) for c in raw_changes(before, after, LanNetwork)]


def test_two_reservations_that_swap_ips_keep_their_own_unknown_keys() -> None:
    now = network()
    now.fixed_ips[0].lan_ip, now.fixed_ips[1].lan_ip = "10.0.0.20", "10.0.0.10"
    written, changed = saved(now)
    assert written == [
        {"macAddress": MAC_1, "lanIp": "10.0.0.20", "description": "A", "u": "A-raw"},
        {"macAddress": MAC_2, "lanIp": "10.0.0.10", "description": "B", "u": "B-raw"},
    ]
    assert changed == [f'fixedIp["{MAC_1}"].lanIp', f'fixedIp["{MAC_2}"].lanIp']


def test_a_reservation_whose_mac_changes_is_removed_and_added() -> None:
    now = network()
    now.fixed_ips[0].mac_address = "aa:bb:cc:dd:ee:09"
    written, changed = saved(now)
    assert written[0] == {
        "macAddress": "aa:bb:cc:dd:ee:09",
        "lanIp": "10.0.0.10",
        "description": "A",
    }
    assert written[1] == NETWORK["fixedIp"][1]
    assert changed == [f'fixedIp["{MAC_1}"]', 'fixedIp["aa:bb:cc:dd:ee:09"]']
