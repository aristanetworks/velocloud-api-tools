"""Tests for LAN data model annotations."""

import copy
from typing import Annotated, Any

import pytest
from pydantic import Field
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
from pyvelo.core import ConfigContext, KeyField, PatchError, VcoModel
from pyvelo.core.changes import patch_of
from pyvelo.core.fields import (
    get_key_spec,
    get_resolvable,
    has_override_flag,
    is_edge_local,
)
from pyvelo.core.rebase import RawChange, raw_changes, rebase


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
    assert written == [
        {"macAddress": "aa:bb:cc:dd:ee:09", "lanIp": "10.0.0.10", "description": "A"},
        NETWORK["fixedIp"][1],
    ]
    assert changed == [f'fixedIp["{MAC_1}"]', 'fixedIp["aa:bb:cc:dd:ee:09"]']


UPPER_1, UPPER_2 = MAC_1.upper(), MAC_2.upper()


def test_a_reservation_is_selected_by_its_mac_in_any_case() -> None:
    now = network()
    _ = now.merge_patch({"fixed_ips": {UPPER_2: {"description": "sel"}}})
    assert [(f.mac_address, f.description) for f in now.fixed_ips] == [
        (MAC_1, "A"),
        (MAC_2, "sel"),
    ]


def test_an_add_under_a_mac_held_in_another_case_is_refused() -> None:
    now = network()
    with pytest.raises(PatchError) as exc:
        _ = now.merge_patch(
            {"fixed_ips": {UPPER_1: {"_op": "add", "lan_ip": "10.0.0.30"}}}
        )
    assert exc.value.kind == "exists"
    assert [f.mac_address for f in now.fixed_ips] == [MAC_1, MAC_2]


def test_a_mac_rewritten_in_another_case_is_the_same_reservation() -> None:
    now = network()
    now.fixed_ips[0].mac_address = UPPER_1
    written, changed = saved(now)
    assert written == [
        {**NETWORK["fixedIp"][0], "macAddress": UPPER_1},
        NETWORK["fixedIp"][1],
    ]
    assert changed == [f'fixedIp["{MAC_1}"].macAddress']


MAC_5 = "aa:bb:cc:dd:ee:05"
UPPER_5 = MAC_5.upper()


def test_two_adds_of_one_mac_in_two_cases_are_refused_as_two_of_one_mac_are() -> None:
    exact = network()
    with pytest.raises(PatchError) as same:
        _ = exact.merge_patch(
            {
                "fixed_ips": {
                    "+a": {"_op": "add", "mac_address": MAC_5, "lan_ip": "10.0.0.5"},
                    "+b": {"_op": "add", "mac_address": MAC_5, "lan_ip": "10.0.0.6"},
                }
            }
        )
    now = network()
    with pytest.raises(PatchError) as exc:
        _ = now.merge_patch(
            {
                "fixed_ips": {
                    UPPER_5: {"_op": "add", "lan_ip": "10.0.0.5"},
                    MAC_5: {"_op": "add", "lan_ip": "10.0.0.6"},
                }
            }
        )
    assert exc.value.kind == same.value.kind == "duplicate"
    assert str(exc.value) == (
        f'fixed_ips["{MAC_5}"]: fixed_ips["{UPPER_5}"] adds this key too: '
        "one add per key"
    )
    assert [f.mac_address for f in now.fixed_ips] == [MAC_1, MAC_2]


def test_an_add_of_the_mac_a_rename_took_in_another_case_is_refused() -> None:
    now = network()
    with pytest.raises(PatchError) as exc:
        _ = now.merge_patch(
            {
                "fixed_ips": {
                    MAC_1: {"mac_address": MAC_5},
                    UPPER_5: {"_op": "add", "lan_ip": "10.0.0.6"},
                }
            }
        )
    assert exc.value.kind == "exists"
    assert str(exc.value) == (
        f'fixed_ips["{UPPER_5}"]: {UPPER_5!r} is already in the list; to change '
        'it, write a plain object under its selector (no "_op")'
    )


TWO_CASES = [
    {"mac_address": MAC_1, "lan_ip": "10.0.0.1"},
    {"mac_address": UPPER_1, "lan_ip": "10.0.0.2"},
]


def test_a_list_value_with_one_mac_in_two_cases_is_refused() -> None:
    now = network()
    with pytest.raises(PatchError) as exc:
        _ = now.merge_patch({"fixed_ips": TWO_CASES})
    assert exc.value.kind == "duplicate"
    assert str(exc.value) == (
        f"fixed_ips: items #0 and #1 have one key, {UPPER_1!r}: the items of a "
        "keyed list have distinct keys"
    )
    assert [f.mac_address for f in now.fixed_ips] == [MAC_1, MAC_2]


def test_a_list_value_with_one_mac_in_two_cases_is_left_out_of_a_patch() -> None:
    change = RawChange(
        "fixedIp",
        [],
        [
            {"macAddress": MAC_1, "lanIp": "10.0.0.1"},
            {"macAddress": UPPER_1, "lanIp": "10.0.0.2"},
        ],
    )
    kept = RawChange("name", "v", "w")
    assert patch_of([change, kept], LanNetwork) == ({"name": "w"}, (change,))


class Tag(VcoModel):
    name: str


class Tags(VcoModel):
    tags: Annotated[list[Tag], KeyField("name")] = Field(default_factory=list)


def test_a_list_without_fold_case_takes_keys_that_differ_only_by_case() -> None:
    adds = Tags()
    _ = adds.merge_patch({"tags": {"Ab": {"_op": "add"}, "ab": {"_op": "add"}}})
    renamed = Tags.model_validate({"tags": [{"name": "x"}]})
    _ = renamed.merge_patch({"tags": {"x": {"name": "ab"}, "Ab": {"_op": "add"}}})
    value = [{"name": "Ab"}, {"name": "ab"}]
    whole = Tags()
    _ = whole.merge_patch({"tags": value})
    assert [t.name for t in adds.tags] == ["Ab", "ab"]
    assert [t.name for t in renamed.tags] == ["ab", "Ab"]
    assert [t.name for t in whole.tags] == ["Ab", "ab"]
    change = RawChange("tags", [], value)
    assert patch_of([change], Tags) == ({"tags": value}, ())
