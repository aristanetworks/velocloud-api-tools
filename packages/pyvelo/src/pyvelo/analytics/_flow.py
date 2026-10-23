from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import MISSING

from pyvelo.analytics._helpers import is_other_bucket
from pyvelo.analytics._series import denormalize_timeseries
from pyvelo.analytics.enums import FlowGroupBy, FlowMetric
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.enums.metrics import (
    AppLinkMetric,
    AppMetric,
    CategoryMetric,
    DestAttribute,
    DestMetric,
    DeviceMetric,
    SegmentMetric,
)
from pyvelo.client.exceptions import VcoEmptyResultError
from pyvelo.client.schemas.metrics import (
    EdgeAppLinkMetricsItem,
    EdgeAppMetricsItem,
    EdgeCategoryMetricsItem,
    EdgeDestMetricsItem,
    EdgeDeviceMetricsItem,
    EdgeSegmentMetricsItem,
    EnterpriseFlowMetricsFilter,
    GetEdgeAppLinkMetrics,
    GetEdgeAppLinkSeries,
    GetEdgeAppMetrics,
    GetEdgeAppSeries,
    GetEdgeCategoryMetrics,
    GetEdgeCategorySeries,
    GetEdgeDestMetrics,
    GetEdgeDestSeries,
    GetEdgeDeviceMetrics,
    GetEdgeDeviceSeries,
    GetEdgeSegmentMetrics,
    GetEdgeSegmentSeries,
)
from pyvelo.core import ApiInterval

if __builtins__:  # type: ignore[truthy-bool]
    from pyvelo.client.protocol import VcoClientProtocol

type _FlowMetricsItems = (
    Sequence[EdgeAppMetricsItem]
    | Sequence[EdgeDeviceMetricsItem]
    | Sequence[EdgeCategoryMetricsItem]
    | Sequence[EdgeDestMetricsItem]
    | Sequence[EdgeSegmentMetricsItem]
    | Sequence[EdgeAppLinkMetricsItem]
)

_METRIC_ENUM_MAP: dict[FlowGroupBy, type] = {
    FlowGroupBy.APP: AppMetric,
    FlowGroupBy.SOURCE: DeviceMetric,
    FlowGroupBy.CATEGORY: CategoryMetric,
    FlowGroupBy.DEST_IP: DestMetric,
    FlowGroupBy.DEST_FQDN: DestMetric,
    FlowGroupBy.DEST_DOMAIN: DestMetric,
    FlowGroupBy.SEGMENT: SegmentMetric,
    FlowGroupBy.APP_LINK: AppLinkMetric,
}

_DEST_ATTR_MAP: dict[FlowGroupBy, DestAttribute] = {
    FlowGroupBy.DEST_IP: DestAttribute.DEST_IP,
    FlowGroupBy.DEST_FQDN: DestAttribute.DEST_FQDN,
    FlowGroupBy.DEST_DOMAIN: DestAttribute.DEST_DOMAIN,
}

_METRICS_FIELDS = [
    "packets_rx",
    "packets_tx",
    "total_packets",
    "bytes_rx",
    "bytes_tx",
    "total_bytes",
    "flow_count",
]


def _convert_metrics(
    group_by: FlowGroupBy,
    metrics: list[FlowMetric] | None,
) -> list[Any] | MISSING:
    if metrics is None:
        return MISSING
    enum_cls = _METRIC_ENUM_MAP[group_by]
    return [enum_cls(m.value) for m in metrics]


def _convert_sort(
    group_by: FlowGroupBy,
    sort: FlowMetric | None,
) -> Any:
    if sort is None:
        return MISSING
    enum_cls = _METRIC_ENUM_MAP[group_by]
    return enum_cls(sort.value)


async def get_edge_flow_metrics(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    group_by: FlowGroupBy,
    *,
    enterprise_id: int | None = None,
    limit: int | None = None,
    sort: FlowMetric | None = None,
    metrics: list[FlowMetric] | None = None,
    filter_values: list[str | int] | None = None,
    filters: list[EnterpriseFlowMetricsFilter] | None = None,
) -> MetricFrame:
    try:
        return await _get_edge_flow_metrics(
            client,
            edge_id,
            interval,
            group_by,
            enterprise_id=enterprise_id,
            limit=limit,
            sort=sort,
            metrics=metrics,
            filter_values=filter_values,
            filters=filters,
        )
    except VcoEmptyResultError:
        return MetricFrame(columns=["name", *_METRICS_FIELDS], rows=[])


async def _get_edge_flow_metrics(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    group_by: FlowGroupBy,
    *,
    enterprise_id: int | None = None,
    limit: int | None = None,
    sort: FlowMetric | None = None,
    metrics: list[FlowMetric] | None = None,
    filter_values: list[str | int] | None = None,
    filters: list[EnterpriseFlowMetricsFilter] | None = None,
) -> MetricFrame:
    converted_metrics = _convert_metrics(group_by, metrics)
    converted_sort = _convert_sort(group_by, sort)
    limit_val = limit if limit is not None else MISSING
    eid = enterprise_id if enterprise_id is not None else MISSING
    filters_val = filters if filters else MISSING

    match group_by:
        case FlowGroupBy.APP:
            apps = [str(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeAppMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                applications=apps,
                filters=filters_val,
            )
            result = await client.get_edge_app_metrics(request)
            return _items_to_frame(result)

        case FlowGroupBy.SOURCE:
            devices = [str(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeDeviceMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                devices=devices,
                filters=filters_val,
            )
            result = await client.get_edge_device_metrics(request)
            return _items_to_frame(result)

        case FlowGroupBy.CATEGORY:
            cats = [int(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeCategoryMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                categories=cats,
                filters=filters_val,
            )
            result = await client.get_edge_category_metrics(request)
            return _items_to_frame(result)

        case FlowGroupBy.DEST_IP | FlowGroupBy.DEST_FQDN | FlowGroupBy.DEST_DOMAIN:
            dests = [str(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeDestMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                destinations=dests,
                attribute=_DEST_ATTR_MAP[group_by],
                filters=filters_val,
            )
            result = await client.get_edge_dest_metrics(request)
            return _items_to_frame(result)

        case FlowGroupBy.SEGMENT:
            segs = [int(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeSegmentMetrics(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                segments=segs,
                filters=filters_val,
            )
            result = await client.get_edge_segment_metrics(request)
            return _items_to_frame(result)

        case FlowGroupBy.APP_LINK:
            request = GetEdgeAppLinkMetrics(
                id_=edge_id,
                interval=interval,
                enterprise_id=eid,
                metrics=converted_metrics,
                filters=filters_val,
            )
            result = await client.get_edge_app_link_metrics(request)
            return _items_to_frame(result)

    raise ValueError(f"Unknown group_by: {group_by}")


async def get_edge_flow_series(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    group_by: FlowGroupBy,
    *,
    enterprise_id: int | None = None,
    max_samples: int | None = None,
    limit: int | None = None,
    sort: FlowMetric | None = None,
    metrics: list[FlowMetric] | None = None,
    filter_values: list[str | int] | None = None,
    filters: list[EnterpriseFlowMetricsFilter] | None = None,
) -> MetricFrame:
    try:
        return await _get_edge_flow_series(
            client,
            edge_id,
            interval,
            group_by,
            enterprise_id=enterprise_id,
            max_samples=max_samples,
            limit=limit,
            sort=sort,
            metrics=metrics,
            filter_values=filter_values,
            filters=filters,
        )
    except VcoEmptyResultError:
        return MetricFrame(columns=["name", *_METRICS_FIELDS], rows=[])


async def _get_edge_flow_series(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    group_by: FlowGroupBy,
    *,
    enterprise_id: int | None = None,
    max_samples: int | None = None,
    limit: int | None = None,
    sort: FlowMetric | None = None,
    metrics: list[FlowMetric] | None = None,
    filter_values: list[str | int] | None = None,
    filters: list[EnterpriseFlowMetricsFilter] | None = None,
) -> MetricFrame:
    converted_metrics = _convert_metrics(group_by, metrics)
    converted_sort = _convert_sort(group_by, sort)
    limit_val = limit if limit is not None else MISSING
    max_samples_val = max_samples if max_samples is not None else MISSING
    eid = enterprise_id if enterprise_id is not None else MISSING
    filters_val = filters if filters else MISSING

    match group_by:
        case FlowGroupBy.APP:
            apps = [int(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeAppSeries(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                applications=apps,
                max_samples=max_samples_val,
                filters=filters_val,
            )
            result = await client.get_edge_app_series(request)
            return denormalize_timeseries(list(result), "name")

        case FlowGroupBy.SOURCE:
            devices = [str(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeDeviceSeries(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                devices=devices,
                max_samples=max_samples_val,
                filters=filters_val,
            )
            result = await client.get_edge_device_series(request)
            return denormalize_timeseries(list(result), "name")

        case FlowGroupBy.CATEGORY:
            cats = [int(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeCategorySeries(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                categories=cats,
                max_samples=max_samples_val,
                filters=filters_val,
            )
            result = await client.get_edge_category_series(request)
            return denormalize_timeseries(list(result), "name")

        case FlowGroupBy.DEST_IP | FlowGroupBy.DEST_FQDN | FlowGroupBy.DEST_DOMAIN:
            dests = [str(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeDestSeries(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                destinations=dests,
                attribute=_DEST_ATTR_MAP[group_by],
                filters=filters_val,
            )
            result = await client.get_edge_dest_series(request)
            return denormalize_timeseries(list(result), "name")

        case FlowGroupBy.SEGMENT:
            segs = [int(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeSegmentSeries(
                edge_id=edge_id,
                interval=interval,
                enterprise_id=eid,
                limit=limit_val,
                sort=converted_sort,
                metrics=converted_metrics,
                segments=segs,
                max_samples=max_samples_val,
                filters=filters_val,
            )
            result = await client.get_edge_segment_series(request)
            return denormalize_timeseries(list(result), "name")

        case FlowGroupBy.APP_LINK:
            links = [int(v) for v in filter_values] if filter_values else MISSING
            request = GetEdgeAppLinkSeries(
                id_=edge_id,
                interval=interval,
                enterprise_id=eid,
                metrics=converted_metrics,
                links=links,
                filters=filters_val,
            )
            result = await client.get_edge_app_link_series(request)
            return denormalize_timeseries(list(result), "name")

    raise ValueError(f"Unknown group_by: {group_by}")


def _items_to_frame(items: _FlowMetricsItems) -> MetricFrame:
    if not items:
        return MetricFrame(columns=["name", *_METRICS_FIELDS], rows=[])

    rows: list[dict[str, Any]] = []
    for item in items:
        name = item.name
        if name is MISSING:
            continue
        if is_other_bucket(name):
            continue
        row: dict[str, Any] = {"name": name}
        for field in _METRICS_FIELDS:
            val = getattr(item, field, MISSING)
            row[field] = None if val is MISSING else val
        rows.append(row)

    return MetricFrame(columns=["name", *_METRICS_FIELDS], rows=rows)
