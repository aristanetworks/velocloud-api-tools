"""Fields are read under the key the VCO stores them at.

Each document here is shaped as a VCO stores it. A field holds the stored
value, a plan with no edit sends nothing and holds the bytes read, and an edit
is written at the stored key.
"""

import copy
import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from pyvelo.client.models.configuration.device_settings.root import EdgeSnmpV2c
from pyvelo.client.models.configuration.device_settings.routed_interface import (
    RoutedInterfaceCell,
    RoutedInterfaceCellular,
)
from pyvelo.client.models.configuration.device_settings.segment import (
    GatewayListItem,
    SegmentHandoffGateways,
)
from pyvelo.client.models.enterprise.service_data import LoadSharingMethod
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.client.schemas.edge import EdgeLink
from pyvelo.core import ConfigContext
from pyvelo.core.layers import Edge
from pyvelo.testing import make_module_json

from ._type_support import require_present

EDGE, PROFILE = ConfigContext.EDGE, ConfigContext.PROFILE


def u(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012x}"


def both_ways[M: ConfigurationModuleBase](
    cls: type[M], name: str, raw: dict[str, Any]
) -> tuple[M, M]:
    """*raw* as a module row, parsed from python objects and from JSON text."""
    row = make_module_json(name, copy.deepcopy(raw))
    return cls.model_validate(copy.deepcopy(row)), cls.model_validate_json(
        json.dumps(row)
    )


def assert_untouched(
    module: ConfigurationModuleBase, raw: dict[str, Any], level: ConfigContext
) -> None:
    plan = plan_module(module, level=level)
    assert plan.operation == "none"
    assert plan.data == raw
    assert module.raw_data == raw


# --- WAN ---------------------------------------------------------------------

LINK_ID = u(0xA1)


def cos_entry(n: int, name: str, percent: int, sla: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": u(n),
        "name": name,
        "dscpTags": ["EF"],
        "bandwidthPct": percent,
        "bandwidthGuaranteed": False,
        "defaultClassOfService": False,
        "policing": False,
        "staticSLA": sla,
    }


# A class's static SLA is stored as numbers by some writers and as text by others.
SLA_NUMBERS: dict[str, Any] = {"latencyMs": 400, "jitterMs": 15, "lossPct": 10}
SLA_TEXT: dict[str, Any] = {"latencyMs": "0", "jitterMs": "0", "lossPct": "0.0"}
WAN: dict[str, Any] = {
    "links": [
        {
            "internalId": LINK_ID,
            "logicalId": "00:00:00:00:00:01:0000",
            "name": "ISP A",
            "type": "WIRED",
            "mode": "PUBLIC",
            "discovery": "USER_DEFINED",
            "addressingVersion": "IPv4",
            "classesofServiceEnabled": True,
            "staticSLA": {"latencyMs": 0, "jitterMs": 0, "lossPct": 0},
            "classesOfService": {
                "classId": None,
                "classesOfService": [
                    cos_entry(1, "voice", 30, SLA_NUMBERS),
                    cos_entry(2, "rest", 70, SLA_TEXT),
                ],
            },
        }
    ],
    "networks": [],
}


def wan(raw: dict[str, Any] = WAN) -> WanConfigurationModule:
    return both_ways(WanConfigurationModule, "WAN", raw)[0]


def test_a_wan_link_is_read_at_the_keys_the_vco_stores() -> None:
    for module in both_ways(WanConfigurationModule, "WAN", WAN):
        link = require_present(module.data.links)[0]
        assert link.class_of_service_enabled is True
        voice, rest = link.classes_of_service.classes_of_service
        assert (voice.bandwidth_percent, rest.bandwidth_percent) == (30, 70)
        assert type(voice.bandwidth_percent) is int
        sla = voice.static_sla
        assert (sla.latency_ms, sla.jitter_ms, sla.loss_pct) == (400, 15, 10)
        sla = rest.static_sla
        assert (sla.latency_ms, sla.jitter_ms, sla.loss_pct) == ("0", "0", "0.0")
        assert_untouched(module, WAN, EDGE)


def test_a_link_s_static_sla_holds_the_numbers_a_vco_stores() -> None:
    raw = copy.deepcopy(WAN)
    raw["links"][0]["staticSLA"] = {"latencyMs": 10, "jitterMs": 20, "lossPct": 0.1}
    for module in both_ways(WanConfigurationModule, "WAN", raw):
        sla = require_present(module.data.links)[0].static_sla
        assert (sla.latency_ms, sla.jitter_ms, sla.loss_pct) == (10, 20, 0.1)
        assert_untouched(module, raw, EDGE)


def test_turning_class_of_service_off_is_written_at_the_stored_key() -> None:
    module = wan()
    require_present(module.data.links)[0].class_of_service_enabled = False
    plan = plan_module(module, level=EDGE)
    expected = copy.deepcopy(WAN)
    expected["links"][0]["classesofServiceEnabled"] = False
    assert plan.data == expected
    assert [str(c.path) for c in plan.changes] == [
        f"links[{LINK_ID},IPv4].classesofServiceEnabled"
    ]


def test_an_edit_to_one_class_keeps_what_every_class_stores() -> None:
    module = wan()
    link = require_present(module.data.links)[0]
    link.classes_of_service.classes_of_service[0].policing = True
    sent = plan_module(module, level=EDGE).data["links"][0]["classesOfService"]
    stored = WAN["links"][0]["classesOfService"]["classesOfService"]
    # The list has no key, so it is written whole from the models.
    kept = ("id", "bandwidthPct", "staticSLA")
    assert [[e[k] for k in kept] for e in sent["classesOfService"]] == [
        [e[k] for k in kept] for e in stored
    ]
    assert [e["policing"] for e in sent["classesOfService"]] == [True, False]
    assert not any(
        "bandwidth_pct" in e or "staticSla" in e for e in sent["classesOfService"]
    )


def test_a_link_that_also_stores_the_key_this_library_wrote_keeps_it_as_read() -> None:
    raw = copy.deepcopy(WAN)
    raw["links"][0]["classesOfServiceEnabled"] = False  # beside the stored key
    for module in both_ways(WanConfigurationModule, "WAN", raw):
        assert require_present(module.data.links)[0].class_of_service_enabled is True
        assert_untouched(module, raw, EDGE)
    module = wan(raw)
    require_present(module.data.links)[0].description = "uplink"
    expected = copy.deepcopy(raw)
    expected["links"][0]["description"] = "uplink"
    assert plan_module(module, level=EDGE).data == expected


# --- device settings ---------------------------------------------------------

DESC: dict[str, Any] = {
    "segmentId": 0,
    "name": "Global Segment",
    "type": "REGULAR",
    "segmentLogicalId": u(0),
}
GATEWAY = uuid.UUID(u(0x77))
WRAPPER = uuid.UUID(u(0x88))
CONTROLLER = uuid.UUID(u(0x66))
# A profile's entry: the gateways chosen (gatewayList, by the Gateway's logical
# ID) and the rows the VCO made for them (gateways, by each row's logical ID).
HANDOFF: dict[str, Any] = {
    "autoSelect": False,
    "gatewayList": [{"logicalId": f"gateway{GATEWAY}", "ipsecDetail": {}}],
    "gateways": [{"logicalId": str(WRAPPER)}],
}
CELLULAR: dict[str, Any] = {
    "simPin": "",
    "network": "",
    "apn": "",
    "username": "",
    "password": "",
    "iptype": "IPv4v6",
}
SNMP: dict[str, Any] = {
    "port": 161,
    "snmpv2c": {
        "enabled": True,
        "community": "",
        "communityList": [],
        "allowedIp": ["10.0.0.9"],
    },
    "snmpv3": {"enabled": False, "users": []},
}
PROFILE_DS: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "segments": [
        {
            "segment": DESC,
            "handOffGateways": HANDOFF,
            "handOffControllers": {
                "autoSelect": True,
                "gatewayList": [{"logicalId": f"gateway{CONTROLLER}"}],
            },
        }
    ],
    "snmp": SNMP,
}
EDGE_DS: dict[str, Any] = {
    "lan": {"networks": [], "interfaces": []},
    "segments": [{"segment": DESC, "handOffGateways": {**HANDOFF, "override": True}}],
    "routedInterfaces": [{"name": "CELL1", "cellular": CELLULAR}],
}


def device_settings(raw: dict[str, Any]) -> DeviceSettingsConfigurationModule:
    return both_ways(DeviceSettingsConfigurationModule, "deviceSettings", raw)[0]


@pytest.mark.parametrize(
    ("raw", "level"), [(PROFILE_DS, PROFILE), (EDGE_DS, EDGE)], ids=["profile", "edge"]
)
def test_a_segment_s_handoff_gateways_are_read(
    raw: dict[str, Any], level: ConfigContext
) -> None:
    for module in both_ways(DeviceSettingsConfigurationModule, "deviceSettings", raw):
        handoff = module.data.segments[0].handoff_gateways
        assert isinstance(handoff, SegmentHandoffGateways)
        assert handoff.auto_select is False
        assert [g.logical_id for g in handoff.gateway_list] == [GATEWAY]
        assert [g.logical_id for g in handoff.gateways] == [str(WRAPPER)]
        assert_untouched(module, raw, level)


def test_a_segment_s_handoff_controllers_are_read() -> None:
    module = device_settings(PROFILE_DS)
    controllers = module.data.segments[0].handoff_controllers
    assert isinstance(controllers, SegmentHandoffGateways)
    assert controllers.auto_select is True
    assert [g.logical_id for g in controllers.gateway_list] == [CONTROLLER]
    assert_untouched(module, PROFILE_DS, PROFILE)


def test_an_edit_beside_the_handoff_lists_leaves_them_as_read() -> None:
    module = device_settings(PROFILE_DS)
    handoff = module.data.segments[0].handoff_gateways
    assert isinstance(handoff, SegmentHandoffGateways)
    handoff.auto_select = True
    plan = plan_module(module, level=PROFILE)
    expected = copy.deepcopy(PROFILE_DS)
    expected["segments"][0]["handOffGateways"]["autoSelect"] = True
    assert plan.data == expected
    assert [str(c.path) for c in plan.changes] == [
        'segments["Global Segment"].handOffGateways.autoSelect'
    ]


def test_a_row_the_vco_made_keeps_its_stored_form_when_the_list_is_written() -> None:
    raw = copy.deepcopy(PROFILE_DS)
    stored_row = {"logicalId": str(WRAPPER), "name": "gw-1"}
    raw["segments"][0]["handOffGateways"]["gateways"] = [stored_row]
    module = device_settings(raw)
    handoff = module.data.segments[0].handoff_gateways
    assert isinstance(handoff, SegmentHandoffGateways)
    row_class = type(handoff.gateways[0])
    handoff.gateways.append(row_class.model_validate({"logicalId": u(0x99)}))
    sent = plan_module(module, level=PROFILE).data["segments"][0]["handOffGateways"]
    # The list has no way to pair an item whose ID dumps in another form, so
    # the stored row must dump as it was read, with the key no model declares.
    assert sent["gateways"][0] == stored_row
    assert sent["gateways"][1] == {"logicalId": u(0x99)}
    assert sent["gatewayList"] == HANDOFF["gatewayList"]


PREFIXED_ROW: dict[str, Any] = {
    "logicalId": f"gateway{u(0x55)}",
    "ipAddress": "10.0.0.9",
    "ipV6Address": "2001:db8::9",
}


def controllers_with(row: dict[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(PROFILE_DS)
    raw["segments"][0]["handOffControllers"]["gateways"] = [copy.deepcopy(row)]
    return raw


def test_a_controllers_row_with_a_prefixed_id_loads_and_is_sent_as_read() -> None:
    raw = controllers_with(PREFIXED_ROW)
    for module in both_ways(DeviceSettingsConfigurationModule, "deviceSettings", raw):
        controllers = module.data.segments[0].handoff_controllers
        assert isinstance(controllers, SegmentHandoffGateways)
        assert [g.logical_id for g in controllers.gateways] == [
            PREFIXED_ROW["logicalId"]
        ]
        assert_untouched(module, raw, PROFILE)


def test_a_row_appended_beside_a_prefixed_row_leaves_the_stored_row_as_read() -> None:
    module = device_settings(controllers_with(PREFIXED_ROW))
    controllers = module.data.segments[0].handoff_controllers
    assert isinstance(controllers, SegmentHandoffGateways)
    row_class = type(controllers.gateways[0])
    controllers.gateways.append(row_class.model_validate({"logicalId": u(0x99)}))
    data = plan_module(module, level=PROFILE).data
    sent = data["segments"][0]["handOffControllers"]["gateways"]
    assert sent == [PREFIXED_ROW, {"logicalId": u(0x99)}]


@pytest.mark.parametrize("stored", [None, 5])
def test_a_gateways_row_whose_id_is_not_text_is_a_validation_error(
    stored: object,
) -> None:
    raw = controllers_with({**PREFIXED_ROW, "logicalId": stored})
    with pytest.raises(ValidationError, match="logicalId"):
        _ = device_settings(raw)


def test_the_gateway_list_is_unchanged_by_no_edit_and_by_an_append() -> None:
    module = device_settings(PROFILE_DS)
    assert_untouched(module, PROFILE_DS, PROFILE)
    handoff = module.data.segments[0].handoff_gateways
    assert isinstance(handoff, SegmentHandoffGateways)
    handoff.gateway_list.append(
        type(handoff.gateway_list[0]).model_validate(
            {"logicalId": f"gateway{u(0x98)}", "ipsecDetail": {}}
        )
    )
    sent = plan_module(module, level=PROFILE).data["segments"][0]["handOffGateways"]
    assert sent["gatewayList"] == [
        *HANDOFF["gatewayList"],
        {"logicalId": f"gateway{u(0x98)}", "ipsecDetail": {}},
    ]
    assert sent["gateways"] == HANDOFF["gateways"]


@pytest.mark.parametrize("stored", [None, 5, [], {}])
def test_a_gateway_id_that_is_not_text_is_a_validation_error(stored: object) -> None:
    with pytest.raises(ValidationError, match="a gateway's logical ID is text"):
        _ = GatewayListItem.model_validate({"logicalId": stored})


def cellular_of(module: DeviceSettingsConfigurationModule) -> RoutedInterfaceCellular:
    interfaces = module.data.routed_interfaces
    assert interfaces is not None
    interface = interfaces[0]
    assert isinstance(interface, RoutedInterfaceCell)
    cellular = interface.cellular
    assert isinstance(cellular, RoutedInterfaceCellular)
    return cellular


def test_a_cellular_interface_s_ip_type_is_read_and_written_at_iptype() -> None:
    for module in both_ways(
        DeviceSettingsConfigurationModule, "deviceSettings", EDGE_DS
    ):
        assert cellular_of(module).ip_type == "IPv4v6"
    module = device_settings(EDGE_DS)
    cellular_of(module).ip_type = "IPv4"
    sent = plan_module(module, level=EDGE).data["routedInterfaces"][0]["cellular"]
    assert sent == {**CELLULAR, "iptype": "IPv4"}


def v2c_of(module: DeviceSettingsConfigurationModule) -> EdgeSnmpV2c:
    return require_present(module.data.snmp).v2c


def test_the_snmp_allowed_addresses_are_read_and_written_at_allowed_ip() -> None:
    for module in both_ways(
        DeviceSettingsConfigurationModule, "deviceSettings", PROFILE_DS
    ):
        assert v2c_of(module).allowed_ips == ["10.0.0.9"]
    module = device_settings(PROFILE_DS)
    v2c_of(module).allowed_ips.append("10.0.0.1")
    plan = plan_module(module, level=PROFILE)
    expected = copy.deepcopy(PROFILE_DS)
    expected["snmp"]["snmpv2c"]["allowedIp"] = ["10.0.0.9", "10.0.0.1"]
    assert plan.data == expected


def test_a_profile_that_stores_the_key_this_library_wrote_keeps_it_as_read() -> None:
    raw = copy.deepcopy(PROFILE_DS)
    raw["snmp"]["snmpv2c"]["allowedIps"] = []  # beside the stored key
    for module in both_ways(DeviceSettingsConfigurationModule, "deviceSettings", raw):
        assert v2c_of(module).allowed_ips == ["10.0.0.9"]
        assert_untouched(module, raw, PROFILE)
    module = device_settings(raw)
    require_present(module.data.snmp).port = 162
    expected = copy.deepcopy(raw)
    expected["snmp"]["port"] = 162
    assert plan_module(module, level=PROFILE).data == expected


# --- firewall ----------------------------------------------------------------

INBOUND_V6: dict[str, Any] = {
    "name": "in6",
    "match": {
        "dipV6": "2001:db8::1",
        "dport_low": 443,
        "dport_high": 443,
        "proto": 6,
        "appid": -1,
        "classid": -1,
        "dscp": -1,
        "dvlan": -1,
        "hostname": "",
    },
    "action": {
        "type": "port_forwarding",
        "nat": {"lan_ip": "fd00::10", "lan_port": 443},
        "interface": "GE3",
    },
}
FIREWALL: dict[str, Any] = {"segments": [], "inboundV6": [INBOUND_V6]}


def test_an_ipv6_inbound_rule_s_destination_is_read_at_dip_v6() -> None:
    for module in both_ways(FirewallConfigurationModule[Edge], "firewall", FIREWALL):
        (rule,) = module.data.inbound_v6
        assert rule.match.destination_ip_v6 == "2001:db8::1"
        assert_untouched(module, FIREWALL, EDGE)


# --- enterprise rows ----------------------------------------------------------


def test_an_edge_link_s_backup_state_is_read() -> None:
    link = EdgeLink.model_validate({"id": 7, "backupState": "UNCONFIGURED"})
    assert link.back_up_state == "UNCONFIGURED"
    assert link.model_dump(mode="json", by_alias=True, exclude_unset=True) == {
        "id": 7,
        "backupState": "UNCONFIGURED",
    }


def test_a_data_center_s_load_sharing_hash_is_read_whole() -> None:
    stored = {
        "algo": "HASH_BASED",
        "hashParams": {
            "srcIp": True,
            "destIp": True,
            "srcPort": False,
            "destPort": False,
            "protocol": False,
        },
    }
    method = LoadSharingMethod.model_validate(stored)
    params = require_present(method.hash_params)
    assert (params.source_ip, params.source_port) == (True, False)
    assert method.model_dump(mode="json", by_alias=True) == stored
