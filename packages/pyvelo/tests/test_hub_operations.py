"""The Edge disable calls accept a ``null`` answer; the cluster ones do not; the services read parses a source-shaped answer."""

from typing import Any

import httpx
import pytest
from pydantic import MISSING
from pyvelo.client.enums.enterprise import (
    EnterpriseServicesParamsWith,
    EnterpriseServiceType,
)
from pyvelo.client.exceptions import VcoEmptyResultError
from pyvelo.client.schemas.edge import (
    DisableEdgeBackHaul,
    DisableEdgeHub,
    DisableEdgeToEdgeBridge,
)
from pyvelo.client.schemas.enterprise import (
    DisableClusterBackHaul,
    DisableClusterHub,
    DisableClusterToEdgeBridge,
    EdgeHubService,
    GetEnterpriseServices,
)

from ._type_support import MockVcoClientFactory

EDGE_ARGS: dict[str, Any] = {
    "id_": 31,
    "configuration_id": 100,
    "module_id": 10,
    "segment_object_id": 501,
}
CLUSTER_ARGS: dict[str, Any] = {**EDGE_ARGS, "id_": 41}


def answer_null(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})


async def test_an_edge_hub_disable_answered_null_returns_none(
    mock_vco_client: MockVcoClientFactory,
):
    client = await mock_vco_client(answer_null)
    assert await client.disable_edge_hub(DisableEdgeHub(**EDGE_ARGS)) is None


async def test_an_edge_bridge_disable_answered_null_returns_none(
    mock_vco_client: MockVcoClientFactory,
):
    client = await mock_vco_client(answer_null)
    assert (
        await client.disable_edge_to_edge_bridge(DisableEdgeToEdgeBridge(**EDGE_ARGS))
        is None
    )


async def test_an_edge_backhaul_disable_answered_null_returns_none(
    mock_vco_client: MockVcoClientFactory,
):
    client = await mock_vco_client(answer_null)
    assert await client.disable_edge_backhaul(DisableEdgeBackHaul(**EDGE_ARGS)) is None


async def test_an_edge_disable_answered_with_a_service_returns_it(
    mock_vco_client: MockVcoClientFactory,
):
    def answer(_request: httpx.Request) -> httpx.Response:
        service = {
            "id": 7,
            "logicalId": "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
            "name": "Hub-A",
            "created": "2024-01-01T00:00:00.000Z",
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": service})

    client = await mock_vco_client(answer)
    got = await client.disable_edge_backhaul(DisableEdgeBackHaul(**EDGE_ARGS))
    assert got is not None
    assert got.id_ == 7


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.disable_cluster_hub(DisableClusterHub(**CLUSTER_ARGS)),
        lambda c: c.disable_cluster_to_edge_bridge(
            DisableClusterToEdgeBridge(**CLUSTER_ARGS)
        ),
        lambda c: c.disable_cluster_backhaul(DisableClusterBackHaul(**CLUSTER_ARGS)),
    ],
)
async def test_a_cluster_disable_answered_null_still_raises(
    mock_vco_client: MockVcoClientFactory, call: Any
):
    client = await mock_vco_client(answer_null)
    with pytest.raises(VcoEmptyResultError):
        await call(client)


async def test_services_with_a_source_shaped_qos_usage_parse(
    mock_vco_client: MockVcoClientFactory,
):
    service = {
        "id": 7,
        "logicalId": "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
        "name": "Hub-A",
        "type": "edgeHub",
        "created": "2024-01-01T00:00:00.000Z",
        "edgeId": 31,
        "data": {
            "logicalId": "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
            "roles": {
                "edgeHub": True,
                "edgeToEdgeBridge": False,
                "backHaulEdge": True,
            },
        },
        "configuration": [
            {"id": 100, "name": "Profile", "moduleId": 10, "segmentObjectId": 501}
        ],
        "edgeQOSUsage": [
            {
                "enterpriseObjectId": 7,
                "segmentObjectId": 501,
                "ref": "deviceSettings:backHaulEdge",
                "edgeId": 34,
                "name": "Branch-1",
                "logicalId": "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e5",
                "profileId": 200,
            }
        ],
    }

    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": [service]}
        )

    client = await mock_vco_client(answer)
    got = await client.get_enterprise_services(
        GetEnterpriseServices(
            type_=EnterpriseServiceType.EDGE_HUB,
            with_=[
                EnterpriseServicesParamsWith.CONFIGURATION,
                EnterpriseServicesParamsWith.EDGE_QOS_USAGE,
            ],
        )
    )
    (hub,) = got
    assert isinstance(hub, EdgeHubService)
    usage = hub.edge_qos_usage
    assert usage is not MISSING
    assert [u.ref for u in usage] == ["deviceSettings:backHaulEdge"]
