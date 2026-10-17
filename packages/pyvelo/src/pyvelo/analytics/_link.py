"""Aggregate link mapping with explicit source scope and entity identities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_core import MISSING

from pyvelo.analytics._helpers import get_field
from pyvelo.analytics.frame import MetricFrame
from pyvelo.client.enums.metrics import AggregateEdgeLinkMetric
from pyvelo.client.exceptions import VcoOperationError
from pyvelo.client.schemas.metrics import (
    AggregateEdgeLinkMetricsItem,
    GetAggregateEdgeLinkMetrics,
)
from pyvelo.core import ApiInterval

if TYPE_CHECKING:
    from pyvelo.client.protocol import VcoClientProtocol

_IDENTITY_FIELDS = [
    "enterprise_id",
    "enterprise_name",
    "edge_id",
    "edge_name",
    "internal_id",
]
_METRIC_FIELDS = [
    field for field in AggregateEdgeLinkMetricsItem.model_fields if field != "link"
]
_COLUMNS = [*_METRIC_FIELDS, *_IDENTITY_FIELDS, "link", "details"]
_OPERATION = "monitoring/getAggregateEdgeLinkMetrics"


async def get_aggregate_link_metrics(
    client: VcoClientProtocol,
    interval: ApiInterval,
    *,
    enterprise_id: int | None = None,
    metrics: list[AggregateEdgeLinkMetric] | None = None,
) -> MetricFrame:
    request = GetAggregateEdgeLinkMetrics(
        enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
        interval=interval,
        metrics=metrics if metrics else MISSING,
    )
    items = await client.get_aggregate_edge_link_metrics(request)
    rows: list[dict[str, Any]] = []
    for item in items:
        link = get_field(item, "link")
        row = {field: get_field(item, field) for field in _METRIC_FIELDS}
        row.update(
            {
                field: get_field(link, field) if link is not None else None
                for field in _IDENTITY_FIELDS
            }
        )
        if enterprise_id is not None and row["enterprise_id"] not in (
            None,
            enterprise_id,
        ):
            raise VcoOperationError(
                _OPERATION, "Returned link belongs to another enterprise"
            )
        nested_id = get_field(link, "link_id") if link is not None else None
        if (
            row["link_id"] is not None
            and nested_id is not None
            and row["link_id"] != nested_id
        ):
            raise VcoOperationError(_OPERATION, "Returned link identifiers conflict")
        if row["link_id"] is None:
            row["link_id"] = nested_id
        row["link"] = (
            link.model_dump(mode="json", by_alias=True) if link is not None else None
        )
        # Native structs normalize absent fields to NULL across rows. Retain the
        # complete typed source as JSON to preserve missing/null distinctions.
        row["details"] = item.model_dump_json(by_alias=True)
        rows.append(row)
    # A real link may be called "other"; do not discard named rows as buckets.
    return MetricFrame(columns=_COLUMNS, rows=rows)
