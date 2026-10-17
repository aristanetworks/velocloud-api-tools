import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from pyvelo.client import VcoClient
from pyvelo.client.enums.edge import GetEdgesWith, GetEnterpriseEdgeListWith
from pyvelo.client.schemas.enterprise import (
    GetEnterpriseEdgeList,
    GetEnterpriseEdgeListItem,
    GetEnterpriseEdges,
)


class TestEnterpriseEdgeWithEnums:
    def test_get_enterprise_edges_rejects_edge_list_only_with_values(self):
        with pytest.raises(ValidationError):
            GetEnterpriseEdges.model_validate({"with": ["analyticsMode"]})

    def test_get_enterprise_edges_accepts_public_with_values(self):
        req = GetEnterpriseEdges.model_validate({"with": ["cloudServiceSiteStatus"]})

        assert req.with_ == [GetEdgesWith.CLOUD_SERVICE_SITE_STATUS]

    def test_get_enterprise_edge_list_accepts_ui_with_values(self):
        req = GetEnterpriseEdgeList.model_validate(
            {
                "with": [
                    "certificateSummary",
                    "isAssignedGatewayQuiesced",
                    "secureDeviceSecrets",
                ]
            }
        )

        assert req.with_ == [
            GetEnterpriseEdgeListWith.CERTIFICATE_SUMMARY,
            GetEnterpriseEdgeListWith.IS_ASSIGNED_GATEWAY_QUIESCED,
            GetEnterpriseEdgeListWith.SECURE_DEVICE_SECRETS,
        ]

    def test_get_enterprise_edge_list_rejects_public_only_with_values(self):
        with pytest.raises(ValidationError):
            GetEnterpriseEdgeList.model_validate({"with": ["cloudServiceSiteStatus"]})

    def test_get_enterprise_edge_list_requires_positive_enterprise_id(self):
        schema = GetEnterpriseEdgeList.model_json_schema(by_alias=True)

        assert schema["properties"]["enterpriseId"]["minimum"] == 1
        with pytest.raises(ValidationError):
            GetEnterpriseEdgeList(enterprise_id=0)

    def test_edge_list_request_serializes_documented_helper_fields(self):
        req = GetEnterpriseEdgeList(
            enterprise_id=1,
            prev_page_link="previous",
            sort_by=[{"attribute": "name", "type": "ASC"}],
            count=True,
            quick_search={"term": "branch"},
        )

        dumped = req.model_dump(by_alias=True)

        assert dumped["enterpriseId"] == 1
        assert dumped["prevPageLink"] == "previous"
        assert dumped["sortBy"] == [{"attribute": "name", "type": "ASC"}]
        assert dumped["_count"] is True
        assert dumped["quickSearch"] == {"term": "branch"}


class TestGetEnterpriseEdgeListOperation:
    async def test_dispatches_to_edge_list_endpoint(self):
        client = VcoClient("test.velocloud.net", "test-token")
        client.portal_paginated_list = AsyncMock(return_value=[])  # type: ignore[method-assign]

        request = GetEnterpriseEdgeList(
            enterprise_id=1,
            with_=[GetEnterpriseEdgeListWith.SECURE_DEVICE_SECRETS],
        )

        result = await client.get_enterprise_edge_list(request)

        assert result == []
        client.portal_paginated_list.assert_awaited_once_with(
            "enterprise/getEnterpriseEdgeList",
            request,
            GetEnterpriseEdgeListItem,
        )

    async def test_single_page_request_records_continuation(self, mock_vco_client):
        sent = []

        def handler(http_request):
            params = json.loads(http_request.content)["params"]
            sent.append(params.get("nextPageLink"))
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [{"id": 1, "name": "Edge 1"}],
                        "metaData": {"more": True, "nextPageLink": "edge-cursor"},
                    }
                },
            )

        client = await mock_vco_client(handler)
        request = GetEnterpriseEdgeList(enterprise_id=1, limit=1)
        request._max_pages = 1

        result = await client.get_enterprise_edge_list(request)

        assert [edge.name for edge in result] == ["Edge 1"]
        assert sent == [None]
        assert request._page_has_more is True
        assert request._page_next_link == "edge-cursor"
