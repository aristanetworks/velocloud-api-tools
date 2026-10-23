from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError
from pyvelo.analytics.analytics import Analytics
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.link_quality import (
    GetLinkQualityEvents,
    GetLinkQualityEventsResult,
)
from pyvelo.client.schemas.metrics import EdgeLinkSeriesItem, GetEdgeLinkSeries
from pyvelo.core import ApiInterval


@pytest.fixture
def interval() -> ApiInterval:
    return ApiInterval(start=datetime(2026, 1, 1, tzinfo=UTC))


class TestLinkRequestSchemas:
    def test_link_series_accepts_numeric_link_ids(self, interval: ApiInterval):
        request = GetEdgeLinkSeries(
            enterprise_id=1,
            edge_id=2,
            interval=interval,
            links=[17],
        )

        assert request.links == [17]

    def test_link_series_rejects_internal_link_uuid(self, interval: ApiInterval):
        with pytest.raises(ValidationError):
            _ = GetEdgeLinkSeries.model_validate(
                {
                    "enterprise_id": 1,
                    "edge_id": 2,
                    "interval": interval,
                    "links": ["00000003-9e3c-4cb5-86af-0367b4165559"],
                }
            )

    def test_qoe_request_accepts_numeric_link_ids(self, interval: ApiInterval):
        request = GetLinkQualityEvents.model_validate(
            {
                "enterpriseId": 1,
                "edgeId": 2,
                "interval": interval,
                "links": [17],
            }
        )

        payload = request.model_dump(by_alias=True, exclude_unset=True)
        assert payload["links"] == [17]

    def test_qoe_request_rejects_internal_link_uuid(self, interval: ApiInterval):
        with pytest.raises(ValidationError):
            _ = GetLinkQualityEvents.model_validate(
                {
                    "enterpriseId": 1,
                    "edgeId": 2,
                    "interval": interval,
                    "links": ["00000003-9e3c-4cb5-86af-0367b4165559"],
                }
            )


class TestLinkAnalyticsRequests:
    class FakeClient:
        def __init__(self) -> None:
            self.link_series_requests: list[GetEdgeLinkSeries] = []
            self.qoe_requests: list[GetLinkQualityEvents] = []

        async def get_edge_link_series(
            self, request: GetEdgeLinkSeries
        ) -> Sequence[EdgeLinkSeriesItem]:
            self.link_series_requests.append(request)
            return []

        async def get_link_quality_events(
            self, request: GetLinkQualityEvents
        ) -> GetLinkQualityEventsResult:
            self.qoe_requests.append(request)
            return GetLinkQualityEventsResult()

    def _analytics(self, client: FakeClient) -> Analytics:
        protocol = cast(VcoClientProtocol, cast(object, client))
        return Analytics(protocol, enterprise_id=1)

    @pytest.mark.anyio
    async def test_link_series_passes_numeric_link_ids(self, interval: ApiInterval):
        client = self.FakeClient()

        _ = await self._analytics(client).get_edge_link_series(
            edge_id=2,
            interval=interval,
            links=[17],
        )

        assert client.link_series_requests[0].links == [17]

    @pytest.mark.anyio
    async def test_qoe_request_passes_numeric_link_ids(self, interval: ApiInterval):
        client = self.FakeClient()

        _ = await self._analytics(client).get_edge_qoe_series(
            edge_id=2,
            interval=interval,
            links=[17],
        )

        payload = client.qoe_requests[0].model_dump(by_alias=True, exclude_unset=True)
        assert payload["links"] == [17]
