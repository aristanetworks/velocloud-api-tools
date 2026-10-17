from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic_core import MISSING
from pyvelo.analytics.analytics import Analytics
from pyvelo.core import ApiInterval


@pytest.fixture
def interval() -> ApiInterval:
    return ApiInterval(start=datetime(2026, 1, 1, tzinfo=UTC))


def _make_client() -> Any:
    client = MagicMock()
    captured_requests: list[Any] = []

    async def _get_enterprise_nni_metrics(request):
        captured_requests.append(request)
        return []

    async def _get_enterprise_nni_series(request):
        captured_requests.append(request)
        return []

    client.get_enterprise_nni_metrics = _get_enterprise_nni_metrics
    client.get_enterprise_nni_series = _get_enterprise_nni_series
    client._captured_requests = captured_requests
    return client


class TestNniEnterpriseContext:
    @pytest.mark.anyio
    async def test_metrics_passes_enterprise_id(self, interval: ApiInterval):
        client = _make_client()

        await Analytics(client, enterprise_id=42).get_nni_metrics(interval)

        assert client._captured_requests[0].enterprise_id == 42

    @pytest.mark.anyio
    async def test_series_passes_enterprise_id(self, interval: ApiInterval):
        client = _make_client()

        await Analytics(client, enterprise_id=42).get_nni_series(interval)

        assert client._captured_requests[0].enterprise_id == 42

    @pytest.mark.anyio
    async def test_omits_enterprise_id_when_unspecified(self, interval: ApiInterval):
        client = _make_client()

        await Analytics(client).get_nni_metrics(interval)

        assert client._captured_requests[0].enterprise_id is MISSING
