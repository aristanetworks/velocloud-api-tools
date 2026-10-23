"""Live integration tests for the Analytics class.

Exercises every Analytics method against a real VCO, covering multiple
parameter combinations per method. Session-scoped fixtures handle
connection setup and infrastructure discovery so individual tests are
concurrency-safe and independent.

Requires env vars:  VCO_HOST, VCO_API_TOKEN, VCO_ENTERPRISE_ID

Run:
    VCO_HOST=... VCO_API_TOKEN=... VCO_ENTERPRISE_ID=... uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import uuid

import polars as pl
import pytest
from pyvelo.analytics import Analytics
from pyvelo.analytics.enums import (
    EnterpriseFirewallType,
    FirewallType,
    FlowGroupBy,
    FlowMetric,
)
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.enums.metrics import (
    AggregateEdgeLinkMetric,
    BasicMetricViewBy,
    DestMetric,
    FilterFieldOperator,
    GatewayStatusMetric,
    LinkMetric,
    NetworkInterfaceMetric,
    NniMetric,
    PathSeriesViewBy,
    SdwanPeerPathMetric,
    StatusMetric,
)
from pyvelo.client.schemas.metrics import (
    EnterpriseFlowMetricsFilter,
    LinkPair,
)
from pyvelo.core import ApiInterval

from ._discovery import VcoTestContext
from .conftest import assert_frame_valid, skip_on_vco_limitation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ---------------------------------------------------------------------------
# Flow metrics — edge level
# ---------------------------------------------------------------------------


class TestEdgeFlowMetrics:
    @pytest.mark.parametrize(
        "group_by", list(FlowGroupBy), ids=[group.name for group in FlowGroupBy]
    )
    async def test_group_by(
        self,
        analytics: Analytics,
        ctx: VcoTestContext,
        interval: ApiInterval,
        group_by: FlowGroupBy,
    ):
        with skip_on_vco_limitation():
            frame = await analytics.get_edge_flow_metrics(
                edge_id=ctx.first_edge.id, interval=interval, group_by=group_by
            )
        assert_frame_valid(frame)

    @pytest.mark.parametrize("limit", [1, 2, 3, 5, 10])
    async def test_small_limits(
        self,
        analytics: Analytics,
        ctx: VcoTestContext,
        interval: ApiInterval,
        limit: int,
    ):
        frame = await analytics.get_edge_flow_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            group_by=FlowGroupBy.APP,
            limit=limit,
            sort=FlowMetric.TOTAL_BYTES,
        )
        assert_frame_valid(frame)
        assert len(frame) <= limit

    async def test_with_metric_subset(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_flow_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            group_by=FlowGroupBy.APP,
            limit=5,
            sort=FlowMetric.TOTAL_BYTES,
            metrics=[FlowMetric.BYTES_RX, FlowMetric.BYTES_TX],
        )
        assert_frame_valid(frame)

    async def test_with_filter_values(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_flow_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            group_by=FlowGroupBy.DEST_FQDN,
            filter_values=["google.com"],
        )
        assert_frame_valid(frame)

    async def test_with_structured_filter(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_flow_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            group_by=FlowGroupBy.APP,
            filters=[
                EnterpriseFlowMetricsFilter.model_validate(
                    {"field": "destPort", "op": FilterFieldOperator.IS, "value": 443}
                )
            ],
        )
        assert_frame_valid(frame)


# ---------------------------------------------------------------------------
# Flow metrics — edge level, name column dtype
# ---------------------------------------------------------------------------


class TestEdgeFlowMetricsNameDtype:
    """Small limits provoke mixed-type name columns (int | str); verify polars handles it."""

    @pytest.mark.parametrize(
        "group_by", list(FlowGroupBy), ids=[group.name for group in FlowGroupBy]
    )
    @pytest.mark.parametrize("limit", [1, 3, 5])
    async def test_name_column_dtype(
        self,
        analytics: Analytics,
        ctx: VcoTestContext,
        interval: ApiInterval,
        group_by: FlowGroupBy,
        limit: int,
    ):
        with skip_on_vco_limitation():
            frame = await analytics.get_edge_flow_metrics(
                edge_id=ctx.first_edge.id,
                interval=interval,
                group_by=group_by,
                limit=limit,
                sort=FlowMetric.TOTAL_BYTES,
            )
        assert_frame_valid(frame)
        df = frame.to_polars()
        if "name" in df.columns and len(df) > 0:
            assert df["name"].dtype in (
                pl.Utf8,
                pl.String,
                pl.Int64,
                pl.UInt64,
                pl.Int32,
            )


# ---------------------------------------------------------------------------
# Flow series — edge level
# ---------------------------------------------------------------------------


class TestEdgeFlowSeries:
    @pytest.mark.parametrize(
        "group_by",
        [FlowGroupBy.APP, FlowGroupBy.DEST_FQDN],
        ids=[FlowGroupBy.APP.name, FlowGroupBy.DEST_FQDN.name],
    )
    async def test_group_by(
        self,
        analytics: Analytics,
        ctx: VcoTestContext,
        interval: ApiInterval,
        group_by: FlowGroupBy,
    ):
        frame = await analytics.get_edge_flow_series(
            edge_id=ctx.first_edge.id, interval=interval, group_by=group_by
        )
        assert_frame_valid(frame)

    async def test_with_limits(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_flow_series(
            edge_id=ctx.first_edge.id,
            interval=interval,
            group_by=FlowGroupBy.APP,
            max_samples=10,
            limit=3,
        )
        assert_frame_valid(frame)


# ---------------------------------------------------------------------------
# Flow metrics — enterprise level
# ---------------------------------------------------------------------------


class TestEnterpriseFlowMetrics:
    @pytest.mark.parametrize(
        "view_by",
        list(BasicMetricViewBy),
        ids=[view.name for view in BasicMetricViewBy],
    )
    async def test_view_by(
        self, analytics: Analytics, interval: ApiInterval, view_by: BasicMetricViewBy
    ):
        with skip_on_vco_limitation():
            frame = await analytics.get_enterprise_flow_metrics(
                interval=interval, view_by=view_by
            )
        assert_frame_valid(frame)

    @pytest.mark.parametrize("limit", [1, 2, 3, 5, 10])
    async def test_small_limits(
        self, analytics: Analytics, interval: ApiInterval, limit: int
    ):
        frame = await analytics.get_enterprise_flow_metrics(
            interval=interval,
            view_by=BasicMetricViewBy.APPLICATION,
            limit=limit,
            sort=DestMetric.TOTAL_BYTES,
        )
        assert_frame_valid(frame)
        assert len(frame) <= limit

    async def test_with_metric_subset(
        self, analytics: Analytics, interval: ApiInterval
    ):
        frame = await analytics.get_enterprise_flow_metrics(
            interval=interval,
            view_by=BasicMetricViewBy.APPLICATION,
            limit=5,
            sort=DestMetric.TOTAL_BYTES,
            metrics=[DestMetric.BYTES_RX, DestMetric.BYTES_TX],
        )
        assert_frame_valid(frame)

    async def test_with_filter(self, analytics: Analytics, interval: ApiInterval):
        frame = await analytics.get_enterprise_flow_metrics(
            interval=interval,
            view_by=BasicMetricViewBy.DEST_FQDN,
            filters=[
                EnterpriseFlowMetricsFilter.model_validate(
                    {
                        "field": "destFQDN",
                        "op": FilterFieldOperator.CONTAINS,
                        "value": "google",
                    }
                )
            ],
        )
        assert_frame_valid(frame)


# ---------------------------------------------------------------------------
# Link metrics & series
# ---------------------------------------------------------------------------


class TestEdgeLinkMetrics:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_link_metrics(
            edge_id=ctx.first_edge.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_with_throughput_metrics(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_link_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            metrics=[LinkMetric.BYTES_RX, LinkMetric.BYTES_TX, LinkMetric.TOTAL_BYTES],
            sort=LinkMetric.TOTAL_BYTES,
        )
        assert_frame_valid(frame, check_other=False)

    async def test_with_qoe_metrics(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_link_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            metrics=[
                LinkMetric.BEST_LATENCY_MS_RX,
                LinkMetric.BEST_JITTER_MS_RX,
                LinkMetric.BEST_LOSS_PCT_RX,
                LinkMetric.SCORE_RX,
                LinkMetric.SCORE_TX,
            ],
        )
        assert_frame_valid(frame, check_other=False)


class TestEdgeLinkSeries:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_link_series(
            edge_id=ctx.first_edge.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_with_limit(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_link_series(
            edge_id=ctx.first_edge.id,
            interval=interval,
            metrics=[LinkMetric.BYTES_RX, LinkMetric.BYTES_TX],
            limit=2,
        )
        assert_frame_valid(frame, check_other=False)


class TestAggregateLinkMetrics:
    async def test_defaults(self, analytics: Analytics, interval: ApiInterval):
        frame = await analytics.get_aggregate_link_metrics(interval=interval)
        assert_frame_valid(frame, check_other=False)

    async def test_with_metric_subset(
        self, analytics: Analytics, interval: ApiInterval
    ):
        frame = await analytics.get_aggregate_link_metrics(
            interval=interval,
            metrics=[
                AggregateEdgeLinkMetric.TOTAL_BYTES,
                AggregateEdgeLinkMetric.SCORE_RX,
            ],
        )
        assert_frame_valid(frame, check_other=False)


# ---------------------------------------------------------------------------
# Interface metrics & series
# ---------------------------------------------------------------------------


class TestEdgeInterfaceMetrics:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_interface_metrics(
            edge_id=ctx.first_edge.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_with_metric_subset(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_interface_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            metrics=[
                NetworkInterfaceMetric.BYTES_RX,
                NetworkInterfaceMetric.BYTES_TX,
                NetworkInterfaceMetric.DROPPED_RX,
                NetworkInterfaceMetric.ERRORS_RX,
            ],
        )
        assert_frame_valid(frame, check_other=False)


class TestEdgeInterfaceSeries:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_interface_series(
            edge_id=ctx.first_edge.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_with_max_samples(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_interface_series(
            edge_id=ctx.first_edge.id, interval=interval, max_samples=10
        )
        assert_frame_valid(frame, check_other=False)


# ---------------------------------------------------------------------------
# Path metrics & series (require peer discovery)
# ---------------------------------------------------------------------------


class TestEdgePathMetrics:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        if ctx.sdwan_gateway_peer is None:
            pytest.skip("No SD-WAN gateway peer discovered")
        frame = await analytics.get_edge_path_metrics(
            edge_id=ctx.sdwan_gateway_peer.edge_id,
            interval=interval,
            peer_logical_id=uuid.UUID(ctx.sdwan_gateway_peer.peer_logical_id),
        )
        assert_frame_valid(frame, check_other=False)

    async def test_with_metric_subset(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        if ctx.sdwan_gateway_peer is None:
            pytest.skip("No SD-WAN gateway peer discovered")
        frame = await analytics.get_edge_path_metrics(
            edge_id=ctx.sdwan_gateway_peer.edge_id,
            interval=interval,
            peer_logical_id=uuid.UUID(ctx.sdwan_gateway_peer.peer_logical_id),
            metrics=[
                SdwanPeerPathMetric.LATENCY_RX,
                SdwanPeerPathMetric.JITTER_RX,
                SdwanPeerPathMetric.PACKET_LOSS_RX,
            ],
        )
        assert_frame_valid(frame, check_other=False)


class TestEdgePathSeries:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        peer = ctx.sdwan_gateway_peer
        if peer is None or not peer.src_link_internal_id:
            pytest.skip("No SD-WAN gateway peer path links discovered")
        frame = await analytics.get_edge_path_series(
            edge_id=peer.edge_id,
            interval=interval,
            peer_logical_id=uuid.UUID(peer.peer_logical_id),
            paths=[
                LinkPair(
                    src_link_logical_id=uuid.UUID(peer.src_link_internal_id),
                    dest_link_logical_id=(
                        uuid.UUID(peer.dest_link_internal_id)
                        if peer.dest_link_internal_id
                        else None
                    ),
                )
            ],
        )
        assert_frame_valid(frame, check_other=False)

    async def test_view_by_peer(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        peer = ctx.sdwan_gateway_peer
        if peer is None or not peer.src_link_internal_id:
            pytest.skip("No SD-WAN gateway peer path links discovered")
        frame = await analytics.get_edge_path_series(
            edge_id=peer.edge_id,
            interval=interval,
            peer_logical_id=uuid.UUID(peer.peer_logical_id),
            paths=[
                LinkPair(
                    src_link_logical_id=uuid.UUID(peer.src_link_internal_id),
                    dest_link_logical_id=(
                        uuid.UUID(peer.dest_link_internal_id)
                        if peer.dest_link_internal_id
                        else None
                    ),
                )
            ],
            view_by=PathSeriesViewBy.PEER,
            max_samples=10,
        )
        assert_frame_valid(frame, check_other=False)


# ---------------------------------------------------------------------------
# Firewall metrics & series
# ---------------------------------------------------------------------------


_FW_VIEW_BY: dict[FirewallType, str] = {
    FirewallType.IDPS: "threatSourceIp",
    FirewallType.MALICIOUS_IP: "sourceIp",
    FirewallType.URL_CATEGORY: "sourceIp",
    FirewallType.URL_REPUTATION: "sourceIp",
}


class TestEdgeFirewallMetrics:
    @pytest.mark.parametrize(
        "fw_type", list(FirewallType), ids=[fw_type.name for fw_type in FirewallType]
    )
    async def test_types(
        self,
        analytics: Analytics,
        ctx: VcoTestContext,
        interval: ApiInterval,
        fw_type: FirewallType,
    ):
        frame = await analytics.get_edge_firewall_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            type=fw_type,
            view_by=_FW_VIEW_BY[fw_type],
        )
        assert_frame_valid(frame)

    async def test_with_limit(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_firewall_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            type=FirewallType.IDPS,
            view_by="threatSourceIp",
            limit=5,
        )
        assert_frame_valid(frame)
        assert len(frame) <= 5


class TestEdgeFirewallSeries:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_firewall_series(
            edge_id=ctx.first_edge.id, interval=interval, max_samples=20
        )
        assert_frame_valid(frame, check_other=False)


class TestEnterpriseFirewallMetrics:
    @pytest.mark.parametrize(
        "ent_type",
        list(EnterpriseFirewallType),
        ids=[ent_type.name for ent_type in EnterpriseFirewallType],
    )
    async def test_types(
        self,
        analytics: Analytics,
        interval: ApiInterval,
        ent_type: EnterpriseFirewallType,
    ):
        frame = await analytics.get_enterprise_firewall_metrics(
            interval=interval, type=ent_type
        )
        assert_frame_valid(frame)


# ---------------------------------------------------------------------------
# Health metrics & series
# ---------------------------------------------------------------------------


class TestEdgeHealth:
    async def test_metrics_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_health_metrics(
            edge_id=ctx.first_edge.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_metrics_subset(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_health_metrics(
            edge_id=ctx.first_edge.id,
            interval=interval,
            metrics=[StatusMetric.CPU_PCT, StatusMetric.MEMORY_PCT],
        )
        assert_frame_valid(frame, check_other=False)

    async def test_series_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_health_series(
            edge_id=ctx.first_edge.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_series_subset(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_health_series(
            edge_id=ctx.first_edge.id,
            interval=interval,
            metrics=[
                StatusMetric.CPU_PCT,
                StatusMetric.MEMORY_PCT,
                StatusMetric.FLOW_COUNT,
            ],
        )
        assert_frame_valid(frame, check_other=False)


class TestGatewayHealth:
    async def test_metrics_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        if not ctx.gateways:
            pytest.skip("No gateway discovered")
        frame = await analytics.get_gateway_health_metrics(
            gateway_id=ctx.first_gateway.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_metrics_subset(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        if not ctx.gateways:
            pytest.skip("No gateway discovered")
        frame = await analytics.get_gateway_health_metrics(
            gateway_id=ctx.first_gateway.id,
            interval=interval,
            metrics=[GatewayStatusMetric.CPU_PCT, GatewayStatusMetric.MEMORY_PCT],
        )
        assert_frame_valid(frame, check_other=False)

    async def test_series_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        if not ctx.gateways:
            pytest.skip("No gateway discovered")
        frame = await analytics.get_gateway_health_series(
            gateway_id=ctx.first_gateway.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)


# ---------------------------------------------------------------------------
# QoE series
# ---------------------------------------------------------------------------


class TestEdgeQoeSeries:
    async def test_defaults(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_qoe_series(
            edge_id=ctx.first_edge.id, interval=interval
        )
        assert_frame_valid(frame, check_other=False)

    async def test_with_max_samples(
        self, analytics: Analytics, ctx: VcoTestContext, interval: ApiInterval
    ):
        frame = await analytics.get_edge_qoe_series(
            edge_id=ctx.first_edge.id, interval=interval, max_samples=10
        )
        assert_frame_valid(frame, check_other=False)


# ---------------------------------------------------------------------------
# NNI metrics & series
# ---------------------------------------------------------------------------


class TestNniMetrics:
    async def test_defaults(self, analytics: Analytics, interval: ApiInterval):
        with skip_on_vco_limitation():
            frame = await analytics.get_nni_metrics(interval=interval)
        assert_frame_valid(frame, check_other=False)

    async def test_with_metric_subset(
        self, analytics: Analytics, interval: ApiInterval
    ):
        with skip_on_vco_limitation():
            frame = await analytics.get_nni_metrics(
                interval=interval, metrics=[NniMetric.BYTES_RX, NniMetric.BYTES_TX]
            )
        assert_frame_valid(frame, check_other=False)


class TestNniSeries:
    async def test_defaults(self, analytics: Analytics, interval: ApiInterval):
        with skip_on_vco_limitation():
            frame = await analytics.get_nni_series(interval=interval)
        assert_frame_valid(frame, check_other=False)

    async def test_with_metric_subset(
        self, analytics: Analytics, interval: ApiInterval
    ):
        with skip_on_vco_limitation():
            frame = await analytics.get_nni_series(
                interval=interval,
                metrics=[NniMetric.BYTES_RX, NniMetric.PACKETS_RX],
                limit=5,
            )
        assert_frame_valid(frame, check_other=False)


# ---------------------------------------------------------------------------
# Collect methods (paginated)
# ---------------------------------------------------------------------------


class TestCollectEvents:
    async def test_defaults(self, analytics: Analytics, interval: ApiInterval):
        frame = await analytics.collect_events(interval=interval, max_items=100)
        assert isinstance(frame, MetricFrame)

    async def test_with_event_types(self, analytics: Analytics, interval: ApiInterval):
        from pyvelo.client.enums.event import EventType

        frame = await analytics.collect_events(
            interval=interval,
            event_types=[EventType.EDGE_UP, EventType.EDGE_DOWN],
            max_items=50,
        )
        assert isinstance(frame, MetricFrame)

    async def test_with_severity(self, analytics: Analytics, interval: ApiInterval):
        from pyvelo.client.enums.event import EventSeverity

        frame = await analytics.collect_events(
            interval=interval,
            severity=[EventSeverity.ALERT, EventSeverity.CRITICAL],
            max_items=50,
        )
        assert isinstance(frame, MetricFrame)

    async def test_max_items(self, analytics: Analytics, interval: ApiInterval):
        frame = await analytics.collect_events(interval=interval, max_items=20)
        assert isinstance(frame, MetricFrame)
        assert len(frame) <= 20


class TestCollectEdges:
    async def test_defaults(self, analytics: Analytics):
        frame = await analytics.collect_edges()
        assert isinstance(frame, MetricFrame)
        assert len(frame) > 0

    async def test_with_state_filter(self, analytics: Analytics):
        from pyvelo.client.enums.edge import EdgeState

        frame = await analytics.collect_edges(edge_state=[EdgeState.CONNECTED])
        assert isinstance(frame, MetricFrame)

    async def test_max_items(self, analytics: Analytics):
        frame = await analytics.collect_edges(max_items=5)
        assert isinstance(frame, MetricFrame)
        assert len(frame) <= 5


class TestCollectConfigurations:
    async def test_defaults(self, analytics: Analytics):
        frame = await analytics.collect_configurations()
        assert isinstance(frame, MetricFrame)

    async def test_without_edge_count(self, analytics: Analytics):
        frame = await analytics.collect_configurations(with_edge_count=False)
        assert isinstance(frame, MetricFrame)
