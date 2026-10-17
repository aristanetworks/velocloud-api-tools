from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any

from pydantic import BaseModel

from pyvelo.analytics._helpers import get_field
from pyvelo.analytics.frame import MetricFrame

log = logging.getLogger(__name__)

type FieldExtractor[T] = Callable[[T], dict[str, Any]]


def default_extractor(columns: list[str]) -> FieldExtractor[Any]:
    def _extract(item: Any) -> dict[str, Any]:
        return {col: get_field(item, col) for col in columns}

    return _extract


async def collect_pages[T: BaseModel](
    pages: AsyncGenerator[list[T]],
    *,
    columns: list[str],
    extractor: FieldExtractor[T] | None = None,
    max_items: int = 10_000,
) -> MetricFrame:
    extract = extractor or default_extractor(columns)
    rows: list[dict[str, Any]] = []
    async for page in pages:
        for item in page:
            if len(rows) >= max_items:
                break
            rows.append(extract(item))
        if len(rows) >= max_items:
            log.debug(
                "collect_pages: reached max_items=%d, stopping pagination", max_items
            )
            break
    return MetricFrame(columns=columns, rows=rows)
