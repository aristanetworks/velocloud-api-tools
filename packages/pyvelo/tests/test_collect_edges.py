from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic_core import MISSING
from pyvelo.analytics.analytics import Analytics
from pyvelo.client.enums.edge import (
    ActivationState,
    EdgeState,
)
from pyvelo.client.schemas.enterprise import GetEnterpriseEdges, GetEnterpriseEdgesItem


def _make_edge(
    id_: int = 1,
    name: str = "edge-1",
    edge_state: str = "CONNECTED",
    activation_state: str = "ACTIVATED",
    service_state: str = "IN_SERVICE",
    ha_state: str = "UNCONFIGURED",
    model_number: str = "edge840",
    software_version: str = "5.4.0",
    serial_number: str = "SN001",
) -> GetEnterpriseEdgesItem:
    return GetEnterpriseEdgesItem.model_validate(
        {
            "id": id_,
            "name": name,
            "edgeState": edge_state,
            "activationState": activation_state,
            "serviceState": service_state,
            "haState": ha_state,
            "modelNumber": model_number,
            "softwareVersion": software_version,
            "serialNumber": serial_number,
            "enterpriseId": 1,
            "siteId": 10,
        }
    )


def _make_client(pages: list[list[GetEnterpriseEdgesItem]]) -> Any:
    client = MagicMock()
    captured_requests: list[GetEnterpriseEdges] = []

    async def _portal_paginated(method, request, item_type):
        captured_requests.append(request)
        for page in pages:
            yield page

    client.portal_paginated = _portal_paginated
    client._captured_requests = captured_requests
    return client


class TestCollectEdgesFilterPushdown:
    @pytest.mark.anyio
    async def test_no_filters(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges()
        assert len(result) == 1
        req = client._captured_requests[0]
        assert req.filters is MISSING

    @pytest.mark.anyio
    async def test_edge_state_filter(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_edges(
            edge_state=[EdgeState.CONNECTED, EdgeState.DEGRADED]
        )
        req = client._captured_requests[0]
        assert req.filters is not MISSING
        filters = req.filters.and_
        f = filters[0]
        assert f.field == "edgeState"
        assert f.operator == "in"
        assert "CONNECTED" in f.value
        assert "DEGRADED" in f.value

    @pytest.mark.anyio
    async def test_activation_state_filter(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_edges(activation_state=[ActivationState.ACTIVATED])
        req = client._captured_requests[0]
        filters = req.filters.and_
        f = filters[0]
        assert f.field == "activationState"
        assert f.operator == "in"

    @pytest.mark.anyio
    async def test_model_number_filter(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_edges(model_number=["edge840", "edge3800"])
        req = client._captured_requests[0]
        filters = req.filters.and_
        assert filters[0].field == "modelNumber"
        assert "edge840" in filters[0].value

    @pytest.mark.anyio
    async def test_name_filter(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_edges(name="my-edge")
        req = client._captured_requests[0]
        filters = req.filters.and_
        assert filters[0].field == "name"
        assert filters[0].operator == "is"
        assert filters[0].value == "my-edge"

    @pytest.mark.anyio
    async def test_edge_ids_filter(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_edges(edge_ids=[100, 200])
        req = client._captured_requests[0]
        assert req.edge_ids == [100, 200]

    @pytest.mark.anyio
    async def test_combined_filters(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_edges(
            edge_state=[EdgeState.CONNECTED],
            activation_state=[ActivationState.ACTIVATED],
            edge_ids=[100],
        )
        req = client._captured_requests[0]
        assert req.edge_ids == [100]
        filters = req.filters.and_
        assert len(filters) == 2


class TestCollectEdgesEarlyTermination:
    @pytest.mark.anyio
    async def test_stops_at_max_items(self):
        page1 = [_make_edge(id_=i, name=f"e{i}") for i in range(5)]
        page2 = [_make_edge(id_=i, name=f"e{i}") for i in range(5, 10)]
        page3 = [_make_edge(id_=i, name=f"e{i}") for i in range(10, 15)]

        pages_yielded = []
        client = MagicMock()
        client._captured_requests = []

        async def _tracking_paginated(method, request, item_type):
            for i, page in enumerate([page1, page2, page3]):
                pages_yielded.append(i)
                yield page

        client.portal_paginated = _tracking_paginated

        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges(max_items=7)

        assert len(result) == 7
        assert len(pages_yielded) == 2

    @pytest.mark.anyio
    async def test_exact_max_items(self):
        page = [_make_edge(id_=i) for i in range(5)]
        client = _make_client([page])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges(max_items=5)
        assert len(result) == 5

    @pytest.mark.anyio
    async def test_fewer_than_max(self):
        page = [_make_edge(id_=i) for i in range(3)]
        client = _make_client([page])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges(max_items=100)
        assert len(result) == 3


class TestCollectEdgesOutput:
    @pytest.mark.anyio
    async def test_returns_metric_frame_with_expected_columns(self):
        client = _make_client([[_make_edge()]])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges()
        expected = [
            "id",
            "logical_id",
            "name",
            "edge_state",
            "activation_state",
            "service_state",
            "ha_state",
            "model_number",
            "device_family",
            "software_version",
            "serial_number",
            "description",
            "last_contact",
            "created",
            "site_id",
        ]
        assert result.columns == expected

    @pytest.mark.anyio
    async def test_enum_values_are_strings(self):
        client = _make_client(
            [
                [
                    _make_edge(
                        edge_state="CONNECTED",
                        activation_state="ACTIVATED",
                        service_state="IN_SERVICE",
                        ha_state="UNCONFIGURED",
                    )
                ]
            ]
        )
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges()
        row = result.rows[0]
        assert row["edge_state"] == "CONNECTED"
        assert row["activation_state"] == "ACTIVATED"
        assert row["service_state"] == "IN_SERVICE"
        assert row["ha_state"] == "UNCONFIGURED"
        assert isinstance(row["edge_state"], str)

    @pytest.mark.anyio
    async def test_id_column_mapped(self):
        client = _make_client([[_make_edge(id_=42)]])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges()
        assert result.rows[0]["id"] == 42

    @pytest.mark.anyio
    async def test_chainable_with_frame_ops(self):
        edges = [
            _make_edge(id_=1, edge_state="CONNECTED"),
            _make_edge(id_=2, edge_state="OFFLINE"),
            _make_edge(id_=3, edge_state="CONNECTED"),
        ]
        client = _make_client([edges])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_edges()
        grouped = result.group_by("edge_state").agg(count="n")
        assert len(grouped) == 2
        top = result.top_n(1, by="name", descending=True)
        assert len(top) == 1
