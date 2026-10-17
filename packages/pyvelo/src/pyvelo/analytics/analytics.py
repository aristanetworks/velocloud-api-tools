from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic_core import MISSING

from pyvelo.analytics._collect import collect_pages
from pyvelo.analytics._firewall import (
    get_edge_firewall_metrics as _edge_fw_metrics,
)
from pyvelo.analytics._firewall import (
    get_edge_firewall_series as _edge_fw_series,
)
from pyvelo.analytics._firewall import (
    get_enterprise_firewall_metrics as _ent_fw_metrics,
)
from pyvelo.analytics._flow import (
    get_edge_flow_metrics as _flow_metrics,
)
from pyvelo.analytics._flow import (
    get_edge_flow_series as _flow_series,
)
from pyvelo.analytics._health import (
    get_edge_health_metrics as _health_metrics,
)
from pyvelo.analytics._health import (
    get_edge_health_series as _health_series,
)
from pyvelo.analytics._helpers import get_field, items_to_frame
from pyvelo.analytics._link import get_aggregate_link_metrics as _aggregate_link_metrics
from pyvelo.analytics._series import (
    denormalize_flat_series,
    denormalize_timeseries,
)
from pyvelo.analytics.enums import (
    EnterpriseFirewallType,
    FirewallType,
    FlowGroupBy,
    FlowMetric,
)
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.enums.edge import (
    ActivationState,
    EdgeServiceState,
    EdgeState,
    HaState,
)
from pyvelo.client.enums.event import EventSeverity, EventType
from pyvelo.client.enums.link_quality import QualityState, TrafficType
from pyvelo.client.enums.metrics import (
    AggregateEdgeLinkMetric,
    BasicMetricViewBy,
    DestMetric,
    GatewayStatusMetric,
    LinkMetric,
    NetworkInterfaceMetric,
    NniMetric,
    NsdTunnelStatsMetric,
    SdwanPeerPathMetric,
    StatusMetric,
)
from pyvelo.client.enums.profiles import GetEnterpriseConfigurationsWith
from pyvelo.client.exceptions import VcoEmptyResultError
from pyvelo.client.schemas.configuration import EnterpriseConfigurationProfile
from pyvelo.client.schemas.edge_filters import (
    EdgeActivationStateFilter,
    EdgeFilter,
    EdgeFiltersAnd,
    EdgeHaStateFilter,
    EdgeStateFilter,
    EdgeStringValueFilter,
)
from pyvelo.client.schemas.enterprise import GetEnterpriseEdges, GetEnterpriseEdgesItem
from pyvelo.client.schemas.event import (
    EnterpriseEventFiltersAnd,
    EnterpriseEventSeverityFilter,
    EnterpriseEventStringValueFilter,
    EnterpriseEventTypeFilter,
    EventSorting,
    GetEnterpriseEvents,
    GetEnterpriseEventsItem,
)
from pyvelo.client.schemas.link_quality import (
    GetLinkQualityEvents,
    LinkQualityData,
)
from pyvelo.client.schemas.metrics import (
    EnterpriseFlowMetricsFilter,
    GetEdgeLinkMetrics,
    GetEdgeLinkSeries,
    GetEdgeNetworkInterfaceMetrics,
    GetEdgeNetworkInterfaceSeries,
    GetEdgeSdwanPeerPathMetrics,
    GetEdgeSdwanPeerPathSeries,
    GetEnterpriseFlowMetrics,
    GetGatewayStatusMetrics,
    GetGatewayStatusSeries,
    GetNsdEdgeTunnelStats,
    GetNsdTunnelStats,
    LinkPair,
)
from pyvelo.client.schemas.monitoring import (
    GetEnterpriseNniMetrics,
    GetEnterpriseNniSeries,
)
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.core import ApiInterval

if __builtins__:  # type: ignore[truthy-bool]
    from pyvelo.client.enums.metrics import PathSeriesViewBy
    from pyvelo.client.protocol import VcoClientProtocol


class Analytics:
    __slots__ = ("_client", "_enterprise_id")

    def __init__(
        self, client: VcoClientProtocol, *, enterprise_id: int | None = None
    ) -> None:
        self._client = client
        self._enterprise_id = enterprise_id

    # -----------------------------------------------------------------------
    # Group 1: Edge Flow (14 methods → 2)
    # -----------------------------------------------------------------------

    async def get_edge_flow_metrics(
        self,
        edge_id: int,
        interval: ApiInterval,
        group_by: FlowGroupBy,
        *,
        limit: int | None = None,
        sort: FlowMetric | None = None,
        metrics: list[FlowMetric] | None = None,
        filter_values: list[str | int] | None = None,
        filters: list[EnterpriseFlowMetricsFilter] | None = None,
    ) -> MetricFrame:
        return await _flow_metrics(
            self._client,
            edge_id,
            interval,
            group_by,
            enterprise_id=self._enterprise_id,
            limit=limit,
            sort=sort,
            metrics=metrics,
            filter_values=filter_values,
            filters=filters,
        )

    async def get_edge_flow_series(
        self,
        edge_id: int,
        interval: ApiInterval,
        group_by: FlowGroupBy,
        *,
        max_samples: int | None = None,
        limit: int | None = None,
        sort: FlowMetric | None = None,
        metrics: list[FlowMetric] | None = None,
        filter_values: list[str | int] | None = None,
        filters: list[EnterpriseFlowMetricsFilter] | None = None,
    ) -> MetricFrame:
        return await _flow_series(
            self._client,
            edge_id,
            interval,
            group_by,
            enterprise_id=self._enterprise_id,
            max_samples=max_samples,
            limit=limit,
            sort=sort,
            metrics=metrics,
            filter_values=filter_values,
            filters=filters,
        )

    # -----------------------------------------------------------------------
    # Group 2: Enterprise Flow (keep as-is)
    # -----------------------------------------------------------------------

    async def get_enterprise_flow_metrics(
        self,
        interval: ApiInterval,
        view_by: BasicMetricViewBy,
        *,
        filters: list[EnterpriseFlowMetricsFilter] | None = None,
        limit: int | None = None,
        sort: DestMetric | None = None,
        metrics: list[DestMetric] | None = None,
    ) -> MetricFrame:
        request = GetEnterpriseFlowMetrics(
            interval=interval,
            view_by=view_by,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            filters=filters if filters else MISSING,
            limit=limit if limit is not None else MISSING,
            sort=sort if sort else MISSING,
            metrics=metrics if metrics else MISSING,
        )
        cols = [
            "name",
            "packets_rx",
            "packets_tx",
            "total_packets",
            "bytes_rx",
            "bytes_tx",
            "total_bytes",
            "flow_count",
        ]
        try:
            result = await self._client.get_enterprise_flow_metrics(request)
        except VcoEmptyResultError:
            return MetricFrame(columns=cols, rows=[])
        return items_to_frame(result, cols)

    # -----------------------------------------------------------------------
    # Group 3: Edge Link (keep separate)
    # -----------------------------------------------------------------------

    async def get_edge_link_metrics(
        self,
        edge_id: int,
        interval: ApiInterval,
        *,
        metrics: list[LinkMetric] | None = None,
        limit: int | None = None,
        sort: LinkMetric | None = None,
        links: list[int] | None = None,
    ) -> MetricFrame:
        request = GetEdgeLinkMetrics(
            edge_id=edge_id,
            interval=interval,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            metrics=metrics if metrics else MISSING,
            limit=limit if limit is not None else MISSING,
            sort=sort if sort else MISSING,
            links=links if links else MISSING,
        )
        result = await self._client.get_edge_link_metrics(request)
        return items_to_frame(
            result,
            [
                "link_id",
                "name",
                "bytes_rx",
                "bytes_tx",
                "total_bytes",
                "packets_rx",
                "packets_tx",
                "total_packets",
            ],
        )

    async def get_edge_link_series(
        self,
        edge_id: int,
        interval: ApiInterval,
        *,
        metrics: list[LinkMetric] | None = None,
        limit: int | None = None,
        sort: LinkMetric | None = None,
        links: list[int] | None = None,
    ) -> MetricFrame:
        request = GetEdgeLinkSeries(
            edge_id=edge_id,
            interval=interval,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            metrics=metrics if metrics else MISSING,
            limit=limit if limit is not None else MISSING,
            sort=sort if sort else MISSING,
            links=links if links else MISSING,
        )
        result = await self._client.get_edge_link_series(request)
        items: list[dict[str, Any]] = []
        for item in result:
            link = item.link if item.link is not MISSING else None
            if link is not None:
                display = (
                    link.display_name if link.display_name is not MISSING else None
                )
                isp = link.isp if link.isp is not MISSING else None
                name = display or isp or str(item.link_id)
            else:
                name = str(item.link_id)
            entry: dict[str, Any] = {
                "name": name,
                "series": (
                    list(item.series)
                    if item.series is not MISSING and item.series
                    else []
                ),
            }
            items.append(entry)
        return denormalize_timeseries(items, "link_name")

    # -----------------------------------------------------------------------
    # Group 4: Aggregate Link (keep as-is)
    # -----------------------------------------------------------------------

    async def get_aggregate_link_metrics(
        self,
        interval: ApiInterval,
        *,
        metrics: list[AggregateEdgeLinkMetric] | None = None,
    ) -> MetricFrame:
        return await _aggregate_link_metrics(
            self._client,
            interval,
            enterprise_id=self._enterprise_id,
            metrics=metrics,
        )

    # -----------------------------------------------------------------------
    # Group 5: Edge Network Interface (keep separate)
    # -----------------------------------------------------------------------

    async def get_edge_interface_metrics(
        self,
        edge_id: int,
        interval: ApiInterval,
        *,
        metrics: list[NetworkInterfaceMetric] | None = None,
    ) -> MetricFrame:
        request = GetEdgeNetworkInterfaceMetrics(
            edge_id=edge_id,
            interval=interval,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            metrics=metrics if metrics else MISSING,
        )
        result = await self._client.get_edge_network_interface_metrics(request)
        return items_to_frame(
            result,
            [
                "name",
                "interface",
                "bytes_in",
                "bytes_out",
                "packets_in",
                "packets_out",
                "errors_in",
                "errors_out",
                "drops_in",
                "drops_out",
            ],
        )

    async def get_edge_interface_series(
        self,
        edge_id: int,
        interval: ApiInterval,
        *,
        metrics: list[NetworkInterfaceMetric] | None = None,
        max_samples: int | None = None,
    ) -> MetricFrame:
        request = GetEdgeNetworkInterfaceSeries(
            edge_id=edge_id,
            interval=interval,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            metrics=metrics if metrics else MISSING,
            max_samples=max_samples if max_samples is not None else MISSING,
        )
        result = await self._client.get_edge_network_interface_series(request)
        return denormalize_timeseries(list(result), "name")

    # -----------------------------------------------------------------------
    # Group 6: Edge Peer Path (keep separate)
    # -----------------------------------------------------------------------

    async def get_edge_path_metrics(
        self,
        edge_id: int,
        interval: ApiInterval,
        peer_logical_id: uuid.UUID,
        *,
        metrics: list[SdwanPeerPathMetric] | None = None,
    ) -> MetricFrame:
        request = GetEdgeSdwanPeerPathMetrics(
            edge_id=edge_id,
            interval=interval,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            peer_logical_id=peer_logical_id,
            metrics=metrics if metrics else list(SdwanPeerPathMetric),
        )
        result = await self._client.get_edge_sdwan_peer_path_metrics(request)
        rows: list[dict[str, Any]] = []
        for item in result:
            row: dict[str, Any] = {"status": str(item.status)}
            if item.source:
                row["src_link_logical_id"] = get_field(item.source, "link_logical_id")
                row["src_link_name"] = get_field(item.source, "link_name")
            if item.destination:
                row["dst_link_logical_id"] = get_field(
                    item.destination, "link_logical_id"
                )
                row["dst_link_name"] = get_field(item.destination, "link_name")
            if item.metrics and item.metrics is not MISSING:
                for k, v in item.metrics.items():
                    row[str(k)] = v
            rows.append(row)

        metric_cols = [m.value for m in (metrics or list(SdwanPeerPathMetric))]
        columns = [
            "status",
            "src_link_logical_id",
            "src_link_name",
            "dst_link_logical_id",
            "dst_link_name",
            *metric_cols,
        ]
        return MetricFrame(columns=columns, rows=rows)

    async def get_edge_path_series(
        self,
        edge_id: int,
        interval: ApiInterval,
        peer_logical_id: uuid.UUID,
        paths: list[LinkPair],
        *,
        view_by: PathSeriesViewBy | None = None,
        metrics: list[SdwanPeerPathMetric] | None = None,
        max_samples: int | None = None,
    ) -> MetricFrame:
        from pyvelo.client.enums.metrics import PathSeriesViewBy as PSViewBy

        request = GetEdgeSdwanPeerPathSeries(
            edge_id=edge_id,
            interval=interval,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            peer_logical_id=peer_logical_id,
            paths=paths,
            metrics=metrics if metrics else list(SdwanPeerPathMetric),
            view_by=view_by if view_by else PSViewBy.PATH,
            max_samples=max_samples if max_samples is not None else MISSING,
        )
        result = await self._client.get_edge_sdwan_peer_path_series(request)
        return denormalize_timeseries(
            list(result), "path", timeseries_field="time_series"
        )

    # -----------------------------------------------------------------------
    # Group 7: NSD Tunnel (2 → 1)
    # -----------------------------------------------------------------------

    async def get_nsd_tunnel_metrics(
        self,
        nsd_logical_id: uuid.UUID,
        interval: ApiInterval,
        *,
        edge_id: int | None = None,
        metrics: list[NsdTunnelStatsMetric] | None = None,
    ) -> MetricFrame:
        metric_list = metrics if metrics else MISSING

        if edge_id is not None:
            request = GetNsdEdgeTunnelStats(
                nsd_logical_id=nsd_logical_id,
                edge_id=edge_id,
                interval=interval,
                metrics=metric_list,
            )
            result = await self._client.get_nsd_edge_tunnel_stats(request)
        else:
            request = GetNsdTunnelStats(
                nsd_logical_id=nsd_logical_id,
                interval=interval,
                metrics=metric_list,
            )
            result = await self._client.get_nsd_tunnel_stats(request)

        rows: list[dict[str, Any]] = []
        for item in result:
            public_ip = get_field(item, "dc_public_ip") or get_field(
                item, "nvs_public_ip"
            )
            series = get_field(item, "series")
            if not series:
                continue
            frame = denormalize_timeseries(
                [{"name": public_ip or "", "series": series}],
                "tunnel",
                group_field="name",
            )
            rows.extend(frame.rows)

        if rows:
            return MetricFrame(columns=list(rows[0].keys()), rows=rows)
        return MetricFrame(columns=["timestamp", "tunnel"], rows=[])

    # -----------------------------------------------------------------------
    # Group 8: Edge Firewall (5 → 2)
    # -----------------------------------------------------------------------

    async def get_edge_firewall_metrics(
        self,
        edge_id: int,
        interval: ApiInterval,
        type: FirewallType,
        view_by: str,
        *,
        limit: int | None = None,
        sort: str | None = None,
    ) -> MetricFrame:
        return await _edge_fw_metrics(
            self._client,
            edge_id,
            interval,
            type,
            view_by,
            enterprise_id=self._enterprise_id,
            limit=limit,
            sort=sort,
        )

    async def get_edge_firewall_series(
        self,
        edge_id: int,
        interval: ApiInterval,
        max_samples: int,
    ) -> MetricFrame:
        return await _edge_fw_series(
            self._client,
            edge_id,
            interval,
            max_samples,
            enterprise_id=self._enterprise_id,
        )

    # -----------------------------------------------------------------------
    # Group 9: Enterprise Firewall (6 → 1)
    # -----------------------------------------------------------------------

    async def get_enterprise_firewall_metrics(
        self,
        interval: ApiInterval,
        type: EnterpriseFirewallType,
        view_by: str | None = None,
        *,
        limit: int | None = None,
        sort: str | None = None,
    ) -> MetricFrame:
        return await _ent_fw_metrics(
            self._client,
            interval,
            type,
            view_by,
            enterprise_id=self._enterprise_id,
            limit=limit,
            sort=sort,
        )

    # -----------------------------------------------------------------------
    # Group 10: Edge Health (3 → 2)
    # -----------------------------------------------------------------------

    async def get_edge_health_metrics(
        self,
        edge_id: int,
        interval: ApiInterval,
        *,
        metrics: list[StatusMetric] | None = None,
    ) -> MetricFrame:
        return await _health_metrics(
            self._client,
            edge_id,
            interval,
            enterprise_id=self._enterprise_id,
            metrics=metrics,
        )

    async def get_edge_health_series(
        self,
        edge_id: int,
        interval: ApiInterval,
        *,
        metrics: list[StatusMetric] | None = None,
    ) -> MetricFrame:
        return await _health_series(
            self._client,
            edge_id,
            interval,
            enterprise_id=self._enterprise_id,
            metrics=metrics,
        )

    # -----------------------------------------------------------------------
    # Group 11: Gateway Health (keep as-is)
    # -----------------------------------------------------------------------

    async def get_gateway_health_metrics(
        self,
        gateway_id: int,
        interval: ApiInterval,
        *,
        metrics: list[GatewayStatusMetric] | None = None,
    ) -> MetricFrame:
        request = GetGatewayStatusMetrics(
            gateway_id=gateway_id,
            interval=interval,
            metrics=metrics if metrics else MISSING,
        )
        result = await self._client.get_gateway_status_metrics(request)
        row: dict[str, Any] = {}
        for field in (
            "tunnel_count",
            "tunnel_count_v6",
            "cpu_pct",
            "memory_pct",
            "flow_count",
            "handoff_queue_drops",
            "connected_edges",
        ):
            val = getattr(result, field, None)
            if val is not MISSING:
                row[field] = val
            else:
                row[field] = None
        columns = list(row.keys())
        return MetricFrame(columns=columns, rows=[row])

    async def get_gateway_health_series(
        self,
        gateway_id: int,
        interval: ApiInterval,
        *,
        metrics: list[GatewayStatusMetric] | None = None,
    ) -> MetricFrame:
        request = GetGatewayStatusSeries(
            gateway_id=gateway_id,
            interval=interval,
            metrics=metrics if metrics else MISSING,
        )
        result = await self._client.get_gateway_status_series(request)
        points = result.series if result.series is not MISSING and result.series else []
        gw_fields = [
            "cpu_pct",
            "memory_pct",
            "flow_count",
            "tunnel_count",
            "tunnel_count_v6",
            "handoff_queue_drops",
            "connected_edges",
        ]
        return denormalize_flat_series(list(points), gw_fields)

    # -----------------------------------------------------------------------
    # Group 12: QoE (keep as-is)
    # -----------------------------------------------------------------------

    async def get_edge_qoe_series(
        self,
        edge_id: int,
        interval: ApiInterval,
        *,
        max_samples: int | None = None,
        links: list[int] | None = None,
    ) -> MetricFrame:
        request = GetLinkQualityEvents(
            edge_id=edge_id,
            interval=interval,
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            max_samples=max_samples if max_samples is not None else MISSING,
            links=links if links else MISSING,
        )
        result = await self._client.get_link_quality_events(request)

        rows: list[dict[str, Any]] = []
        entries: dict[str, LinkQualityData] = result.link_entries
        for link_id, data in entries.items():
            if not data.timeseries or data.timeseries is MISSING:
                continue
            for sample in data.timeseries:
                row: dict[str, Any] = {
                    "timestamp": datetime.fromtimestamp(
                        sample.timestamp / 1000, tz=UTC
                    ),
                    "link_id": link_id,
                }
                if sample.after and sample.after is not MISSING:
                    for traffic_type, quality in sample.after.items():
                        row[f"quality_after_{TrafficType(traffic_type).label}"] = (
                            QualityState(quality).label
                        )
                if sample.before and sample.before is not MISSING:
                    for traffic_type, quality in sample.before.items():
                        row[f"quality_before_{TrafficType(traffic_type).label}"] = (
                            QualityState(quality).label
                        )
                if data.score and data.score is not MISSING:
                    for traffic_type, score in data.score.items():
                        row[f"score_{TrafficType(traffic_type).label}"] = score
                rows.append(row)

        if rows:
            all_cols: dict[str, None] = {}
            for r in rows:
                for k in r:
                    all_cols[k] = None
            return MetricFrame(columns=list(all_cols), rows=rows)
        return MetricFrame(columns=["timestamp", "link_id"], rows=[])

    # -----------------------------------------------------------------------
    # Group 13: NNI (keep as-is)
    # -----------------------------------------------------------------------

    async def get_nni_metrics(
        self,
        interval: ApiInterval,
        *,
        metrics: list[NniMetric] | None = None,
    ) -> MetricFrame:
        request = GetEnterpriseNniMetrics(
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            interval=interval,
            metrics=metrics if metrics else MISSING,
        )
        result = await self._client.get_enterprise_nni_metrics(request)
        return items_to_frame(
            result,
            [
                "gateway_id",
                "gateway_name",
                "nni_id",
                "bytes_rx",
                "bytes_tx",
                "packets_rx",
                "packets_tx",
            ],
        )

    async def get_nni_series(
        self,
        interval: ApiInterval,
        *,
        metrics: list[NniMetric] | None = None,
        limit: int | None = None,
    ) -> MetricFrame:
        request = GetEnterpriseNniSeries(
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            interval=interval,
            metrics=metrics if metrics else MISSING,
            limit=limit if limit is not None else MISSING,
        )
        result = await self._client.get_enterprise_nni_series(request)

        rows: list[dict[str, Any]] = []
        for item in result:
            gw_name = get_field(item, "gateway_name")
            nni_id = get_field(item, "nni_id")
            series = get_field(item, "series")
            if not series:
                continue
            for point in series:
                row: dict[str, Any] = {
                    "gateway_name": gw_name,
                    "nni_id": nni_id,
                }
                if isinstance(point, dict):
                    row.update(point)
                rows.append(row)

        if rows:
            all_cols: dict[str, None] = {}
            for r in rows:
                for k in r:
                    all_cols[k] = None
            return MetricFrame(columns=list(all_cols), rows=rows)
        return MetricFrame(columns=["gateway_name", "nni_id"], rows=[])

    # -----------------------------------------------------------------------
    # Group 14: Event Collection
    # -----------------------------------------------------------------------

    _EVENT_COLUMNS = [
        "event_time",
        "event",
        "category",
        "severity",
        "edge_name",
        "edge_id",
        "segment_name",
        "message",
        "detail",
        "enterprise_username",
    ]

    async def collect_events(
        self,
        interval: ApiInterval,
        *,
        event_types: list[EventType] | None = None,
        severity: list[EventSeverity] | None = None,
        edge_ids: list[int] | None = None,
        string_filters: list[EnterpriseEventStringValueFilter] | None = None,
        max_items: int = 10_000,
        sort_by: list[EventSorting] | None = None,
    ) -> MetricFrame:
        filters: list[
            EnterpriseEventTypeFilter
            | EnterpriseEventSeverityFilter
            | EnterpriseEventStringValueFilter
        ] = []
        if event_types:
            filters.append(
                EnterpriseEventTypeFilter(
                    field="event", operator="in", value=event_types
                )
            )
        if severity:
            filters.append(
                EnterpriseEventSeverityFilter(
                    field="severity", operator="in", value=severity
                )
            )
        if string_filters:
            filters.extend(string_filters)

        request = GetEnterpriseEvents(
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            interval=interval,
            edge_id=edge_ids if edge_ids else MISSING,
            filters=EnterpriseEventFiltersAnd(and_=filters) if filters else MISSING,
            sort_by=sort_by if sort_by else MISSING,
        )
        pages = self._client.portal_paginated(
            "event/getEnterpriseEventsList",
            request,
            GetEnterpriseEventsItem,
        )

        def _extract_event(item: GetEnterpriseEventsItem) -> dict[str, Any]:
            row: dict[str, Any] = {}
            for col in self._EVENT_COLUMNS:
                val = getattr(item, col, None)
                if val is MISSING:
                    val = None
                if isinstance(val, (EventType, EventSeverity)):
                    val = str(val.value)
                row[col] = val
            return row

        return await collect_pages(
            pages,
            columns=self._EVENT_COLUMNS,
            extractor=_extract_event,
            max_items=max_items,
        )

    # -----------------------------------------------------------------------
    # Group 15: Edge Collection
    # -----------------------------------------------------------------------

    _EDGE_COLUMNS = [
        "id",
        "logical_id",
        "name",
        "edge_state",
        "activation_state",
        "service_state",
        "ha_state",
        "model_number",
        "device_family",
        "software_version",
        "serial_number",
        "description",
        "last_contact",
        "created",
        "site_id",
    ]

    async def collect_edges(
        self,
        *,
        edge_state: list[EdgeState] | None = None,
        activation_state: list[ActivationState] | None = None,
        service_state: list[EdgeServiceState] | None = None,
        ha_state: list[HaState] | None = None,
        model_number: list[str] | None = None,
        name: str | None = None,
        edge_ids: list[int] | None = None,
        max_items: int = 10_000,
    ) -> MetricFrame:
        """Collect enterprise edges, applying state and identity filters.

        The edge-list filter schema supports ``haState`` but not
        ``serviceState``. Service-state filtering is therefore applied to the
        collected rows locally.
        """
        filters: list[EdgeFilter] = []
        if edge_state:
            filters.append(
                EdgeStateFilter(field="edgeState", operator="in", value=edge_state)
            )
        if activation_state:
            filters.append(
                EdgeActivationStateFilter(
                    field="activationState", operator="in", value=activation_state
                )
            )
        if ha_state:
            filters.append(
                EdgeHaStateFilter(field="haState", operator="in", value=ha_state)
            )
        if model_number:
            filters.append(
                EdgeStringValueFilter(
                    field="modelNumber", operator="in", value=model_number
                )
            )
        if name:
            filters.append(
                EdgeStringValueFilter(field="name", operator="is", value=name)
            )

        request = GetEnterpriseEdges(
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            edge_ids=edge_ids if edge_ids else MISSING,
            filters=EdgeFiltersAnd(and_=filters) if filters else MISSING,
        )
        pages = self._client.portal_paginated(
            "enterprise/getEnterpriseEdges",
            request,
            GetEnterpriseEdgesItem,
        )

        def _extract_edge(item: GetEnterpriseEdgesItem) -> dict[str, Any]:
            row: dict[str, Any] = {}
            for col in self._EDGE_COLUMNS:
                attr = "id_" if col == "id" else col
                val = getattr(item, attr, None)
                if val is MISSING:
                    val = None
                if isinstance(val, StrEnum):
                    val = str(val.value)
                row[col] = val
            return row

        frame = await collect_pages(
            pages,
            columns=self._EDGE_COLUMNS,
            extractor=_extract_edge,
            max_items=max_items,
        )
        if service_state:
            frame = frame.filter(
                "service_state", "in", [state.value for state in service_state]
            )
        return frame

    # -----------------------------------------------------------------------
    # Group 16: Configuration Profile Collection
    # -----------------------------------------------------------------------

    _CONFIGURATION_COLUMNS = [
        "id",
        "logical_id",
        "name",
        "configuration_type",
        "description",
        "edge_count",
        "version",
        "schema_version",
        "created",
        "modified",
        "effective",
        "is_staging",
        "bastion_state",
    ]

    async def collect_configurations(
        self,
        *,
        with_edge_count: bool = True,
        max_items: int | None = None,
    ) -> MetricFrame:
        with_: list[GetEnterpriseConfigurationsWith] = []
        if with_edge_count:
            with_.append(GetEnterpriseConfigurationsWith.EDGE_COUNT)

        request = GetEnterpriseConfigurations(
            enterprise_id=(
                self._enterprise_id if self._enterprise_id is not None else MISSING
            ),
            with_=with_ if with_ else MISSING,
        )
        items = await self._client.portal_list(
            "enterprise/getEnterpriseConfigurations",
            request,
            EnterpriseConfigurationProfile,
        )

        rows: list[dict[str, Any]] = []
        for item in items:
            row: dict[str, Any] = {}
            for col in self._CONFIGURATION_COLUMNS:
                attr = "id_" if col == "id" else col
                val = getattr(item, attr, None)
                if val is MISSING:
                    val = None
                if isinstance(val, StrEnum):
                    val = str(val.value)
                row[col] = val
            rows.append(row)
        if max_items is not None:
            rows = rows[:max_items]
        return MetricFrame(columns=self._CONFIGURATION_COLUMNS, rows=rows)
