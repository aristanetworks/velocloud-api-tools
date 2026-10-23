from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import MISSING
from pyvelo.analytics.analytics import Analytics
from pyvelo.client.enums.event import EventSeverity, EventType
from pyvelo.client.schemas.event import (
    EnterpriseEventStringValueFilter,
    GetEnterpriseEvents,
    GetEnterpriseEventsItem,
)
from pyvelo.core import ApiInterval


def _make_event(
    event: str = "EDGE_DOWN",
    severity: str = "CRITICAL",
    edge_name: str = "edge-1",
    edge_id: int = 100,
    message: str = "Edge went down",
) -> GetEnterpriseEventsItem:
    return GetEnterpriseEventsItem(
        logical_id=uuid.uuid4(),
        enterprise_name="test-ent",
        created=datetime.now(UTC),
        event_time=datetime.now(UTC),
        event=event,
        category="SYSTEM",
        severity=severity,
        message=message,
        detail="{}",
        edge_name=edge_name,
        edge_id=edge_id,
    )


def _make_client(pages: list[list[GetEnterpriseEventsItem]]) -> Any:
    client = MagicMock()
    captured_requests: list[GetEnterpriseEvents] = []

    async def _portal_paginated(method, request, item_type):
        captured_requests.append(request)
        for page in pages:
            yield page

    client.portal_paginated = _portal_paginated
    client._captured_requests = captured_requests
    return client


@pytest.fixture
def interval():
    return ApiInterval(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 2, tzinfo=UTC),
    )


class TestCollectEventsFilterPushdown:
    @pytest.mark.anyio
    async def test_no_filters(self, interval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_events(interval)
        assert len(result) == 1
        req = client._captured_requests[0]
        assert req.filters is MISSING

    @pytest.mark.anyio
    async def test_event_type_filter(self, interval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_events(
            interval, event_types=[EventType.EDGE_DOWN, EventType.EDGE_UP]
        )
        req = client._captured_requests[0]
        assert req.filters is not MISSING
        filters = req.filters.and_
        event_filter = filters[0]
        assert event_filter.field == "event"
        assert event_filter.operator == "in"
        assert EventType.EDGE_DOWN in event_filter.value
        assert EventType.EDGE_UP in event_filter.value

    @pytest.mark.anyio
    async def test_severity_filter(self, interval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_events(interval, severity=[EventSeverity.CRITICAL])
        req = client._captured_requests[0]
        filters = req.filters.and_
        sev_filter = filters[0]
        assert sev_filter.field == "severity"
        assert sev_filter.operator == "in"

    @pytest.mark.anyio
    async def test_edge_id_filter(self, interval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_events(interval, edge_ids=[100, 200])
        req = client._captured_requests[0]
        assert req.edge_id == [100, 200]

    @pytest.mark.anyio
    async def test_string_filter(self, interval):
        string_filter = EnterpriseEventStringValueFilter(
            field="message", operator="contains", value="down"
        )
        client = _make_client([[_make_event()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_events(interval, string_filters=[string_filter])
        req = client._captured_requests[0]
        filters = req.filters.and_
        assert any(getattr(f, "field", None) == "message" for f in filters)

    @pytest.mark.anyio
    async def test_combined_filters(self, interval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_events(
            interval,
            event_types=[EventType.EDGE_DOWN],
            severity=[EventSeverity.CRITICAL],
            edge_ids=[100],
        )
        req = client._captured_requests[0]
        assert req.edge_id == [100]
        filters = req.filters.and_
        assert len(filters) == 2


class TestCollectEventsEarlyTermination:
    @pytest.mark.anyio
    async def test_stops_at_max_items(self, interval):
        page1 = [_make_event(edge_name=f"e{i}") for i in range(5)]
        page2 = [_make_event(edge_name=f"e{i}") for i in range(5, 10)]
        page3 = [_make_event(edge_name=f"e{i}") for i in range(10, 15)]

        pages_yielded = []

        original_client = _make_client([page1, page2, page3])
        client = MagicMock()
        client._captured_requests = original_client._captured_requests

        async def _tracking_paginated(method, request, item_type):
            for i, page in enumerate([page1, page2, page3]):
                pages_yielded.append(i)
                yield page

        client.portal_paginated = _tracking_paginated

        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_events(interval, max_items=7)

        assert len(result) == 7
        assert len(pages_yielded) == 2

    @pytest.mark.anyio
    async def test_exact_max_items(self, interval):
        page = [_make_event() for _ in range(5)]
        client = _make_client([page])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_events(interval, max_items=5)
        assert len(result) == 5

    @pytest.mark.anyio
    async def test_fewer_than_max(self, interval):
        page = [_make_event() for _ in range(3)]
        client = _make_client([page])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_events(interval, max_items=100)
        assert len(result) == 3


class TestCollectEventsOutput:
    @pytest.mark.anyio
    async def test_returns_metric_frame_with_expected_columns(self, interval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_events(interval)
        expected = [
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
        assert result.columns == expected

    @pytest.mark.anyio
    async def test_event_values_are_strings(self, interval):
        client = _make_client([[_make_event(event="EDGE_DOWN", severity="CRITICAL")]])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_events(interval)
        row = result.rows[0]
        assert row["event"] == "EDGE_DOWN"
        assert row["severity"] == "CRITICAL"

    @pytest.mark.anyio
    async def test_chainable_with_frame_ops(self, interval):
        events = [
            _make_event(event="EDGE_DOWN", severity="CRITICAL"),
            _make_event(event="EDGE_UP", severity="INFO"),
            _make_event(event="EDGE_DOWN", severity="CRITICAL"),
        ]
        client = _make_client([events])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_events(interval)
        grouped = result.group_by("event").agg(count="n")
        assert len(grouped) == 2
        top = result.top_n(1, by="event", descending=True)
        assert len(top) == 1
