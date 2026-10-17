from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from pyvelo.analytics.analytics import Analytics
from pyvelo.client.schemas.link_quality import GetLinkQualityEvents
from pyvelo.client.schemas.metrics import GetEdgeLinkSeries
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
            GetEdgeLinkSeries.model_validate(
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
            GetLinkQualityEvents.model_validate(
                {
                    "enterpriseId": 1,
                    "edgeId": 2,
                    "interval": interval,
                    "links": ["00000003-9e3c-4cb5-86af-0367b4165559"],
                }
            )


class TestLinkAnalyticsRequests:
    @pytest.mark.anyio
    async def test_link_series_passes_numeric_link_ids(self, interval: ApiInterval):
        client: Any = MagicMock()
        captured: list[Any] = []

        async def _get_edge_link_series(request):
            captured.append(request)
            return []

        client.get_edge_link_series = _get_edge_link_series

        await Analytics(client, enterprise_id=1).get_edge_link_series(
            edge_id=2,
            interval=interval,
            links=[17],
        )

        assert captured[0].links == [17]

    @pytest.mark.anyio
    async def test_qoe_request_passes_numeric_link_ids(self, interval: ApiInterval):
        client: Any = MagicMock()
        captured: list[Any] = []

        async def _get_link_quality_events(request):
            captured.append(request)
            return MagicMock(link_entries={})

        client.get_link_quality_events = _get_link_quality_events

        await Analytics(client, enterprise_id=1).get_edge_qoe_series(
            edge_id=2,
            interval=interval,
            links=[17],
        )

        payload = captured[0].model_dump(by_alias=True, exclude_unset=True)
        assert payload["links"] == [17]
