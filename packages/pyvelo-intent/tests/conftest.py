"""Shared intent/pipeline test fixtures."""

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
    return make_jsonrpc_response


@pytest.fixture
def make_segment_resolver():
    return _make_segment_resolver


@pytest.fixture
async def mock_vco_client():
    """Factory that creates a real VcoClient backed by a MockTransport."""
    return mock_vco_client_factory()
