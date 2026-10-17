from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pyvelo.analytics._helpers import get_field, is_other_bucket
from pyvelo.analytics.frame import MetricFrame
from pyvelo.core.base import VcoModel


def denormalize_timeseries(
    items: Sequence[VcoModel | dict[str, Any]],
    group_key: str,
    group_field: str = "name",
    timeseries_field: str = "series",
) -> MetricFrame:
    """Convert structured time-series responses into flat rows.

    Takes items shaped like::

        [
            {name: "app1", time_series: [
                {metric: "bytesRx", start_time: ..., tick_interval: 300000, data: [1,2,3]},
                {metric: "bytesTx", start_time: ..., tick_interval: 300000, data: [4,5,6]},
            ]},
            ...
        ]

    And produces flat rows::

        [
            {timestamp: ..., name: "app1", bytes_rx: 1, bytes_tx: 4},
            {timestamp: ..., name: "app1", bytes_rx: 2, bytes_tx: 5},
            ...
        ]
    """
    if not items:
        return MetricFrame(columns=["timestamp", group_key], rows=[])

    rows: list[dict[str, Any]] = []
    metric_names: set[str] = set()

    for item in items:
        raw_group = get_field(item, group_field)
        if is_other_bucket(raw_group):
            continue
        group_value = raw_group
        series_list = get_field(item, timeseries_field)
        if not series_list:
            continue

        ts_by_metric: dict[str, tuple[float, float, list[float | None]]] = {}
        for ts in series_list:
            metric = get_field(ts, "metric")
            start_time = get_field(ts, "start_time")
            tick_interval = get_field(ts, "tick_interval")
            data = get_field(ts, "data")
            if metric is None or data is None:
                continue
            col_name = _camel_to_snake(str(metric))
            metric_names.add(col_name)
            start_ms = _to_millis(start_time)
            ts_by_metric[col_name] = (start_ms, tick_interval, data)

        if not ts_by_metric:
            continue

        first_col = next(iter(ts_by_metric))
        start_ms, tick_interval, data = ts_by_metric[first_col]
        num_points = len(data)

        for i in range(num_points):
            ts_ms = start_ms + (i * tick_interval)
            row: dict[str, Any] = {
                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                group_key: group_value,
            }
            for col_name, (_, _, col_data) in ts_by_metric.items():
                row[col_name] = col_data[i] if i < len(col_data) else None
            rows.append(row)

    columns = ["timestamp", group_key, *sorted(metric_names)]
    return MetricFrame(columns=columns, rows=rows)


def denormalize_flat_series(
    points: list[VcoModel | dict[str, Any]],
    metric_fields: list[str],
) -> MetricFrame:
    """Convert flat series points (like edge/gateway status series) into rows.

    These responses have shape::

        {series: [{start_time: ..., cpu_pct: 5.0, memory_pct: 30.0}, ...]}

    Each point is already a flat row keyed by start_time.
    """
    if not points:
        return MetricFrame(columns=["timestamp", *metric_fields], rows=[])

    rows: list[dict[str, Any]] = []
    for point in points:
        start_time = get_field(point, "start_time")
        row: dict[str, Any] = {
            "timestamp": _to_datetime(start_time),
        }
        for field in metric_fields:
            row[field] = get_field(point, field)
        rows.append(row)

    return MetricFrame(columns=["timestamp", *metric_fields], rows=rows)


def _to_millis(value: Any) -> float:
    if isinstance(value, (int, float)):
        if value > 1e12:
            return float(value)
        return float(value) * 1000
    if isinstance(value, datetime):
        return value.timestamp() * 1000
    if hasattr(value, "timestamp"):
        return value.timestamp() * 1000
    return 0.0


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        if value > 1e12:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        return datetime.fromtimestamp(value, tz=UTC)
    if hasattr(value, "timestamp"):
        return datetime.fromtimestamp(value.timestamp(), tz=UTC)
    return None


def _camel_to_snake(name: str) -> str:
    result: list[str] = []
    for i, c in enumerate(name):
        if c.isupper():
            if (
                i > 0
                and not name[i - 1].isupper()
                or (
                    i > 0
                    and i + 1 < len(name)
                    and name[i - 1].isupper()
                    and not name[i + 1].isupper()
                )
            ):
                result.append("_")
            result.append(c.lower())
        else:
            result.append(c)
    return "".join(result)
