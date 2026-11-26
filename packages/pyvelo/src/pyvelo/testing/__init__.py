"""Shared testing helpers for pyvelo and downstream packages.

Importable from any test suite that depends on pyvelo. Exposes minimal
fakes (``FakeSegment``, ``FakeEdge``), a JSON-RPC envelope builder, a
``mock_vco_client`` factory backed by ``httpx.MockTransport``, and
``ModuleStore``, a fake VCO that holds configuration module rows and serves
their reads and writes to a client built over it. For the configuration
pipeline there is ``MockVcoClient``, a client that holds such a store, and the
firewall builders ``make_firewall_module`` and ``make_firewall_segment``.
``network_segment_wire`` builds one item of a ``getEnterpriseNetworkSegments``
answer in wire keys, which a test parses with ``NetworkSegment.model_validate``
so that its fake gives a segment as the VCO's answer does;
``make_segment_resolver`` builds a loaded segment resolver that way.
"""

from pyvelo.testing.fixtures import (
    FakeEdge,
    FakeSegment,
    FakeSegmentData,
    make_jsonrpc_response,
    make_module_json,
    make_network_service_resolver,
    make_segment_resolver,
    make_transport,
    mock_vco_client_factory,
    network_segment_wire,
)
from pyvelo.testing.mock_client import (
    MockVcoClient,
    make_firewall_module,
    make_firewall_segment,
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
    "MockVcoClient",
    "ModuleStore",
    "RpcError",
    "StoreHook",
    "as_stored",
    "configuration_row",
    "make_firewall_module",
    "make_firewall_segment",
    "make_jsonrpc_response",
    "make_module_json",
    "make_network_service_resolver",
    "make_segment_resolver",
    "make_transport",
    "mock_vco_client_factory",
    "module_row",
    "network_segment_wire",
    "store_of",
]
