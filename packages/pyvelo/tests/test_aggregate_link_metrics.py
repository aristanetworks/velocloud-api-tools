"""Real protocol regressions for bulk-link scope, identity and coverage."""

from typing import cast

import httpx
import pytest
from pyvelo.analytics import Analytics
from pyvelo.client.enums.metrics import AggregateEdgeLinkMetric
from pyvelo.client.exceptions import VcoOperationError
from pyvelo.core import ApiInterval, PaginatedVcoModel, VcoModel

from ._type_support import (
    JsonObject,
    MockVcoClientFactory,
    parse_json_object,
)

INTERVAL = ApiInterval.model_validate({"start": 1000, "end": 2000})


@pytest.mark.parametrize("enterprise_id", [None, 0, 42])
async def test_scope_metrics_and_nested_identity_survive_mapping(
    mock_vco_client: MockVcoClientFactory, enterprise_id: int | None
):
    calls: list[JsonObject] = []
    item: JsonObject = {
        "linkId": 11,
        "name": "other",
        "bytesRx": 0,
        "p1BytesRx": 7,
        "link": {
            "enterpriseId": enterprise_id,
            "enterpriseName": "Tenant",
            "edgeId": 2,
            "edgeName": "Edge",
            "internalId": "wan-1",
            "linkId": 11,
            "edgeState": "CONNECTED",
            "isp": None,
        },
    }

    def handle(request: httpx.Request) -> httpx.Response:
        body = parse_json_object(request.content)
        calls.append(body)
        assert body["method"] == "monitoring/getAggregateEdgeLinkMetrics"
        params = cast(JsonObject, body["params"])
        if enterprise_id is None:
            assert "enterpriseId" not in params
        else:
            assert params["enterpriseId"] == enterprise_id
        assert params["metrics"] == ["bytesRx", "p1BytesRx"]
        return httpx.Response(200, json={"result": [item]})

    client = await mock_vco_client(handle)
    frame = await Analytics(
        client, enterprise_id=enterprise_id
    ).get_aggregate_link_metrics(
        INTERVAL,
        metrics=[AggregateEdgeLinkMetric.BYTES_RX, AggregateEdgeLinkMetric.P1_BYTES_RX],
    )
    assert len(calls) == 1
    row = frame.rows[0]
    assert row["name"] == "other"
    assert row["enterprise_id"] == enterprise_id
    assert row["edge_id"] == 2 and row["internal_id"] == "wan-1"
    assert row["p1_bytes_rx"] == 7 and row["bytes_rx"] == 0
    assert row["link"]["edgeState"] == "CONNECTED"
    details = parse_json_object(cast(str, row["details"]))
    assert details == item
    link = cast(JsonObject, details["link"])
    assert "edgeSerialNumber" not in link
    assert link["isp"] is None


async def test_missing_identity_and_duplicate_link_ids_are_not_invented_or_dropped(
    mock_vco_client: MockVcoClientFactory,
):
    items = [
        {"linkId": 11, "name": "WAN", "link": {"enterpriseId": 42, "edgeId": 2}},
        {"linkId": 11, "name": "WAN", "link": {"enterpriseId": 84, "edgeId": 3}},
        {"name": "WAN", "link": {"linkId": 12}},
        {"linkId": 13},
    ]
    client = await mock_vco_client(
        lambda _: httpx.Response(200, json={"result": items})
    )
    rows = (await Analytics(client).get_aggregate_link_metrics(INTERVAL)).rows
    assert len(rows) == 4
    assert [(r["enterprise_id"], r["edge_id"]) for r in rows[:2]] == [(42, 2), (84, 3)]
    assert rows[2]["link_id"] == 12 and rows[2]["enterprise_id"] is None
    assert "linkId" not in parse_json_object(cast(str, rows[2]["details"]))
    assert rows[3]["edge_id"] is None and rows[3]["link"] is None
    assert "link" not in parse_json_object(cast(str, rows[3]["details"]))


@pytest.mark.parametrize(
    "item,error",
    [
        ({"linkId": 1, "link": {"enterpriseId": 84}}, "another enterprise"),
        ({"linkId": 1, "link": {"linkId": 2}}, "identifiers conflict"),
    ],
)
async def test_conflicting_scope_and_identity_fail(
    mock_vco_client: MockVcoClientFactory, item: JsonObject, error: str
):
    client = await mock_vco_client(
        lambda _: httpx.Response(200, json={"result": [item]})
    )
    with pytest.raises(VcoOperationError, match=error):
        _ = await Analytics(client, enterprise_id=42).get_aggregate_link_metrics(
            INTERVAL
        )


@pytest.mark.parametrize("more", [False, True])
async def test_terminal_envelope_supported_and_partial_envelope_rejected(
    mock_vco_client: MockVcoClientFactory, more: bool
):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": [{"linkId": 1}],
                    "metaData": {
                        "more": more,
                        "nextPageLink": "cursor" if more else None,
                    },
                }
            },
        )

    client = await mock_vco_client(handle)
    analytics = Analytics(client, enterprise_id=42)
    if more:
        with pytest.raises(VcoOperationError, match="pagination is unsupported"):
            _ = await analytics.get_aggregate_link_metrics(INTERVAL)
    else:
        assert len(await analytics.get_aggregate_link_metrics(INTERVAL)) == 1
    assert len(calls) == 1


async def test_empty_results_keep_explicit_identity_schema(
    mock_vco_client: MockVcoClientFactory,
):
    client = await mock_vco_client(lambda _: httpx.Response(200, json={"result": []}))
    frame = await Analytics(client, enterprise_id=42).get_aggregate_link_metrics(
        INTERVAL
    )
    assert len(frame) == 0
    assert {"enterprise_id", "edge_id", "internal_id", "link", "details"} <= set(
        frame.columns
    )


async def test_paginated_list_rejects_missing_continuation_cursor(
    mock_vco_client: MockVcoClientFactory,
):
    calls: list[httpx.Request] = []

    class Request(PaginatedVcoModel):
        pass

    class Item(VcoModel):
        value: int

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"result": {"data": [{"value": 1}], "metaData": {"more": True}}},
        )

    client = await mock_vco_client(handle)
    with pytest.raises(VcoOperationError, match="omitted its continuation cursor"):
        _ = await client.portal_paginated_list("test/list", Request(), Item)
    assert len(calls) == 1
