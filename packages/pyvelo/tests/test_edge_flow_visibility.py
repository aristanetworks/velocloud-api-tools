"""Request, row and pagination contract for metrics/getEdgeFlowVisibilityMetrics."""

import datetime
from typing import cast

import httpx
import pytest
from pydantic import MISSING, ValidationError
from pyvelo.client.enums.metrics import FilterFieldOperator, FlowVisibilityFilterField
from pyvelo.client.schemas.metrics import (
    FlowVisibilityFilter,
    FlowVisibilityFilters,
    GetEdgeFlowVisibilityMetrics,
)
from pyvelo.core import ApiInterval

from ._type_support import JsonObject, MockVcoClientFactory, parse_json_object

INTERVAL = ApiInterval.model_validate(
    {"start": 1_717_200_000_000, "end": 1_717_203_600_000}
)


def flow_row(index: int) -> JsonObject:
    return {
        "sourceIp": "10.0.0.1",
        "destIp": f"36.23.94.{index}",
        "transport": 6,
        "destPort": 443,
        "linkId": 2,
        "linkName": "GE3",
        "startTime": "2026-10-09T00:00:00.000Z",
        "endTime": f"2026-10-09T00:0{index}:00.000Z",
        "application": 292,
        "category": 19,
        "destFQDN": f"host{index}.example.com",
        "destDomain": "example.com",
        "segmentId": 0,
        "route": "Direct",
        "bytesRx": 100,
        "bytesTx": 10,
        "flowCount": 4,
        "hostName": None,
        "isp": None,
        "nextHop": None,
        "businessRuleName": None,
    }


SUMMARY_ROW: JsonObject = {
    "name": "other",
    "count": 3,
    "metrics": {"bytesRx": 300, "bytesTx": 30},
}


def request_with(
    *,
    limit: int | MISSING = MISSING,
    filters: FlowVisibilityFilters | MISSING = MISSING,
) -> GetEdgeFlowVisibilityMetrics:
    return GetEdgeFlowVisibilityMetrics(
        edge_id=7, interval=INTERVAL, limit=limit, filters=filters
    )


class TestFilters:
    def test_serializes_api_filter_shape(self):
        request = request_with(
            filters=FlowVisibilityFilters(
                conditions=[
                    FlowVisibilityFilter(
                        field=FlowVisibilityFilterField.DEST_FQDN,
                        operator=FilterFieldOperator.IS,
                        value="host1.example.com",
                    )
                ]
            )
        )

        dumped = request.model_dump(by_alias=True)

        assert dumped["filters"] == {
            "and": [
                {"field": "destFqdn", "operator": "is", "value": "host1.example.com"}
            ]
        }

    def test_accepts_op_as_input_alias(self):
        condition = FlowVisibilityFilter.model_validate(
            {"field": "transport", "op": "is", "value": 6}
        )
        assert condition.operator == FilterFieldOperator.IS

    def test_accepts_substring_operator_on_fqdn(self):
        condition = FlowVisibilityFilter.model_validate(
            {"field": "destFqdn", "operator": "contains", "value": "example.com"}
        )
        assert condition.model_dump(by_alias=True)["operator"] == "contains"

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            _ = FlowVisibilityFilter.model_validate(
                {"field": "notAField", "operator": "is", "value": "x"}
            )


class TestOperation:
    async def test_follows_pages_and_drops_summary_rows(
        self, mock_vco_client: MockVcoClientFactory
    ):
        sent: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = parse_json_object(request.content)
            assert body["method"] == "metrics/getEdgeFlowVisibilityMetrics"
            params = cast(JsonObject, body["params"])
            cursor = params.get("nextPageLink")
            assert cursor is None or isinstance(cursor, str)
            sent.append(cursor)
            first = cursor is None
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": (
                            [flow_row(1), flow_row(2), SUMMARY_ROW]
                            if first
                            else [flow_row(3), SUMMARY_ROW]
                        ),
                        "metaData": {
                            "limit": 2,
                            "more": first,
                            **({"nextPageLink": "flow-cursor"} if first else {}),
                        },
                    }
                },
            )

        client = await mock_vco_client(handler)
        rows = await client.get_edge_flow_visibility_metrics(request_with(limit=2))

        assert sent == [None, "flow-cursor"]
        assert [row.dest_fqdn for row in rows] == [
            "host1.example.com",
            "host2.example.com",
            "host3.example.com",
        ]
        first = rows[0]
        assert first.category == 19
        assert first.dest_port == 443
        assert first.transport == 6
        assert first.flow_count == 4
        assert first.isp is None
        assert first.end_time == datetime.datetime(
            2026, 10, 9, 0, 1, tzinfo=datetime.UTC
        )

    async def test_single_page_records_continuation(
        self, mock_vco_client: MockVcoClientFactory
    ):
        sent: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = parse_json_object(request.content)
            params = cast(JsonObject, body["params"])
            cursor = params.get("nextPageLink")
            assert cursor is None or isinstance(cursor, str)
            sent.append(cursor)
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [flow_row(1), SUMMARY_ROW],
                        "metaData": {"more": True, "nextPageLink": "flow-cursor"},
                    }
                },
            )

        client = await mock_vco_client(handler)
        request = request_with(limit=1)
        setattr(request, "_max_pages", 1)

        rows = await client.get_edge_flow_visibility_metrics(request)

        assert len(rows) == 1
        assert sent == [None]
        assert cast(bool, getattr(request, "_page_has_more")) is True
        assert cast(str | None, getattr(request, "_page_next_link")) == "flow-cursor"

    async def test_no_filter_fallback_returns_no_flows(
        self, mock_vco_client: MockVcoClientFactory
    ):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [{"count": 0, "name": "other"}],
                        "metaData": {"limit": 200, "more": False},
                    }
                },
            )

        client = await mock_vco_client(handler)
        assert await client.get_edge_flow_visibility_metrics(request_with()) == []
