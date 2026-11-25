"""Reusable fakes and factories for tests."""

import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from unittest.mock import AsyncMock

import httpx
from pydantic import MISSING

from pyvelo.client import VcoClient
from pyvelo.client.enums.enterprise import EnterpriseServiceType, NetworkSegmentType
from pyvelo.client.schemas.enterprise import (
    AnyEnterpriseService,
    EdgeHubClusterMemberService,
    EdgeHubClusterService,
    EnterpriseServiceSummary,
    GetEnterpriseEdgeListItem,
    GetEnterpriseServices,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.core.identity import HubIdentity, NetworkServiceIdentity


def make_jsonrpc_response(
    result: Any = None,
    error: dict[str, Any] | None = None,
    id_: int = 1,
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 response envelope."""
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def make_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    """Wrap a handler into a mock transport."""
    return httpx.MockTransport(handler)


def make_module_json(  # noqa: PLR0913 (existing: split it when it next changes)
    name: str,
    data: object,
    *,
    module_id: int = 1,
    configuration_id: int = 100,
    version: str | None = "v1",
    refs: object = MISSING,
) -> dict[str, Any]:
    """Build a configuration module row in wire form.

    ``refs`` is included only when given.
    """
    row: dict[str, Any] = {
        "id": module_id,
        "type": "ENTERPRISE",
        "created": "2024-01-01T00:00:00.000Z",
        "modified": "2024-01-01T00:00:00.000Z",
        "description": None,
        "configurationId": configuration_id,
        "enterpriseLogicalId": None,
        "schemaVersion": None,
        "version": version,
        "metadata": None,
        "name": name,
        "data": data,
    }
    if refs is not MISSING:
        row["refs"] = refs
    return row


class FakeSegmentData:
    """The ``data`` of a ``FakeSegment``: only its ``segment_id``."""

    def __init__(self, segment_id: int) -> None:
        """Data holding *segment_id*."""
        self.segment_id: int = segment_id


class FakeSegment:
    """Minimal segment matching NetworkSegment shape for SegmentResolver tests."""

    def __init__(
        self,
        id_: int,
        name: str,
        logical_id: uuid.UUID,
        segment_id: int,
        type_: NetworkSegmentType = NetworkSegmentType.REGULAR,
    ) -> None:
        """A segment with these fields, its *segment_id* under ``data``."""
        self.id_: int = id_
        self.name: str = name
        self.logical_id: uuid.UUID = logical_id
        self.type_: NetworkSegmentType = type_
        self.data: FakeSegmentData = FakeSegmentData(segment_id)


class FakeEdge:
    """Minimal edge object for EdgeResolver tests."""

    def __init__(self, name: str, id_: int, logical_id: uuid.UUID) -> None:
        """An edge with these three fields and nothing else."""
        self.name: str = name
        self.id_: int = id_
        self.logical_id: uuid.UUID = logical_id


async def make_segment_resolver(segments: list[dict[str, Any]]) -> SegmentResolver:
    """Build a ``SegmentResolver`` populated from a list of segment dicts.

    Expected dict keys: ``name``, ``segment_id``, optionally ``object_id``
    and ``segment_logical_id``.
    """
    fake_segments = [
        FakeSegment(
            id_=seg.get("object_id", seg["segment_id"]),
            name=seg["name"],
            logical_id=(
                uuid.UUID(str(seg["segment_logical_id"]))
                if "segment_logical_id" in seg
                else uuid.UUID(int=0)
            ),
            segment_id=seg["segment_id"],
        )
        for seg in segments
    ]
    client = AsyncMock()
    client.get_enterprise_network_segments.return_value = fake_segments
    resolver = SegmentResolver(client)
    await resolver.load(ResolverContext())
    return resolver


async def make_network_service_resolver(
    services: Iterable[NetworkServiceIdentity],
) -> NetworkServiceResolver:
    """Build a ``NetworkServiceResolver`` loaded with *services*, over a mock answer."""
    client = AsyncMock()
    client.get_enterprise_service_summaries.return_value = [
        EnterpriseServiceSummary(
            id_=service.object_id,
            logical_id=service.logical_id,
            name=service.name,
            type_=service.type_,
        )
        for service in services
    ]
    resolver = NetworkServiceResolver(client)
    await resolver.load(ResolverContext())
    return resolver


async def make_hub_resolver(
    hubs: Sequence[HubIdentity],
    *,
    members: Sequence[tuple[uuid.UUID, str]] = (),
) -> HubResolver:
    """Build a ``HubResolver`` loaded with *hubs*, over a mock answer.

    An ``edge`` hub is an Edge of the enterprise, a ``cluster`` hub a hub
    cluster. Each of *members*, a logical ID and a name, is an Edge that is a
    member of a hub cluster: no hub, and known to ``is_cluster_member``.
    """
    edges = [
        GetEnterpriseEdgeListItem(
            id_=hub.edge_id, logical_id=hub.logical_id, name=hub.name
        )
        for hub in hubs
        if hub.kind == "edge" and hub.edge_id is not None
    ]
    member_edges = {logical_id: 9000 + i for i, (logical_id, _) in enumerate(members)}
    edges += [
        GetEnterpriseEdgeListItem(
            id_=member_edges[logical_id], logical_id=logical_id, name=name
        )
        for logical_id, name in members
    ]
    clusters = [
        EdgeHubClusterService.model_validate(
            {
                "id": hub.cluster_id,
                "logicalId": str(hub.logical_id),
                "name": hub.name,
                "type": "edgeHubCluster",
                "data": {},
                "created": "2024-01-01T00:00:00.000Z",
            }
        )
        for hub in hubs
        if hub.kind == "cluster"
    ]
    member_services = [
        EdgeHubClusterMemberService.model_validate(
            {
                "id": 8000 + member_edges[logical_id],
                "logicalId": str(uuid.uuid5(logical_id, "member")),
                "name": name,
                "type": "edgeHubClusterMember",
                "edgeId": member_edges[logical_id],
                "data": {"logicalId": str(logical_id)},
                "created": "2024-01-01T00:00:00.000Z",
            }
        )
        for logical_id, name in members
    ]

    async def services(
        request: GetEnterpriseServices,
    ) -> Sequence[AnyEnterpriseService]:
        if request.type_ == EnterpriseServiceType.EDGE_HUB_CLUSTER:
            return clusters
        if request.type_ == EnterpriseServiceType.EDGE_HUB_CLUSTER_MEMBER:
            return member_services
        return []

    client = AsyncMock()
    client.get_enterprise_edge_list.return_value = edges
    client.get_enterprise_services.side_effect = services
    resolver = HubResolver(client)
    await resolver.load(ResolverContext())
    return resolver


def mock_vco_client_factory() -> Callable[..., Any]:
    """Return an async factory that builds a ``VcoClient`` over a MockTransport.

    Matches the call signature used by the legacy ``mock_vco_client`` fixture
    in both packages' conftests.
    """

    async def _factory(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        max_retries: int = 5,
        max_concurrent_requests: int | None = None,
        max_requests_per_window: int | None = None,
    ) -> VcoClient:
        return VcoClient(
            "test.velocloud.net",
            "test-token",
            max_retries=max_retries,
            max_concurrent_requests=max_concurrent_requests,
            max_requests_per_window=max_requests_per_window,
            transport=make_transport(handler),
        )

    return _factory
