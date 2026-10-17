"""Shared SDK test fixtures: JSON-RPC response builders, mock VCO client, segment resolver factory."""

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

__all__ = [
    "FakeEdge",
    "FakeSegment",
    "FakeSegmentData",
    "make_jsonrpc_response",
    "make_transport",
]


@pytest.fixture
def json_rpc_response():
    """Factory fixture for JSON-RPC responses."""
    return make_jsonrpc_response


@pytest.fixture
def make_segment_resolver():
    """Factory fixture for building SegmentResolver from fake segment dicts."""
    return _make_segment_resolver


@pytest.fixture
async def mock_vco_client():
    """Factory that creates a VcoClient backed by a MockTransport."""
    return mock_vco_client_factory()
