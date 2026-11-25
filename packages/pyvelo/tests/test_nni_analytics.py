from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast, final

import pytest
from pydantic import MISSING
from pyvelo.analytics.analytics import Analytics
from pyvelo.core import ApiInterval

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyvelo.client.protocol import VcoClientProtocol
    from pyvelo.client.schemas.monitoring import (
        GetEnterpriseNniMetrics,
        GetEnterpriseNniSeries,
    )


@pytest.fixture
def interval() -> ApiInterval:
    return ApiInterval(start=datetime(2026, 1, 1, tzinfo=UTC))


@final
class NniAnalyticsTestClient:
    def __init__(self) -> None:
        self.captured_requests: list[
            GetEnterpriseNniMetrics | GetEnterpriseNniSeries
        ] = []

    @property
    def protocol(self) -> VcoClientProtocol:
        return cast("VcoClientProtocol", cast("object", self))

    async def get_enterprise_nni_metrics(
        self, request: GetEnterpriseNniMetrics
    ) -> Sequence[object]:
        self.captured_requests.append(request)
        return []

    async def get_enterprise_nni_series(
        self, request: GetEnterpriseNniSeries
    ) -> Sequence[object]:
        self.captured_requests.append(request)
        return []


class TestNniEnterpriseContext:
    @pytest.mark.anyio
    async def test_metrics_passes_enterprise_id(self, interval: ApiInterval) -> None:
        client = NniAnalyticsTestClient()

        _ = await Analytics(client.protocol, enterprise_id=42).get_nni_metrics(interval)

        assert client.captured_requests[0].enterprise_id == 42

    @pytest.mark.anyio
    async def test_series_passes_enterprise_id(self, interval: ApiInterval) -> None:
        client = NniAnalyticsTestClient()

        _ = await Analytics(client.protocol, enterprise_id=42).get_nni_series(interval)

        assert client.captured_requests[0].enterprise_id == 42

    @pytest.mark.anyio
    async def test_omits_enterprise_id_when_unspecified(
        self, interval: ApiInterval
    ) -> None:
        client = NniAnalyticsTestClient()

        _ = await Analytics(client.protocol).get_nni_metrics(interval)

        assert client.captured_requests[0].enterprise_id is MISSING
