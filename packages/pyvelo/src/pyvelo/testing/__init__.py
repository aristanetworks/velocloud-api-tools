"""Shared testing helpers for pyvelo and downstream packages.

Importable from any test suite that depends on pyvelo. Exposes minimal
fakes (``FakeSegment``, ``FakeEdge``), a JSON-RPC envelope builder, and a
``mock_vco_client`` factory backed by ``httpx.MockTransport``.
"""

from pyvelo.testing.fixtures import (
    FakeEdge,
    FakeSegment,
    FakeSegmentData,
    make_jsonrpc_response,
    make_segment_resolver,
    make_transport,
    mock_vco_client_factory,
)

__all__ = [
    "FakeEdge",
    "FakeSegment",
    "FakeSegmentData",
    "make_jsonrpc_response",
    "make_segment_resolver",
    "make_transport",
    "mock_vco_client_factory",
]
