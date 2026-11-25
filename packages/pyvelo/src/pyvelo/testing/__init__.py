"""Shared testing helpers for pyvelo and downstream packages.

Importable from any test suite that depends on pyvelo. Exposes minimal
fakes (``FakeSegment``, ``FakeEdge``), a JSON-RPC envelope builder, a
``mock_vco_client`` factory backed by ``httpx.MockTransport``, and
``ModuleStore``, a fake VCO that holds configuration module rows and serves
their reads and writes to a client built over it.
"""

from pyvelo.testing.fixtures import (
    FakeEdge,
    FakeSegment,
    FakeSegmentData,
    make_jsonrpc_response,
    make_module_json,
    make_segment_resolver,
    make_transport,
    mock_vco_client_factory,
)
from pyvelo.testing.module_store import (
    ModuleStore,
    RpcError,
    StoreHook,
    as_stored,
    configuration_row,
    module_row,
    store_of,
)

__all__ = [
    "FakeEdge",
    "FakeSegment",
    "FakeSegmentData",
    "ModuleStore",
    "RpcError",
    "StoreHook",
    "as_stored",
    "configuration_row",
    "make_jsonrpc_response",
    "make_module_json",
    "make_segment_resolver",
    "make_transport",
    "mock_vco_client_factory",
    "module_row",
    "store_of",
]
