from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic_core import MISSING
from pyvelo.analytics.analytics import Analytics
from pyvelo.client.schemas.configuration import EnterpriseConfigurationProfile
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations


def _make_profile(
    id_: int = 1,
    name: str = "profile-1",
    description: str = "Test profile",
    edge_count: int = 5,
) -> EnterpriseConfigurationProfile:
    return EnterpriseConfigurationProfile(
        id_=id_,
        name=name,
        description=description,
        edge_count=edge_count,
    )


def _make_client(profiles: list[EnterpriseConfigurationProfile]) -> Any:
    client = MagicMock()
    captured_requests: list[GetEnterpriseConfigurations] = []

    async def _portal_list(method, request, item_type, **kwargs):
        captured_requests.append(request)
        return profiles

    client.portal_list = _portal_list
    client._captured_requests = captured_requests
    return client


class TestCollectConfigurationsRequest:
    @pytest.mark.anyio
    async def test_includes_edge_count_by_default(self):
        client = _make_client([_make_profile()])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_configurations()
        req = client._captured_requests[0]
        assert req.with_ is not MISSING
        assert "edgeCount" in req.with_

    @pytest.mark.anyio
    async def test_without_edge_count(self):
        client = _make_client([_make_profile()])
        analytics = Analytics(client, enterprise_id=1)
        await analytics.collect_configurations(with_edge_count=False)
        req = client._captured_requests[0]
        assert req.with_ is MISSING

    @pytest.mark.anyio
    async def test_enterprise_id_passed(self):
        client = _make_client([_make_profile()])
        analytics = Analytics(client, enterprise_id=42)
        await analytics.collect_configurations()
        req = client._captured_requests[0]
        assert req.enterprise_id == 42


class TestCollectConfigurationsOutput:
    @pytest.mark.anyio
    async def test_returns_metric_frame_with_expected_columns(self):
        client = _make_client([_make_profile()])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_configurations()
        expected = [
            "id",
            "logical_id",
            "name",
            "configuration_type",
            "description",
            "edge_count",
            "version",
            "schema_version",
            "created",
            "modified",
            "effective",
            "is_staging",
            "bastion_state",
        ]
        assert result.columns == expected

    @pytest.mark.anyio
    async def test_enum_values_are_strings(self):
        client = _make_client([_make_profile()])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_configurations()
        row = result.rows[0]
        assert row["configuration_type"] == "SEGMENT_BASED"
        assert isinstance(row["configuration_type"], str)

    @pytest.mark.anyio
    async def test_id_column_mapped(self):
        client = _make_client([_make_profile(id_=99)])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_configurations()
        assert result.rows[0]["id"] == 99

    @pytest.mark.anyio
    async def test_max_items_limits_results(self):
        profiles = [_make_profile(id_=i, name=f"p{i}") for i in range(5)]
        client = _make_client(profiles)
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_configurations(max_items=2)
        assert len(result) == 2

    @pytest.mark.anyio
    async def test_empty_result(self):
        client = _make_client([])
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_configurations()
        assert len(result) == 0
        expected = [
            "id",
            "logical_id",
            "name",
            "configuration_type",
            "description",
            "edge_count",
            "version",
            "schema_version",
            "created",
            "modified",
            "effective",
            "is_staging",
            "bastion_state",
        ]
        assert result.columns == expected

    @pytest.mark.anyio
    async def test_chainable_with_frame_ops(self):
        profiles = [
            _make_profile(id_=1, name="Default", edge_count=10),
            _make_profile(id_=2, name="Branch", edge_count=5),
            _make_profile(id_=3, name="Default", edge_count=3),
        ]
        client = _make_client(profiles)
        analytics = Analytics(client, enterprise_id=1)
        result = await analytics.collect_configurations()
        grouped = result.group_by("name").agg(count="n")
        assert len(grouped) == 2
        filtered = result.filter("edge_count", ">", 4)
        assert len(filtered) == 2
