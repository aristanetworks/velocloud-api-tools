"""Tests for monitoring schema typed models and enums."""

from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError
from pydantic_core import MISSING
from pyvelo.client.enums.event import EventType
from pyvelo.client.enums.monitoring import (
    BfdPeerState,
    BgpPeerState,
    DegradationDetailsSortAttribute,
    DegradationViewBy,
    DegradationWith,
    GatewayState,
    TunnelState,
    TunnelType,
)
from pyvelo.client.schemas.monitoring import (
    BfdPeerStatusRecord,
    BfdPeerTimer,
    BgpPeerData,
    ClusterEdgeEntry,
    DegradationDetailItem,
    DegradationSummaryItem,
    EdgeHealthStats,
    GatewayHealthStats,
    GetAggregateEnterpriseEventsResult,
    GetEnterpriseAggregateEdgeNvsTunnelStatusResultItem,
    GetEnterpriseBfdPeerStatusResultItem,
    GetEnterpriseBgpPeerStatusResultItem,
    GetEnterpriseEdgeBfdPeerStatusResultItem,
    GetEnterpriseEdgeBgpPeerStatusResultItem,
    GetEnterpriseEdgeClusterStatusResultItem,
    GetEnterpriseEdgeNvsTunnelStatusResultItem,
    GetEnterpriseEdgeStatusResultItem,
    GetLinkDegradationDetails,
    GetLinkDegradationDetailsResult,
    GetLinkDegradationSummary,
    GetLinkDegradationSummaryResult,
    GetLinkFlapSummary,
    GetLinkFlapSummaryResult,
    GetLinkInsightsOverview,
    GetLinkInsightsOverviewResult,
    GetNetworkGatewayStatus,
    GetNetworkGatewayStatusResultItem,
    GetVpnSiteStatusResultItem,
    MetricSummary,
    NniIdentifier,
    NvsDataCenter,
    TunnelStateCounts,
)

from ._type_support import require_present

_INTERVAL = {"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:00Z"}


class TestGetNetworkGatewayStatusRequest:
    def test_accepts_epoch_milliseconds(self):
        request = GetNetworkGatewayStatus.model_validate(
            {"networkId": 1, "time": 1789076451863}
        )

        assert request.time == 1789076451863
        schema = GetNetworkGatewayStatus.model_json_schema(by_alias=True)
        assert {"type": "integer", "minimum": 1} in schema["properties"]["time"][
            "anyOf"
        ]

    def test_accepts_iso_timestamp(self):
        request = GetNetworkGatewayStatus.model_validate(
            {"networkId": 1, "time": "2026-09-10T21:40:51.863Z"}
        )

        assert request.time == datetime(2026, 9, 10, 21, 40, 51, 863000, tzinfo=UTC)

    def test_accepts_vco_http_timestamp(self):
        request = GetNetworkGatewayStatus.model_validate(
            {
                "networkId": 1,
                "time": "Thu Sep 10 2026 21:40:51 GMT+0000 (UTC)",
            }
        )

        assert request.time == datetime(2026, 9, 10, 21, 40, 51, tzinfo=UTC)

    @pytest.mark.parametrize(
        "time",
        [
            "7d",
            {"start": 1788471651863, "end": 1789076451863},
            0,
            -1,
        ],
    )
    def test_rejects_duration_and_interval(self, time):
        with pytest.raises(ValidationError):
            GetNetworkGatewayStatus.model_validate({"networkId": 1, "time": time})


class TestGetVpnSiteStatusResult:
    def test_preserves_vco_deployment_status_fields(self):
        item = GetVpnSiteStatusResultItem.model_validate(
            {
                "id": 42,
                "action": "CREATE_SITE",
                "actionData": '{"site":"branch-1"}',
                "status": 2,
                "statusMsg": "COMPLETE",
                "vendorType": "genericIKEv1Router",
                "edgeId": 7,
                "providerId": 11,
            }
        )

        assert item.id_ == 42
        assert item.action == "CREATE_SITE"
        assert item.action_data == '{"site":"branch-1"}'
        assert item.status == 2
        assert item.status_msg == "COMPLETE"
        assert item.vendor_type == "genericIKEv1Router"
        assert item.edge_id == 7
        assert item.provider_id == 11

        assert item.model_dump(by_alias=True, exclude_unset=True) == {
            "id": 42,
            "action": "CREATE_SITE",
            "actionData": '{"site":"branch-1"}',
            "status": 2,
            "statusMsg": "COMPLETE",
            "vendorType": "genericIKEv1Router",
            "edgeId": 7,
            "providerId": 11,
        }

    def test_retains_legacy_attributes_for_source_compatibility(self):
        item = GetVpnSiteStatusResultItem.model_validate(
            {
                "siteId": 3,
                "siteName": "legacy-site",
                "state": "UP",
                "linkId": 17,
            }
        )

        assert item.model_dump(by_alias=True, exclude_unset=True) == {
            "siteId": 3,
            "siteName": "legacy-site",
            "state": "UP",
            "linkId": 17,
        }
        schema = GetVpnSiteStatusResultItem.model_json_schema(by_alias=True)
        assert schema["properties"]["siteId"]["deprecated"] is True
        assert schema["properties"]["siteName"]["deprecated"] is True
        assert schema["properties"]["state"]["deprecated"] is True
        assert schema["properties"]["linkId"]["deprecated"] is True


# --- Enum string identity tests ---


class TestEnumStringIdentity:
    def test_bgp_peer_state(self):
        assert BgpPeerState("ESTABLISHED") == "ESTABLISHED"
        assert BgpPeerState("IDLE") == "IDLE"
        assert BgpPeerState("ACTIVE") == "ACTIVE"
        assert BgpPeerState("REMOVED") == "REMOVED"

    def test_bfd_peer_state(self):
        assert BfdPeerState("UP") == "UP"
        assert BfdPeerState("DOWN") == "DOWN"
        assert BfdPeerState("INIT") == "INIT"
        assert BfdPeerState("SHUTDOWN") == "SHUTDOWN"
        assert BfdPeerState("UNKNOWN") == "UNKNOWN"

    def test_tunnel_state(self):
        assert TunnelState("UP") == "UP"
        assert TunnelState("DOWN") == "DOWN"

    def test_tunnel_type(self):
        assert TunnelType("IPSEC") == "IPSEC"
        assert TunnelType("GRE") == "GRE"

    def test_gateway_state(self):
        assert GatewayState("CONNECTED") == "CONNECTED"
        assert GatewayState("OFFLINE") == "OFFLINE"

    def test_degradation_view_by(self):
        assert DegradationViewBy("link") == "link"
        assert DegradationViewBy("edge") == "edge"
        assert DegradationViewBy("isp") == "isp"

    @pytest.mark.parametrize(
        "event_type",
        [
            EventType.LINK_FLAPPING,
            EventType.LINK_SEVERE_DEGRADATION,
            EventType.LINK_STATE_TRANSITION,
        ],
    )
    def test_link_insights_event_types(self, event_type):
        assert EventType(event_type.value) is event_type


# --- MetricSummary ---


class TestMetricSummary:
    def test_round_trip(self):
        data = {"min": 1.0, "max": 99.5, "average": 42.3}
        summary = MetricSummary.model_validate(data)
        assert summary.min_ == 1.0
        assert summary.max_ == 99.5
        assert summary.average == 42.3
        dumped = summary.model_dump(by_alias=True)
        assert dumped["min"] == 1.0
        assert dumped["max"] == 99.5
        assert dumped["average"] == 42.3

    def test_partial_fields(self):
        summary = MetricSummary.model_validate({"average": 50.0})
        assert summary.min_ is MISSING
        assert summary.max_ is MISSING
        assert summary.average == 50.0


# --- EdgeHealthStats ---


class TestEdgeHealthStats:
    def test_deserialization(self):
        raw = {
            "cpuPct": {"min": 5.0, "max": 80.0, "average": 35.0},
            "memoryPct": {"min": 20.0, "max": 60.0, "average": 40.0},
            "tunnelCount": {"min": 1.0, "max": 10.0, "average": 5.0},
        }
        stats = EdgeHealthStats.model_validate(raw)
        assert require_present(stats.cpu_pct).average == 35.0
        assert require_present(stats.memory_pct).max_ == 60.0
        assert require_present(stats.tunnel_count).min_ == 1.0

    def test_extra_allow_captures_unknown(self):
        raw = {
            "cpuPct": {"min": 5.0, "max": 80.0, "average": 35.0},
            "futureMetric": {"min": 0.0, "max": 100.0, "average": 50.0},
        }
        stats = EdgeHealthStats.model_validate(raw)
        assert require_present(stats.cpu_pct).average == 35.0
        assert stats.model_extra is not None
        assert "futureMetric" in stats.model_extra


# --- Backwards compatibility: raw strings coerce to enums ---


class TestBackwardsCompatibility:
    def test_edge_state_coerces_from_string(self):
        item = GetEnterpriseEdgeStatusResultItem.model_validate(
            {"edgeState": "CONNECTED", "haState": "READY"}
        )
        assert item.edge_state == "CONNECTED"
        assert item.ha_state == "READY"

    def test_bgp_peer_state_coerces(self):
        record = BgpPeerData.model_validate(
            {"state": "ESTABLISHED", "neighborIp": "10.0.0.1"}
        )
        assert record.state == "ESTABLISHED"

    def test_bfd_peer_state_coerces(self):
        item = GetEnterpriseEdgeBfdPeerStatusResultItem.model_validate(
            {"state": "UP", "peerAddress": "10.0.0.1"}
        )
        assert item.state == "UP"

    def test_tunnel_state_coerces(self):
        item = GetEnterpriseEdgeNvsTunnelStatusResultItem.model_validate(
            {
                "state": "UP",
                "data": {
                    "name": "test-tunnel",
                    "link": "00000000-0000-0000-0000-000000000001",
                    "nvs": "00000000-0000-0000-0000-000000000002",
                    "state": "UP",
                },
                "dataKey": "test-key",
                "edgeName": "test-edge",
                "edgeId": 1,
                "edgeLogicalId": "00000000-0000-0000-0000-000000000003",
                "tag": "EDGE_NVS_TUNNEL",
                "timestamp": "2024-01-01T00:00:00.000Z",
            }
        )
        assert item.state == "UP"

    def test_gateway_state_coerces(self):
        item = GetNetworkGatewayStatusResultItem.model_validate(
            {"gatewayState": "CONNECTED"}
        )
        assert item.gateway_state == "CONNECTED"

    def test_gateway_status_preserves_flat_health_metrics(self):
        item = GetNetworkGatewayStatusResultItem.model_validate(
            {
                "gatewayId": 2,
                "tunnelCount": 12,
                "tunnelCountV6": 3,
                "memoryPct": 42.5,
                "flowCount": 900,
                "cpuPct": 17.25,
                "handoffQueueDrops": 4,
                "connectedEdges": 8.5,
            }
        )

        assert item.gateway_id == 2
        assert item.tunnel_count == 12
        assert item.tunnel_count_v6 == 3
        assert item.memory_pct == 42.5
        assert item.flow_count == 900
        assert item.cpu_pct == 17.25
        assert item.handoff_queue_drops == 4
        assert item.connected_edges == 8.5


# --- Edge BGP peer status (event-based response) ---


class TestEdgeBgpPeerStatus:
    """Tests for the event-based BGP peer status response from VCO."""

    SAMPLE_EVENT: ClassVar[dict[str, Any]] = {
        "id": 1,
        "timestamp": "2025-01-02T03:04:05.000Z",
        "enterpriseId": 1,
        "edgeId": 2,
        "enterpriseObjectId": None,
        "tag": "BGP_NEIGHBOR_SUMMARY",
        "dataKey": "198.51.100.20",
        "state": "ESTABLISHED",
        "data": {
            "enterpriseLogicalId": "00000000-0000-4000-8000-000000000001",
            "segmentId": 0,
            "neighborIp": "198.51.100.20",
            "neighborAS": "64512",
            "msgRcvd": "12",
            "msgSent": "8",
            "upDownTime": "00:05:00",
            "state": "ESTABLISHED",
            "pfxRcvd": "3",
        },
        "edgeName": "Example-Edge",
        "edgeLogicalId": "00000000-0000-4000-8000-000000000002",
        "edgeState": "CONNECTED",
    }

    def test_full_event_round_trip(self):
        item = GetEnterpriseEdgeBgpPeerStatusResultItem.model_validate(
            self.SAMPLE_EVENT
        )
        assert item.id_ == 1
        assert item.timestamp == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
        assert item.enterprise_id == 1
        assert item.edge_id == 2
        assert item.tag == "BGP_NEIGHBOR_SUMMARY"
        assert item.data_key == "198.51.100.20"
        assert item.state == BgpPeerState.ESTABLISHED
        assert item.edge_name == "Example-Edge"
        assert item.edge_state is not None

    def test_nested_data_fields(self):
        # SAMPLE_EVENT carries the historical quoted-int form ("94", "93", "2");
        # CoerceInt normalizes them to real ints on the way in.
        item = GetEnterpriseEdgeBgpPeerStatusResultItem.model_validate(
            self.SAMPLE_EVENT
        )
        data = require_present(item.data)
        assert data is not None
        assert data.neighbor_ip == "198.51.100.20"
        assert data.neighbor_as == "64512"  # stays a string; ASNs may be dotted
        assert data.msg_rcvd == 12
        assert data.msg_sent == 8
        assert data.up_down_time == "00:05:00"
        assert data.state == BgpPeerState.ESTABLISHED
        assert data.pfx_rcvd == 3
        assert data.segment_id == 0

    def test_int_form_data_validates_identically(self):
        # Regression: counters may arrive as JSON numbers or quoted integers.
        # Both wire representations should normalize to the same integer values.
        int_event = {
            **self.SAMPLE_EVENT,
            "data": {
                **self.SAMPLE_EVENT["data"],
                "msgRcvd": 12,
                "msgSent": 8,
                "pfxRcvd": 3,
            },
        }
        str_item = GetEnterpriseEdgeBgpPeerStatusResultItem.model_validate(
            self.SAMPLE_EVENT
        )
        int_item = GetEnterpriseEdgeBgpPeerStatusResultItem.model_validate(int_event)
        str_data = require_present(str_item.data)
        int_data = require_present(int_item.data)
        assert str_data is not None and int_data is not None
        assert str_data.msg_rcvd == int_data.msg_rcvd == 12
        assert str_data.msg_sent == int_data.msg_sent == 8
        assert str_data.pfx_rcvd == int_data.pfx_rcvd == 3

    def test_bgp_peer_data_standalone(self):
        data = BgpPeerData.model_validate(
            {
                "neighborIp": "10.0.0.1",
                "neighborAS": "65000",
                "upDownTime": "4d13h12m",
                "state": "IDLE",
                "pfxRcvd": 0,
            }
        )
        assert data.neighbor_ip == "10.0.0.1"
        assert data.neighbor_as == "65000"
        assert data.up_down_time == "4d13h12m"
        assert data.state == BgpPeerState.IDLE
        assert data.pfx_rcvd == 0

    def test_minimal_event(self):
        item = GetEnterpriseEdgeBgpPeerStatusResultItem.model_validate(
            {"state": "ESTABLISHED", "dataKey": "10.0.0.1"}
        )
        assert item.state == BgpPeerState.ESTABLISHED
        assert item.data_key == "10.0.0.1"
        assert item.data is MISSING


# --- Aggregate sub-model tests ---


class TestAggregateSubModels:
    def test_tunnel_state_counts(self):
        counts = TunnelStateCounts.model_validate({"up": 5, "down": 1, "unknown": 0})
        assert counts.up == 5
        assert counts.down == 1
        assert counts.unknown == 0

    def test_nvs_data_center(self):
        dc = NvsDataCenter.model_validate({"name": "US-West", "publicIp": "1.2.3.4"})
        assert dc.name == "US-West"
        assert dc.public_ip == "1.2.3.4"

    def test_aggregate_nvs_tunnel_status(self):
        raw = {
            "id": 42,
            "name": "Zscaler",
            "tunnelStateCounts": {"up": 3, "down": 0, "unknown": 1},
            "dataCenters": [{"name": "DC1", "publicIp": "5.6.7.8"}],
        }
        item = GetEnterpriseAggregateEdgeNvsTunnelStatusResultItem.model_validate(raw)
        assert item.tunnel_state_counts is not None
        assert item.data_centers is not None
        assert require_present(item.tunnel_state_counts).up == 3
        assert require_present(item.data_centers)[0].name == "DC1"

    def test_aggregate_events_result(self):
        raw = {
            "data": [
                {
                    "event": "EDGE_DOWN",
                    "category": "SYSTEM",
                    "severity": "CRITICAL",
                    "message": "Edge went offline",
                }
            ],
            "metaData": {"limit": 100, "more": True},
        }
        result = GetAggregateEnterpriseEventsResult.model_validate(raw)
        data = require_present(result.data)
        meta_data = require_present(result.meta_data)
        assert data is not None
        assert meta_data is not None
        assert data[0].event == "EDGE_DOWN"
        assert meta_data.more is True
        assert meta_data.limit == 100

    def test_nni_identifier(self):
        nni = NniIdentifier.model_validate({"gatewayId": "123", "nniId": "456"})
        assert nni.gateway_id == "123"
        assert nni.nni_id == "456"


# --- Gateway health stats ---


class TestGatewayHealthStats:
    def test_deserialization(self):
        raw = {
            "cpuPct": {"min": 10.0, "max": 90.0, "average": 50.0},
            "connectedEdges": {"min": 5.0, "max": 100.0, "average": 55.0},
        }
        stats = GatewayHealthStats.model_validate(raw)
        assert require_present(stats.cpu_pct).average == 50.0
        assert require_present(stats.connected_edges).max_ == 100.0

    def test_gateway_status_with_health_stats(self):
        raw = {
            "id": 1,
            "gatewayState": "CONNECTED",
            "healthStats": {
                "cpuPct": {"min": 10.0, "max": 90.0, "average": 50.0},
            },
        }
        item = GetNetworkGatewayStatusResultItem.model_validate(raw)
        assert item.gateway_state == "CONNECTED"
        assert item.health_stats is not None
        health_stats = require_present(item.health_stats)
        assert health_stats is not None
        assert require_present(health_stats.cpu_pct).average == 50.0


# --- Enterprise BGP peer status (gateway enterprise record) ---


class TestEnterpriseBgpPeerStatus:
    SAMPLE_RECORD: ClassVar[dict[str, Any]] = {
        "id": 3,
        "enterpriseId": 1,
        "gatewayName": "Example-Gateway",
        "gatewayId": 10,
        "gatewayLogicalId": "00000000-0000-4000-8000-000000000010",
        "gatewayState": "CONNECTED",
        "principle": "GATEWAY",
        "enterpriseObjectId": None,
        "timestamp": "2025-01-02T03:04:05.000Z",
        "tag": "BGP_NEIGHBOR_SUMMARY",
        "dataKey": "198.51.100.1",
        "state": "ESTABLISHED",
        "data": {
            "enterpriseLogicalId": "00000000-0000-4000-8000-000000000001",
            "segmentId": 0,
            "neighborIp": "198.51.100.1",
            "neighborAS": "64513",
            "msgRcvd": "12",
            "msgSent": "8",
            "upDownTime": "00:05:00",
            "state": "ESTABLISHED",
            "pfxRcvd": "3",
        },
    }

    def test_full_record(self):
        item = GetEnterpriseBgpPeerStatusResultItem.model_validate(self.SAMPLE_RECORD)
        assert item.id_ == 3
        assert item.gateway_name == "Example-Gateway"
        assert item.gateway_id == 10
        assert item.gateway_state == "CONNECTED"
        assert item.tag == "BGP_NEIGHBOR_SUMMARY"
        assert item.data_key == "198.51.100.1"
        assert item.state == BgpPeerState.ESTABLISHED

    def test_nested_data(self):
        # Sample uses the historical quoted-int form; CoerceInt normalizes.
        item = GetEnterpriseBgpPeerStatusResultItem.model_validate(self.SAMPLE_RECORD)
        data = require_present(item.data)
        assert data is not None
        assert data.neighbor_ip == "198.51.100.1"
        assert data.neighbor_as == "64513"
        assert data.msg_rcvd == 12
        assert data.pfx_rcvd == 3
        assert data.segment_id == 0


# --- Enterprise BFD peer status (gateway enterprise record) ---


class TestEnterpriseBfdPeerStatus:
    SAMPLE_RECORD: ClassVar[dict[str, Any]] = {
        "id": 4,
        "enterpriseId": 1,
        "gatewayName": "Example-Gateway",
        "gatewayId": 10,
        "gatewayLogicalId": "00000000-0000-4000-8000-000000000010",
        "gatewayState": "CONNECTED",
        "enterpriseObjectId": None,
        "timestamp": "2025-01-02T03:04:05.000Z",
        "tag": "BFD_NEIGHBOR_SUMMARY",
        "dataKey": "203.0.113.9",
        "state": "UP",
        "data": {
            "enterpriseLogicalId": "00000000-0000-4000-8000-000000000011",
            "segmentId": "0",
            "peerAddress": "203.0.113.9",
            "localAddress": "203.0.113.10",
            "state": "UP",
            "sessionTime": "1 minute(s), 0 second(s)",
            "localTimers": {"receive": "300ms", "transmit": "300ms"},
            "remoteTimers": {"receive": "300ms", "transmit": "300ms"},
        },
    }

    def test_full_record(self):
        item = GetEnterpriseBfdPeerStatusResultItem.model_validate(self.SAMPLE_RECORD)
        assert item.id_ == 4
        assert item.gateway_name == "Example-Gateway"
        assert item.gateway_id == 10
        assert item.tag == "BFD_NEIGHBOR_SUMMARY"
        assert item.data_key == "203.0.113.9"
        assert item.state == BfdPeerState.UP

    def test_nested_data(self):
        item = GetEnterpriseBfdPeerStatusResultItem.model_validate(self.SAMPLE_RECORD)
        data = require_present(item.data)
        assert data is not None
        assert data.peer_address == "203.0.113.9"
        assert data.local_address == "203.0.113.10"
        assert data.state == BfdPeerState.UP
        assert data.session_time == "1 minute(s), 0 second(s)"
        assert data.segment_id == "0"

    def test_bfd_timers(self):
        item = GetEnterpriseBfdPeerStatusResultItem.model_validate(self.SAMPLE_RECORD)
        data = require_present(item.data)
        assert data is not None
        local_timers = require_present(data.local_timers)
        remote_timers = require_present(data.remote_timers)
        assert local_timers is not None
        assert remote_timers is not None
        assert local_timers.receive == "300ms"
        assert local_timers.transmit == "300ms"
        assert remote_timers.receive == "300ms"

    def test_bfd_peer_status_record_standalone(self):
        record = BfdPeerStatusRecord.model_validate(
            {
                "peerAddress": "10.0.0.1",
                "localAddress": "10.0.0.2",
                "state": "DOWN",
                "segmentId": "1",
            }
        )
        assert record.peer_address == "10.0.0.1"
        assert record.state == BfdPeerState.DOWN
        assert record.session_time is MISSING

    def test_bfd_peer_timer(self):
        timer = BfdPeerTimer.model_validate({"receive": "100ms", "transmit": "100ms"})
        assert timer.receive == "100ms"
        assert timer.transmit == "100ms"


# --- Cluster status ---


class TestClusterStatus:
    SAMPLE_RESPONSE: ClassVar[list[dict[str, Any]]] = [
        {
            "clusterName": "Clone-Cluster-A",
            "clusterId": 70079,
            "edges": [
                {
                    "edgeId": 11729,
                    "edgeName": "Clone_nc93q-vp-st-igbn4",
                    "tunnelCount": 34,
                    "memoryPct": 28.9,
                    "flowCount": 1786,
                    "cpuPct": 2.9,
                    "cpuCoreTemp": 34,
                    "handoffQueueDrops": 0,
                    "tunnelCountV6": 0,
                    "routeCount": 0,
                    "routeCountV6": 0,
                    "natCount": 0,
                    "flowUtilPct": 0,
                    "tunnelUtilPct": 0,
                }
            ],
        },
        {
            "clusterName": "NetLab-Cluster",
            "clusterId": 71939,
            "edges": [],
        },
        {
            "clusterName": "Infra-170Lab-STL",
            "clusterId": 176440,
            "edges": [
                {
                    "edgeId": 25843,
                    "edgeName": "Infra 170 Lab STL Hub",
                    "tunnelCount": None,
                    "memoryPct": None,
                    "flowCount": None,
                    "cpuPct": None,
                    "cpuCoreTemp": None,
                    "handoffQueueDrops": None,
                    "tunnelCountV6": None,
                    "routeCount": None,
                    "routeCountV6": None,
                    "natCount": None,
                    "flowUtilPct": None,
                    "tunnelUtilPct": None,
                },
            ],
        },
    ]

    def test_full_response_round_trip(self):
        items = [
            GetEnterpriseEdgeClusterStatusResultItem.model_validate(r)
            for r in self.SAMPLE_RESPONSE
        ]
        assert len(items) == 3
        assert items[0].cluster_name == "Clone-Cluster-A"
        assert items[0].cluster_id == 70079
        assert items[1].edges == []

    def test_edge_entry_with_all_metrics(self):
        item = GetEnterpriseEdgeClusterStatusResultItem.model_validate(
            self.SAMPLE_RESPONSE[0]
        )
        edges = require_present(item.edges)
        assert edges is not None
        edge = edges[0]
        assert edge.edge_id == 11729
        assert edge.edge_name == "Clone_nc93q-vp-st-igbn4"
        assert edge.tunnel_count == 34
        assert edge.memory_pct == 28.9
        assert edge.flow_count == 1786
        assert edge.cpu_pct == 2.9
        assert edge.cpu_core_temp == 34
        assert edge.handoff_queue_drops == 0
        assert edge.tunnel_count_v6 == 0
        assert edge.route_count == 0
        assert edge.route_count_v6 == 0
        assert edge.nat_count == 0
        assert edge.flow_util_pct == 0
        assert edge.tunnel_util_pct == 0

    def test_edge_entry_with_null_metrics(self):
        item = GetEnterpriseEdgeClusterStatusResultItem.model_validate(
            self.SAMPLE_RESPONSE[2]
        )
        edges = require_present(item.edges)
        assert edges is not None
        edge = edges[0]
        assert edge.edge_id == 25843
        assert edge.tunnel_count is None
        assert edge.memory_pct is None
        assert edge.route_count is None
        assert edge.flow_util_pct is None

    def test_cluster_edge_entry_standalone(self):
        entry = ClusterEdgeEntry.model_validate(
            {
                "edgeId": 1384,
                "edgeName": "nc93-vp-igbn2",
                "tunnelCount": 3421,
                "memoryPct": 63,
                "flowCount": 32521,
                "cpuPct": 9,
                "cpuCoreTemp": 36,
                "handoffQueueDrops": 0,
                "tunnelCountV6": 0,
                "routeCount": 0,
                "routeCountV6": 0,
                "natCount": 0,
                "flowUtilPct": 0,
                "tunnelUtilPct": 0,
            }
        )
        assert entry.edge_id == 1384
        assert entry.tunnel_count == 3421
        assert entry.route_count == 0


# --- Link Insights schemas ---


class TestLinkInsightsOverview:
    SAMPLE: ClassVar[dict[str, Any]] = {
        "totalLinks": 42,
        "totalEdges": 12,
        "totalIsps": 5,
        "linkFlapping": {
            "affectedLinks": 3,
            "affectedEdges": 2,
            "affectedIsps": 1,
        },
        "linkDegradation": {
            "degradedLinks": 7,
            "degradedEdges": 4,
            "degradedIsps": 2,
        },
        "degradationBefore": {
            "totalMinutes": 120.5,
            "criticalMinutes": 45.0,
            "fairMinutes": 75.5,
        },
        "degradationAfter": {
            "totalMinutes": 30.0,
            "criticalMinutes": 5.0,
            "fairMinutes": 25.0,
        },
    }

    def test_parse_overview(self):
        result = GetLinkInsightsOverviewResult.model_validate(self.SAMPLE)
        assert result.total_links == 42
        assert result.total_edges == 12
        assert result.total_isps == 5
        link_flapping = require_present(result.link_flapping)
        link_degradation = require_present(result.link_degradation)
        degradation_before = require_present(result.degradation_before)
        degradation_after = require_present(result.degradation_after)
        assert link_flapping is not None
        assert link_degradation is not None
        assert degradation_before is not None
        assert degradation_after is not None
        assert link_flapping.affected_links == 3
        assert link_degradation.degraded_edges == 4
        assert degradation_before.total_minutes == 120.5
        assert degradation_after.critical_minutes == 5.0

    def test_overview_without_flapping(self):
        data = {**self.SAMPLE, "linkFlapping": None}
        result = GetLinkInsightsOverviewResult.model_validate(data)
        assert result.link_flapping is None

    def test_request_serialization(self):
        req = GetLinkInsightsOverview.model_validate(
            {
                "interval": {
                    "start": "2026-08-01T00:00:00Z",
                    "end": "2026-08-02T00:00:00Z",
                }
            }
        )
        dumped = req.model_dump(by_alias=True, exclude_unset=True)
        assert "interval" in dumped
        assert "enterpriseId" not in dumped


class TestLinkFlapSummary:
    SAMPLE_LINK_VIEW: ClassVar[dict[str, Any]] = {
        "data": [
            {
                "linkLogicalId": "abc-123",
                "edgeLogicalId": "def-456",
                "linkName": "GE2",
                "edgeName": "Boston-01",
                "interface": "GE2",
                "linkIpAddress": "10.0.0.1",
                "isp": "Comcast",
                "wanLinkType": "WIRED",
                "wanLinkMode": "PUBLIC",
                "totalFlaps": 7,
                "detail": {},
            }
        ]
    }

    SAMPLE_ISP_VIEW: ClassVar[dict[str, Any]] = {
        "data": [
            {
                "isp": "AT&T",
                "totalFlaps": 15,
                "detail": {
                    "numLinks": 20,
                    "numEdges": 8,
                    "numFlappingLinks": 5,
                    "numFlappingEdges": 3,
                },
            }
        ]
    }

    def test_parse_link_view(self):
        result = GetLinkFlapSummaryResult.model_validate(self.SAMPLE_LINK_VIEW)
        data = require_present(result.data)
        assert data is not None
        assert len(data) == 1
        item = data[0]
        assert item.link_name == "GE2"
        assert item.total_flaps == 7
        assert item.isp == "Comcast"
        assert item.wan_link_type == "WIRED"

    def test_parse_isp_view(self):
        result = GetLinkFlapSummaryResult.model_validate(self.SAMPLE_ISP_VIEW)
        assert result.data is not None
        data = require_present(result.data)
        assert data is not None
        item = data[0]
        assert item.isp == "AT&T"
        assert item.total_flaps == 15
        detail = require_present(item.detail)
        assert detail is not None
        assert detail.num_flapping_links == 5
        assert detail.num_flapping_edges == 3

    def test_request_view_by(self):
        req = GetLinkFlapSummary.model_validate(
            {
                "interval": {
                    "start": "2026-08-01T00:00:00Z",
                    "end": "2026-08-02T00:00:00Z",
                },
                "viewBy": "edge",
            }
        )
        dumped = req.model_dump(by_alias=True, exclude_unset=True)
        assert dumped["viewBy"] == "edge"


class TestLinkDegradationSummary:
    SAMPLE: ClassVar[dict[str, Any]] = {
        "data": [
            {
                "linkLogicalId": "abc-123",
                "edgeLogicalId": "def-456",
                "linkName": "GE3",
                "edgeName": "NYC-02",
                "interface": "GE3",
                "linkIpAddress": "10.0.1.5",
                "isp": "Verizon",
                "wanLinkType": "WIRED",
                "wanLinkMode": "PUBLIC",
                "detail": {},
                "degradation": {
                    "before": {
                        "totalMinutes": 60.0,
                        "criticalMinutes": 20.0,
                        "fairMinutes": 40.0,
                    },
                    "after": {
                        "totalMinutes": 10.0,
                        "criticalMinutes": 0.0,
                        "fairMinutes": 10.0,
                    },
                },
            }
        ]
    }

    def test_parse_summary(self):
        result = GetLinkDegradationSummaryResult.model_validate(self.SAMPLE)
        data = require_present(result.data)
        assert data is not None
        assert len(data) == 1
        item = data[0]
        assert item.link_name == "GE3"
        assert item.isp == "Verizon"
        degradation = require_present(item.degradation)
        assert degradation is not None
        before = require_present(degradation.before)
        after = require_present(degradation.after)
        assert before is not None
        assert after is not None
        assert before.total_minutes == 60.0
        assert after.critical_minutes == 0.0
        assert item.link_ip_address == "10.0.1.5"

    def test_no_phantom_link_id_edge_id(self):
        """DegradationSummaryItem should not have link_id or edge_id fields."""
        assert "link_id" not in DegradationSummaryItem.model_fields
        assert "edge_id" not in DegradationSummaryItem.model_fields


class TestLinkDegradationDetails:
    SAMPLE: ClassVar[dict[str, Any]] = {
        "data": [
            {
                "linkLogicalId": "abc-123",
                "edgeLogicalId": "def-456",
                "linkName": "GE2",
                "edgeName": "Boston-01",
                "interface": "GE2",
                "linkIpAddress": "10.0.0.1",
                "isp": "Comcast",
                "wanLinkType": "WIRED",
                "wanLinkMode": "PUBLIC",
                "trafficType": "VOICE",
                "degradation": {
                    "before": {
                        "totalMinutes": 30.0,
                        "criticalMinutes": 10.0,
                        "fairMinutes": 20.0,
                    },
                    "after": {
                        "totalMinutes": 5.0,
                        "criticalMinutes": 0.0,
                        "fairMinutes": 5.0,
                    },
                },
                "packetLossRx": 2.5,
                "jitterRx": 8.3,
                "latencyRx": 45.0,
                "packetLossTx": 1.0,
                "jitterTx": 5.0,
                "latencyTx": 42.0,
                "secondsUp": 604800,
                "uptimePct": 99.5,
                "secondsDown": 3024,
                "secondsUnknown": 576,
                "totalFlaps": 12,
                "currentState": "STABLE",
                "currentStateSince": "2026-08-19T00:00:00Z",
                "lastUpdated": "2026-08-20T12:00:00Z",
            }
        ],
        "metaData": {
            "more": True,
            "limit": 5,
            "nextPageLink": "eyJwYWdlIjogMn0=",
        },
    }

    def test_parse_details(self):
        result = GetLinkDegradationDetailsResult.model_validate(self.SAMPLE)
        data = require_present(result.data)
        assert data is not None
        assert len(data) == 1
        item = data[0]
        assert item.link_name == "GE2"
        assert item.traffic_type == "VOICE"
        assert item.packet_loss_rx == 2.5
        assert item.jitter_rx == 8.3
        assert item.latency_rx == 45.0
        assert item.seconds_up == 604800
        degradation = require_present(item.degradation)
        assert degradation is not None
        before = require_present(degradation.before)
        assert before is not None
        assert before.critical_minutes == 10.0

    def test_new_uptime_and_state_fields(self):
        result = GetLinkDegradationDetailsResult.model_validate(self.SAMPLE)
        data = require_present(result.data)
        assert data is not None
        item = data[0]
        assert item.uptime_pct == 99.5
        assert item.seconds_down == 3024
        assert item.seconds_unknown == 576
        assert item.total_flaps == 12
        assert item.current_state == "STABLE"
        assert item.current_state_since == datetime(2026, 8, 19, tzinfo=UTC)
        assert item.last_updated == datetime(2026, 8, 20, 12, tzinfo=UTC)

    def test_no_phantom_link_id_edge_id(self):
        """DegradationDetailItem should not have link_id or edge_id fields."""
        assert "link_id" not in DegradationDetailItem.model_fields
        assert "edge_id" not in DegradationDetailItem.model_fields

    def test_parse_metadata(self):
        result = GetLinkDegradationDetailsResult.model_validate(self.SAMPLE)
        meta_data = require_present(result.meta_data)
        assert meta_data is not None
        assert meta_data.more is True
        assert meta_data.next_page_link == "eyJwYWdlIjogMn0="
        assert meta_data.prev_page_link is MISSING

    def test_empty_response(self):
        result = GetLinkDegradationDetailsResult.model_validate(
            {"data": [], "metaData": {"limit": 20, "more": False}}
        )
        assert result.data == []
        meta_data = require_present(result.meta_data)
        assert meta_data is not None
        assert meta_data.more is False
        assert meta_data.limit == 20

    def test_count_response(self):
        result = GetLinkDegradationDetailsResult.model_validate({"totalCount": 7})

        assert result.total_count == 7
        assert result.data is MISSING
        assert result.meta_data is MISSING


@pytest.mark.parametrize(
    "result_type",
    [
        GetLinkInsightsOverviewResult,
        GetLinkFlapSummaryResult,
        GetLinkDegradationSummaryResult,
        GetLinkDegradationDetailsResult,
    ],
)
def test_link_insights_filter_spec_response(result_type):
    result = result_type.model_validate(
        {
            "pagination": {"supported": False},
            "sorting": {"supported": False},
            "quickSearch": {"supported": False},
            "filtering": {
                "supported": True,
                "attributes": {"isp": {"type": "string", "operators": ["is"]}},
            },
        }
    )

    assert result.filtering["attributes"]["isp"]["operators"] == ["is"]
    assert result.pagination["supported"] is False


# --- Request validation ---


class TestLinkInsightsRequestAliases:
    @pytest.mark.parametrize(
        ("request_type", "params"),
        [
            (GetLinkInsightsOverview, {"interval": _INTERVAL}),
            (
                GetLinkFlapSummary,
                {"interval": _INTERVAL, "viewBy": "link"},
            ),
            (
                GetLinkDegradationSummary,
                {"interval": _INTERVAL, "viewBy": "link"},
            ),
            (GetLinkDegradationDetails, {"interval": _INTERVAL}),
        ],
    )
    def test_filter_spec_safe_name_and_wire_alias(self, request_type, params):
        request = request_type.model_validate({**params, "filter_spec": True})

        dumped = request.model_dump(by_alias=True, exclude_unset=True)

        assert request.filter_spec is True
        assert dumped["_filterSpec"] is True
        assert "filter_spec" not in dumped

    def test_count_safe_name_and_wire_alias(self):
        request = GetLinkDegradationDetails.model_validate(
            {"interval": _INTERVAL, "count": True}
        )

        dumped = request.model_dump(by_alias=True, exclude_unset=True)

        assert request.count is True
        assert dumped["_count"] is True
        assert "count" not in dumped

    def test_wire_control_aliases_are_accepted(self):
        request = GetLinkDegradationDetails.model_validate(
            {"interval": _INTERVAL, "_count": True, "_filterSpec": True}
        )

        assert request.count is True
        assert request.filter_spec is True

    @pytest.mark.parametrize(
        ("request_type", "params"),
        [
            (GetLinkInsightsOverview, {}),
            (GetLinkFlapSummary, {"viewBy": "link"}),
            (GetLinkDegradationSummary, {"viewBy": "link"}),
            (GetLinkDegradationDetails, {}),
        ],
    )
    def test_link_insights_interval_requires_end(self, request_type, params):
        with pytest.raises(ValidationError):
            request_type.model_validate(
                {
                    **params,
                    "interval": {"start": _INTERVAL["start"]},
                }
            )


class TestFlapSummaryLimitValidation:
    def test_valid_limit(self):
        req = GetLinkFlapSummary.model_validate(
            {"interval": _INTERVAL, "viewBy": "link", "limit": 50}
        )
        assert req.limit == 50

    def test_limit_at_boundaries(self):
        for val in (1, 100):
            req = GetLinkFlapSummary.model_validate(
                {"interval": _INTERVAL, "viewBy": "link", "limit": val}
            )
            assert req.limit == val

    def test_limit_zero_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkFlapSummary.model_validate(
                {"interval": _INTERVAL, "viewBy": "link", "limit": 0}
            )

    def test_limit_over_max_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkFlapSummary.model_validate(
                {"interval": _INTERVAL, "viewBy": "link", "limit": 101}
            )

    def test_limit_negative_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkFlapSummary.model_validate(
                {"interval": _INTERVAL, "viewBy": "link", "limit": -5}
            )


class TestDegradationSummaryWithValidation:
    def test_valid_with_before(self):
        req = GetLinkDegradationSummary.model_validate(
            {"interval": _INTERVAL, "viewBy": "link", "with": ["before"]}
        )
        assert req.with_ == [DegradationWith.BEFORE]
        assert req.model_dump(by_alias=True, exclude_unset=True)["with"] == ["before"]

    def test_valid_with_both(self):
        req = GetLinkDegradationSummary.model_validate(
            {"interval": _INTERVAL, "viewBy": "link", "with": ["before", "after"]}
        )
        assert req.with_ == [DegradationWith.BEFORE, DegradationWith.AFTER]

    def test_duplicate_with_value_rejected(self):
        with pytest.raises(ValidationError, match="unique"):
            GetLinkDegradationSummary.model_validate(
                {"interval": _INTERVAL, "viewBy": "link", "with": ["before", "before"]}
            )

    def test_more_than_two_with_values_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkDegradationSummary.model_validate(
                {
                    "interval": _INTERVAL,
                    "viewBy": "link",
                    "with": ["before", "after", "before"],
                }
            )

    def test_invalid_with_value_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkDegradationSummary.model_validate(
                {"interval": _INTERVAL, "viewBy": "link", "with": ["garbage"]}
            )

    def test_limit_over_max_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkDegradationSummary.model_validate(
                {"interval": _INTERVAL, "viewBy": "link", "limit": 101}
            )


class TestDegradationDetailsValidation:
    def test_valid_sort_by(self):
        req = GetLinkDegradationDetails.model_validate(
            {
                "interval": _INTERVAL,
                "sortBy": [{"attribute": "lastUpdated", "type": "DESC"}],
            }
        )
        sort_by = require_present(req.sort_by)
        assert len(sort_by) == 1
        assert sort_by[0].attribute == DegradationDetailsSortAttribute.LAST_UPDATED

    def test_invalid_sort_attribute_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkDegradationDetails.model_validate(
                {
                    "interval": _INTERVAL,
                    "sortBy": [{"attribute": "nonexistent", "type": "ASC"}],
                }
            )

    def test_dot_notation_sort_attribute(self):
        req = GetLinkDegradationDetails.model_validate(
            {
                "interval": _INTERVAL,
                "sortBy": [
                    {
                        "attribute": "degradation.before.criticalMinutes",
                        "type": "DESC",
                    }
                ],
            }
        )
        assert (
            require_present(req.sort_by)[0].attribute
            == DegradationDetailsSortAttribute.DEGRADATION_BEFORE_CRITICAL
        )

    @pytest.mark.parametrize(
        "attribute",
        ["uptimePct", "secondsUp", "secondsDown", "secondsUnknown", "totalFlaps"],
    )
    def test_uptime_and_flap_sort_attributes(self, attribute):
        req = GetLinkDegradationDetails.model_validate(
            {
                "interval": _INTERVAL,
                "sortBy": [{"attribute": attribute, "type": "DESC"}],
            }
        )

        assert require_present(req.sort_by)[0].attribute == attribute

    def test_sort_direction_required(self):
        with pytest.raises(ValidationError):
            GetLinkDegradationDetails.model_validate(
                {"interval": _INTERVAL, "sortBy": [{"attribute": "lastUpdated"}]}
            )

    def test_limit_at_boundaries(self):
        for val in (1, 2048):
            req = GetLinkDegradationDetails.model_validate(
                {"interval": _INTERVAL, "limit": val}
            )
            assert req.limit == val

    def test_limit_over_2048_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkDegradationDetails.model_validate(
                {"interval": _INTERVAL, "limit": 2049}
            )

    def test_quick_search_at_255_character_limit(self):
        req = GetLinkDegradationDetails.model_validate(
            {"interval": _INTERVAL, "quickSearch": "x" * 255}
        )
        assert req.quick_search == "x" * 255

    def test_quick_search_over_255_rejected(self):
        with pytest.raises(ValidationError):
            GetLinkDegradationDetails.model_validate(
                {"interval": _INTERVAL, "quickSearch": "x" * 256}
            )

    @pytest.mark.parametrize("token_field", ["nextPageLink", "prevPageLink"])
    def test_token_pagination_without_interval(self, token_field):
        req = GetLinkDegradationDetails.model_validate(
            {token_field: "eyJwYWdlIjogMn0="}
        )
        assert (
            getattr(
                req,
                "next_page_link" if token_field == "nextPageLink" else "prev_page_link",
            )
            == "eyJwYWdlIjogMn0="
        )
        assert req.interval is MISSING

    def test_empty_request_rejected(self):
        with pytest.raises(ValidationError, match="provide exactly one"):
            GetLinkDegradationDetails.model_validate({})

    def test_both_pagination_tokens_rejected(self):
        with pytest.raises(ValidationError, match="provide at most one"):
            GetLinkDegradationDetails.model_validate(
                {"nextPageLink": "next-token", "prevPageLink": "prev-token"}
            )

    @pytest.mark.parametrize("token_field", ["nextPageLink", "prevPageLink"])
    def test_interval_with_pagination_token_rejected(self, token_field):
        with pytest.raises(ValidationError, match="provide exactly one"):
            GetLinkDegradationDetails.model_validate(
                {"interval": _INTERVAL, token_field: "page-token"}
            )

    @pytest.mark.parametrize("token_field", ["nextPageLink", "prevPageLink"])
    def test_empty_pagination_token_rejected(self, token_field):
        with pytest.raises(ValidationError):
            GetLinkDegradationDetails.model_validate({token_field: ""})

    def test_json_schema_exposes_interval_or_page_token_contract(self):
        schema = GetLinkDegradationDetails.model_json_schema()

        assert {tuple(option["required"]) for option in schema["oneOf"]} == {
            ("interval",),
            ("nextPageLink",),
            ("prevPageLink",),
        }


class TestLinkInsightsFilterValidation:
    @pytest.mark.parametrize(
        ("request_type", "params", "field", "operator", "value"),
        [
            (
                GetLinkInsightsOverview,
                {"interval": _INTERVAL},
                "trafficType",
                "is",
                "VOICE",
            ),
            (
                GetLinkDegradationSummary,
                {"interval": _INTERVAL, "viewBy": "link"},
                "trafficType",
                "is",
                "VOICE",
            ),
            (
                GetLinkDegradationDetails,
                {"interval": _INTERVAL},
                "uptimePct",
                "greaterThan",
                99,
            ),
        ],
    )
    def test_supported_filter_round_trip(
        self, request_type, params, field, operator, value
    ):
        request = request_type.model_validate(
            {
                **params,
                "filters": {
                    "and": [{"field": field, "operator": operator, "value": value}]
                },
            }
        )

        dumped = request.model_dump(by_alias=True, exclude_unset=True)
        assert dumped["filters"]["and"][0]["field"] == field

    def test_flap_summary_rejects_traffic_type_filter(self):
        with pytest.raises(ValidationError, match="trafficType"):
            GetLinkFlapSummary.model_validate(
                {
                    "interval": _INTERVAL,
                    "viewBy": "link",
                    "filters": {
                        "and": [
                            {
                                "field": "trafficType",
                                "operator": "is",
                                "value": "VOICE",
                            }
                        ]
                    },
                }
            )

    @pytest.mark.parametrize(
        "request_data",
        [
            {"interval": _INTERVAL},
            {"interval": _INTERVAL, "viewBy": "link"},
        ],
    )
    def test_summary_requests_reject_detail_only_filter(self, request_data):
        request_type = (
            GetLinkInsightsOverview
            if "viewBy" not in request_data
            else GetLinkDegradationSummary
        )
        with pytest.raises(ValidationError, match="uptimePct"):
            request_type.model_validate(
                {
                    **request_data,
                    "filters": {
                        "and": [
                            {
                                "field": "uptimePct",
                                "operator": "greaterThan",
                                "value": 99,
                            }
                        ]
                    },
                }
            )
