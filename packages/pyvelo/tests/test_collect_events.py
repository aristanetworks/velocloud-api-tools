from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import MISSING
from pyvelo.analytics.analytics import Analytics
from pyvelo.client.enums.event import EventSeverity, EventType
from pyvelo.client.schemas.event import (
    EnterpriseEventFiltersAnd,
    EnterpriseEventSeverityFilter,
    EnterpriseEventStringValueFilter,
    EnterpriseEventTypeFilter,
    GetEnterpriseEvents,
    GetEnterpriseEventsItem,
)
from pyvelo.core import ApiInterval

from ._type_support import MockPaginatedClient


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


def _make_client(
    pages: list[list[GetEnterpriseEventsItem]],
    *,
    on_page: Callable[[int], None] | None = None,
) -> MockPaginatedClient[GetEnterpriseEvents, GetEnterpriseEventsItem]:
    return MockPaginatedClient(pages, on_page=on_page)


@pytest.fixture
def interval() -> ApiInterval:
    return ApiInterval(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 2, tzinfo=UTC),
    )


class TestCollectEventsFilterPushdown:
    @pytest.mark.anyio
    async def test_no_filters(self, interval: ApiInterval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client.protocol, enterprise_id=1)
        result = await analytics.collect_events(interval)
        assert len(result) == 1
        req = client.captured_requests[0]
        assert req.filters is MISSING

    @pytest.mark.anyio
    async def test_event_type_filter(self, interval: ApiInterval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client.protocol, enterprise_id=1)
        _ = await analytics.collect_events(
            interval, event_types=[EventType.EDGE_DOWN, EventType.EDGE_UP]
        )
        req = client.captured_requests[0]
        assert req.filters is not MISSING
        assert isinstance(req.filters, EnterpriseEventFiltersAnd)
        filters = req.filters.and_
        event_filter = filters[0]
        assert isinstance(event_filter, EnterpriseEventTypeFilter)
        assert event_filter.field == "event"
        assert event_filter.operator == "in"
        assert EventType.EDGE_DOWN in event_filter.value
        assert EventType.EDGE_UP in event_filter.value

    @pytest.mark.anyio
    async def test_severity_filter(self, interval: ApiInterval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client.protocol, enterprise_id=1)
        _ = await analytics.collect_events(interval, severity=[EventSeverity.CRITICAL])
        req = client.captured_requests[0]
        assert isinstance(req.filters, EnterpriseEventFiltersAnd)
        filters = req.filters.and_
        sev_filter = filters[0]
        assert isinstance(sev_filter, EnterpriseEventSeverityFilter)
        assert sev_filter.field == "severity"
        assert sev_filter.operator == "in"

    @pytest.mark.anyio
    async def test_edge_id_filter(self, interval: ApiInterval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client.protocol, enterprise_id=1)
        _ = await analytics.collect_events(interval, edge_ids=[100, 200])
        req = client.captured_requests[0]
        assert req.edge_id == [100, 200]

    @pytest.mark.anyio
    async def test_string_filter(self, interval: ApiInterval):
        string_filter = EnterpriseEventStringValueFilter(
            field="message", operator="contains", value="down"
        )
        client = _make_client([[_make_event()]])
        analytics = Analytics(client.protocol, enterprise_id=1)
        _ = await analytics.collect_events(interval, string_filters=[string_filter])
        req = client.captured_requests[0]
        assert isinstance(req.filters, EnterpriseEventFiltersAnd)
        filters = req.filters.and_
        assert any(
            isinstance(f, EnterpriseEventStringValueFilter) and f.field == "message"
            for f in filters
        )

    @pytest.mark.anyio
    async def test_combined_filters(self, interval: ApiInterval):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client.protocol, enterprise_id=1)
        _ = await analytics.collect_events(
            interval,
            event_types=[EventType.EDGE_DOWN],
            severity=[EventSeverity.CRITICAL],
            edge_ids=[100],
        )
        req = client.captured_requests[0]
        assert req.edge_id == [100]
        assert isinstance(req.filters, EnterpriseEventFiltersAnd)
        filters = req.filters.and_
        assert len(filters) == 2


class TestCollectEventsEarlyTermination:
    @pytest.mark.anyio
    async def test_stops_at_max_items(self, interval: ApiInterval):
        page1 = [_make_event(edge_name=f"e{i}") for i in range(5)]
        page2 = [_make_event(edge_name=f"e{i}") for i in range(5, 10)]
        page3 = [_make_event(edge_name=f"e{i}") for i in range(10, 15)]

        pages_yielded: list[int] = []
        client = _make_client([page1, page2, page3], on_page=pages_yielded.append)

        analytics = Analytics(client.protocol, enterprise_id=1)
        result = await analytics.collect_events(interval, max_items=7)

        assert len(result) == 7
        assert len(pages_yielded) == 2

    @pytest.mark.anyio
    async def test_exact_max_items(self, interval: ApiInterval):
        page = [_make_event() for _ in range(5)]
        client = _make_client([page])
        analytics = Analytics(client.protocol, enterprise_id=1)
        result = await analytics.collect_events(interval, max_items=5)
        assert len(result) == 5

    @pytest.mark.anyio
    async def test_fewer_than_max(self, interval: ApiInterval):
        page = [_make_event() for _ in range(3)]
        client = _make_client([page])
        analytics = Analytics(client.protocol, enterprise_id=1)
        result = await analytics.collect_events(interval, max_items=100)
        assert len(result) == 3


class TestCollectEventsOutput:
    @pytest.mark.anyio
    async def test_returns_metric_frame_with_expected_columns(
        self, interval: ApiInterval
    ):
        client = _make_client([[_make_event()]])
        analytics = Analytics(client.protocol, enterprise_id=1)
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
    async def test_event_values_are_strings(self, interval: ApiInterval):
        client = _make_client([[_make_event(event="EDGE_DOWN", severity="CRITICAL")]])
        analytics = Analytics(client.protocol, enterprise_id=1)
        result = await analytics.collect_events(interval)
        row = result.rows[0]
        assert row["event"] == "EDGE_DOWN"
        assert row["severity"] == "CRITICAL"

    @pytest.mark.anyio
    async def test_chainable_with_frame_ops(self, interval: ApiInterval):
        events = [
            _make_event(event="EDGE_DOWN", severity="CRITICAL"),
            _make_event(event="EDGE_UP", severity="INFO"),
            _make_event(event="EDGE_DOWN", severity="CRITICAL"),
        ]
        client = _make_client([events])
        analytics = Analytics(client.protocol, enterprise_id=1)
        result = await analytics.collect_events(interval)
        grouped = result.group_by("event").agg(count="n")
        assert len(grouped) == 2
        top = result.top_n(1, by="event", descending=True)
        assert len(top) == 1
