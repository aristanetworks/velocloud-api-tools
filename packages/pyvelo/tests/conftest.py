"""Shared SDK test fixtures.

JSON-RPC response builders, mock VCO client, segment resolver factory.
"""

from typing import cast

import pytest
from _pytest.mark.expression import Expression
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

# The marks of a live test that only reads, and of one that writes.
_LIVE_MARKS = ({"integration"}, {"integration", "live_write"})


def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """No workers when the live tests are selected, so they run in one process.

    Each worker would connect its own client, each with its own rate limit, so
    together they could send the Orchestrator many times its limit. And the
    tests that write would write to the one enterprise at once.
    """
    selection = cast("str", config.getoption("markexpr"))
    if not selection:
        return 0
    expression = Expression.compile(selection)
    if any(_selects(expression, marks) for marks in _LIVE_MARKS):
        return 0
    return None


def _selects(expression: Expression, marks: set[str]) -> bool:
    """Whether ``-m`` selects a test with these marks."""
    return expression.evaluate(lambda name, /, **_: name in marks)


@pytest.fixture
def json_rpc_response() -> JsonRpcResponseFactory:
    """Factory fixture for JSON-RPC responses."""
    return cast("JsonRpcResponseFactory", make_jsonrpc_response)


@pytest.fixture
def make_segment_resolver() -> SegmentResolverFactory:
    """Factory fixture for building SegmentResolver from fake segment dicts."""
    return cast("SegmentResolverFactory", _make_segment_resolver)


@pytest.fixture
async def mock_vco_client() -> MockVcoClientFactory:
    """Factory that creates a VcoClient backed by a MockTransport."""
    return cast("MockVcoClientFactory", mock_vco_client_factory())
