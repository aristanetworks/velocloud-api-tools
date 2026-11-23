"""Reusable fakes and factories for tests."""

import uuid
from collections.abc import Callable, Iterable
from typing import Any
from unittest.mock import AsyncMock

import httpx
from pydantic import MISSING

from pyvelo.client import VcoClient
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.schemas.enterprise import EnterpriseServiceSummary
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.core.identity import NetworkServiceIdentity


def make_jsonrpc_response(
    result: Any = None,
    error: dict[str, Any] | None = None,
    id: int = 1,
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 response envelope."""
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
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


def make_module_json(
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
    def __init__(self, segment_id: int):
        self.segment_id = segment_id


class FakeSegment:
    """Minimal segment matching NetworkSegment shape for SegmentResolver tests."""

    def __init__(
        self,
        id_: int,
        name: str,
        logical_id: uuid.UUID,
        segment_id: int,
        type_: NetworkSegmentType = NetworkSegmentType.REGULAR,
    ):
        self.id_ = id_
        self.name = name
        self.logical_id = logical_id
        self.type_ = type_
        self.data = FakeSegmentData(segment_id)


class FakeEdge:
    """Minimal edge object for EdgeResolver tests."""

    def __init__(self, name: str, id_: int, logical_id: uuid.UUID):
        self.name = name
        self.id_ = id_
        self.logical_id = logical_id


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
        client = VcoClient(
            "test.velocloud.net",
            "test-token",
            max_retries=max_retries,
            max_concurrent_requests=max_concurrent_requests,
            max_requests_per_window=max_requests_per_window,
            transport=make_transport(handler),
        )
        return client

    return _factory
