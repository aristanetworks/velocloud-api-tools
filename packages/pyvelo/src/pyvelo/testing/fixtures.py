"""Reusable fakes and factories for tests."""

import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx

from pyvelo.client import VcoAuth, VcoClient
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.segment import SegmentResolver


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
        )
        client._http = httpx.AsyncClient(
            auth=VcoAuth("test-token"),
            transport=make_transport(handler),
            base_url="https://test.velocloud.net",
        )
        return client

    return _factory
