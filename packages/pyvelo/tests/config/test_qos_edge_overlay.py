"""Edge QOS overlay insert matches the VCO UI contract.

When the edge has no QOS module, insertConfigurationModule must not clone
the profile catalog into ``rules``. Profile rules become inheritedRules;
the new overlay starts with empty rules so later patches add overrides.
"""

import copy
from typing import Any

from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    InsertQosConfigurationModule,
)
from pyvelo.config import add
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.qos_overlay import qos_overlay_data
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigState
from pyvelo.testing import MockVcoClient, make_module_json


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


def qos_rule(name: str, *, appid: int = -1, proto: int = -1) -> dict[str, Any]:
    return {
        "name": name,
        "match": {"appid": appid, "proto": proto},
        "action": {
            "edge2EdgeRouteAction": {},
            "edge2DataCenterRouteAction": {},
            "edge2CloudRouteAction": {},
            "QoS": {"type": "bulk"},
        },
    }


PROFILE_QOS: dict[str, Any] = {
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "rules": [qos_rule("Box", appid=767), qos_rule("Speedtest", appid=1465)],
            "defaults": [qos_rule("Default-Internet-UDP", proto=17)],
        }
    ]
}

EDGE_FW: dict[str, Any] = {
    "segments": [{"segment": desc(0, "Global Segment"), "outbound": []}],
    "syslog_forwarding": False,
}

_RTSP_PATCH = {
    "name": "RTSP-RTP",
    "match": {"appid": 175},
    "action": {
        "route_type": "edge2Any",
        "edge2EdgeRouteAction": {
            "route_policy": "auto",
            "link_policy": "preferred",
            "service_group": "PRIVATE_WIRED",
            "link_outer_dscp_tag": "CS0",
        },
        "edge2DataCenterRouteAction": {
            "route_policy": "auto",
            "link_policy": "preferred",
            "service_group": "PRIVATE_WIRED",
            "link_outer_dscp_tag": "CS0",
        },
        "edge2CloudRouteAction": {
            "route_policy": "gateway",
            "link_policy": "preferred",
            "service_group": "PRIVATE_WIRED",
            "link_outer_dscp_tag": "CS0",
        },
        "QoS": {
            "type": "realtime",
            "rx_scheduler": {"priority": "high"},
            "tx_scheduler": {"priority": "high"},
        },
        "sla": {"latency_ms": "0", "jitter_ms": "0", "loss_pct": "0.0"},
    },
}


def make_state(*, edge_has_qos: bool = False) -> ConfigState:
    profile_data = copy.deepcopy(PROFILE_QOS)
    edge_rows: list[dict[str, Any]] = [
        make_module_json(
            "firewall",
            copy.deepcopy(EDGE_FW),
            module_id=1,
            configuration_id=11,
            version="e1",
        )
    ]
    if edge_has_qos:
        overlay = qos_overlay_data(
            {
                "segments": [
                    {"segment": profile_data["segments"][0]["segment"], "rules": []}
                ]
            },
            profile_data,
        )
        edge_rows.append(
            make_module_json(
                "QOS", overlay, module_id=78, configuration_id=11, version="e2"
            )
        )
    edge = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "id": 11, "modules": edge_rows}
    )
    profile = EnterpriseConfigurationProfile.model_validate(
        {
            "name": "DMPO-Profile",
            "id": 9,
            "modules": [
                make_module_json(
                    "firewall",
                    copy.deepcopy(EDGE_FW),
                    module_id=2,
                    configuration_id=9,
                ),
                make_module_json("QOS", profile_data, module_id=63, configuration_id=9),
            ],
        }
    )
    state = ConfigState(
        edge_config=edge,
        profile_config=profile,
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=1),
    )
    state.take_snapshot()
    return state


def test_overlay_moves_catalog_out_of_rules() -> None:
    profile_data = copy.deepcopy(PROFILE_QOS)
    profile_data["segments"][0]["futureField"] = {"rawValue": "1.00"}
    overlay = qos_overlay_data(
        {
            "segments": [
                {"segment": profile_data["segments"][0]["segment"], "rules": []}
            ]
        },
        profile_data,
    )

    seg = overlay["segments"][0]
    assert seg["rules"] == []
    assert [r["name"] for r in seg["inheritedRules"]] == ["Box", "Speedtest"]
    assert [r["name"] for r in seg["inheritedDefaults"]] == ["Default-Internet-UDP"]
    assert seg["defaults"] == []
    assert seg["cosMappingOverride"] is False
    assert seg["futureField"] == {"rawValue": "1.00"}
    assert [r["name"] for r in profile_data["segments"][0]["rules"]] == [
        "Box",
        "Speedtest",
    ]


async def test_save_inserts_overlay_not_cloned_catalog() -> None:
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)

    state = await pipeline.apply(
        state,
        module="qos",
        patch={
            "segments": {"Global Segment": {"rules": {"RTSP-RTP": add(_RTSP_PATCH)}}}
        },
    )
    planned = state.plan().changed[0].data
    result = await pipeline.save(state)

    assert ConfigurationModuleName.QOS in result.modules_inserted
    (request,) = client.insert_requests
    assert isinstance(request, InsertQosConfigurationModule)
    assert request.configuration_id == 11
    assert request.profile_configuration_id == 9
    assert request.profile_module_id == 63
    assert request.profile_data is not MISSING
    assert request.data == planned

    payload = request.model_dump(by_alias=True)
    wire_seg = payload["data"]["segments"][0]
    assert [r["name"] for r in wire_seg["rules"]] == ["RTSP-RTP"]
    assert [r["name"] for r in wire_seg["inheritedRules"]] == ["Box", "Speedtest"]
    assert "inheritedDefaults" in wire_seg
    assert payload["profileConfigurationId"] == 9
    assert payload["profileModuleId"] == 63
    assert "profileData" in payload
    assert payload["data"].get("serviceRateLimitOverride") is False
    assert payload["data"].get("qosClientConnectorOverride") is False
    assert payload["data"].get("customizableQOEOverride") is False
    assert wire_seg.get("cosMappingOverride") is False
    assert wire_seg["rules"][0]["action"]["sla"]["lossPct"] == "0.0"
    assert "loss_pct" not in wire_seg["rules"][0]["action"]["sla"]
    assert wire_seg["rules"][0]["ruleLogicalId"]

    assert state.edge_config is not None
    assert state.edge_config.qos.id_ is not None


async def test_existing_edge_qos_updates_without_recloning_catalog() -> None:
    state = make_state(edge_has_qos=True)
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)

    state = await pipeline.apply(
        state,
        module="qos",
        patch={
            "segments": {"Global Segment": {"rules": {"RTSP-RTP": add(_RTSP_PATCH)}}}
        },
    )
    result = await pipeline.save(state)

    assert ConfigurationModuleName.QOS in result.modules_updated
    assert result.modules_inserted == []
    (request,) = client.update_requests
    payload = request.update.data
    seg = payload["segments"][0]
    assert [r["name"] for r in seg["rules"]] == ["RTSP-RTP"]
    assert [r["name"] for r in seg["inheritedRules"]] == ["Box", "Speedtest"]
