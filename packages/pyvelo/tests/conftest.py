"""Shared SDK test fixtures: JSON-RPC response builders, mock VCO client, segment resolver factory."""

from typing import cast

import pytest
from pyvelo.testing import (
    FakeEdge,
    FakeSegment,
    FakeSegmentData,
    make_jsonrpc_response,
    make_transport,
    mock_vco_client_factory,
)
from pyvelo.testing import make_segment_resolver as _make_segment_resolver

from ._type_support import (
    JsonRpcResponseFactory,
    MockVcoClientFactory,
    SegmentResolverFactory,
)

__all__ = [
    "FakeEdge",
    "FakeSegment",
    "FakeSegmentData",
    "make_jsonrpc_response",
    "make_transport",
]


@pytest.fixture
def json_rpc_response() -> JsonRpcResponseFactory:
    """Factory fixture for JSON-RPC responses."""
    return cast(JsonRpcResponseFactory, make_jsonrpc_response)


@pytest.fixture
def make_segment_resolver() -> SegmentResolverFactory:
    """Factory fixture for building SegmentResolver from fake segment dicts."""
    return cast(SegmentResolverFactory, _make_segment_resolver)


@pytest.fixture
async def mock_vco_client() -> MockVcoClientFactory:
    """Factory that creates a VcoClient backed by a MockTransport."""
    return cast(MockVcoClientFactory, mock_vco_client_factory())
