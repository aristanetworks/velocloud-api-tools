"""Edge QOS overlay insert matches the VCO UI contract.

When the edge has no QOS module, insertConfigurationModule must not clone
the profile catalog into ``rules``. Profile rules become inheritedRules;
the new overlay starts with empty rules so later patches add overrides.
"""

from datetime import UTC, datetime

import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import (
    ConfigurationModuleName,
    ConfigurationModuleType,
)
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.models.configuration.qos.segment import CosMapping, QosSegment
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    InsertQosConfigurationModule,
    QosConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo_intent.config.pipeline import ConfigPipeline, _overlay_qos_from_profile
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient, make_firewall_module

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _qos_rule(name: str, *, appid: int = -1, proto: int = -1) -> QosRule:
    return QosRule.model_validate(
        {
            "name": name,
            "match": {"appid": appid, "proto": proto},
            "action": {
                "edge2EdgeRouteAction": {},
                "edge2DataCenterRouteAction": {},
                "edge2CloudRouteAction": {},
                "QoS": {"type": "bulk"},
            },
        }
    )


def _make_profile_qos_module() -> QosConfigurationModule:
    segment = QosSegment(
        segment=SegmentMetadata(
            name="Global Segment",
            segment_id=0,
            type_=NetworkSegmentType.REGULAR,
        ),
        rules=[_qos_rule("Box", appid=767), _qos_rule("Speedtest", appid=1465)],
        defaults=[_qos_rule("Default-Internet-UDP", proto=17)],
        cos_mapping=CosMapping(),
    )
    return QosConfigurationModule(
        name="QOS",
        data=QosData(segments=[segment]),
        created=_TS,
        modified=_TS,
        id_=63,
        type_=ConfigurationModuleType.ENTERPRISE,
        description=None,
        configuration_id=9,
        enterprise_logical_id=None,
        schema_version=None,
        version=None,
        metadata=None,
    )


def _make_state(*, edge_has_qos: bool = False) -> ConfigState:
    profile_qos = _make_profile_qos_module()
    edge_modules = [make_firewall_module(mod_id=1, config_id=11)]
    if edge_has_qos:
        overlay = _overlay_qos_from_profile(profile_qos)
        overlay.id_ = 78
        overlay.configuration_id = 11
        edge_modules.append(overlay)
    edge_config = EdgeConfiguration(
        name="Edge Specific Profile",
        id_=11,
        modules=edge_modules,
    )
    profile_config = EnterpriseConfigurationProfile(
        name="DMPO-Profile",
        id_=9,
        modules=[
            make_firewall_module(mod_id=2, config_id=9),
            profile_qos,
        ],
    )
    state = ConfigState(
        edge_config=edge_config,
        profile_config=profile_config,
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=1),
    )
    state.take_snapshot()
    return state


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


def test_overlay_moves_catalog_out_of_rules():
    template = _make_profile_qos_module()
    overlay = _overlay_qos_from_profile(template)

    seg = overlay.data.get_segment(name="Global Segment")
    assert [r.name for r in seg.rules] == []
    assert [r.name for r in seg.inherited_rules] == ["Box", "Speedtest"]
    assert [r.name for r in seg.inherited_defaults] == ["Default-Internet-UDP"]
    assert seg.defaults == []
    assert seg.cos_mapping_override is False
    # Profile template is unchanged.
    profile_seg = template.data.get_segment(name="Global Segment")
    assert [r.name for r in profile_seg.rules] == ["Box", "Speedtest"]


@pytest.mark.asyncio
async def test_save_inserts_overlay_not_cloned_catalog():
    client = MockVcoClient()
    pipeline = ConfigPipeline(client)
    state = _make_state()

    state = await pipeline.apply(
        state,
        module="qos",
        patch={"segments": {"Global Segment": {"rules": {"RTSP-RTP": _RTSP_PATCH}}}},
    )
    result = await pipeline.save(state)

    assert ConfigurationModuleName.QOS in result.modules_inserted
    assert client.calls and client.calls[0][0] == "insert_configuration_module"
    request = client.calls[0][1]
    assert isinstance(request, InsertQosConfigurationModule)
    assert request.configuration_id == 11
    assert request.profile_configuration_id == 9
    assert request.profile_module_id == 63
    assert request.profile_data is not MISSING

    seg = request.data.get_segment(name="Global Segment")
    assert [r.name for r in seg.rules] == ["RTSP-RTP"]
    assert [r.name for r in seg.inherited_rules] == ["Box", "Speedtest"]
    assert [r.name for r in seg.inherited_defaults] == ["Default-Internet-UDP"]
    assert seg.defaults == []
    assert seg.rules[0].rule_logical_id is None
    assert seg.rules[0].action.sla is not MISSING
    assert seg.rules[0].action.sla.loss_percent == "0.0"

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
    assert wire_seg["rules"][0]["ruleLogicalId"] is None

    assert state.edge_config is not None
    assert state.edge_config.qos.id_ == 1  # assigned from insert result


@pytest.mark.asyncio
async def test_existing_edge_qos_updates_without_recloning_catalog():
    client = MockVcoClient()
    pipeline = ConfigPipeline(client)
    state = _make_state(edge_has_qos=True)

    state = await pipeline.apply(
        state,
        module="qos",
        patch={"segments": {"Global Segment": {"rules": {"RTSP-RTP": _RTSP_PATCH}}}},
    )
    result = await pipeline.save(state)

    assert ConfigurationModuleName.QOS in result.modules_updated
    assert result.modules_inserted == []
    assert client.calls[0][0] == "update_configuration_module"

    assert state.edge_config is not None
    seg = state.edge_config.qos.data.get_segment(name="Global Segment")
    assert [r.name for r in seg.rules] == ["RTSP-RTP"]
    assert [r.name for r in seg.inherited_rules] == ["Box", "Speedtest"]
