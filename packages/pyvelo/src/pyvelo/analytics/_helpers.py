from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import MISSING

from pyvelo.analytics.frame import MetricFrame
from pyvelo.core.base import VcoModel


def is_other_bucket(value: Any) -> bool:
    return isinstance(value, str) and value.lower() == "other"


def get_field(obj: VcoModel | dict[str, Any], field: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(field)
    val = getattr(obj, field, None)
    if val is MISSING:
        return None
    return val


def items_to_frame(
    items: Sequence[VcoModel] | Sequence[dict[str, Any]],
    fields: list[str],
) -> MetricFrame:
    if not items:
        return MetricFrame(columns=fields, rows=[])

    has_name = "name" in fields
    rows: list[dict[str, Any]] = []
    for item in items:
        if has_name:
            name = get_field(item, "name")
            if is_other_bucket(name):
                continue
        row: dict[str, Any] = {}
        for field in fields:
            val = get_field(item, field)
            row[field] = val
        rows.append(row)

    return MetricFrame(columns=fields, rows=rows)
