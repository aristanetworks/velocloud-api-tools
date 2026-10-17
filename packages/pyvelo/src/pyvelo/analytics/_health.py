from __future__ import annotations

from typing import Any

from pydantic_core import MISSING

from pyvelo.analytics._series import denormalize_flat_series
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.enums.metrics import StatusMetric
from pyvelo.client.schemas.metrics import (
    GetEdgeStatusMetrics,
    GetEdgeStatusSeries,
)
from pyvelo.core import ApiInterval

if __builtins__:  # type: ignore[truthy-bool]
    from pyvelo.client.protocol import VcoClientProtocol


_HEALTH_METRIC_FIELDS = [
    "cpu_pct",
    "memory_pct",
    "flow_count",
    "tunnel_count",
    "tunnel_count_v6",
    "handoff_queue_drops",
    "cpu_core_temp",
]


async def get_edge_health_metrics(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    *,
    enterprise_id: int | None = None,
    metrics: list[StatusMetric] | None = None,
) -> MetricFrame:
    request = GetEdgeStatusMetrics(
        edge_id=edge_id,
        interval=interval,
        enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
        metrics=metrics if metrics else list(StatusMetric),
    )
    result = await client.get_edge_status_metrics(request)

    row: dict[str, Any] = {}
    for field in _HEALTH_METRIC_FIELDS:
        val = getattr(result, field, None)
        row[field] = None if (val is MISSING or val is None) else val

    return MetricFrame(columns=_HEALTH_METRIC_FIELDS, rows=[row])


async def get_edge_health_series(
    client: VcoClientProtocol,
    edge_id: int,
    interval: ApiInterval,
    *,
    enterprise_id: int | None = None,
    metrics: list[StatusMetric] | None = None,
) -> MetricFrame:
    request = GetEdgeStatusSeries(
        edge_id=edge_id,
        interval=interval,
        enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
        metrics=metrics if metrics else list(StatusMetric),
    )
    result = await client.get_edge_status_series(request)

    points = result.series if result.series is not MISSING and result.series else []
    return denormalize_flat_series(list(points), _HEALTH_METRIC_FIELDS)
