"""Tests for ConfigPipeline.load() and load_profile()."""

import uuid

import pytest
from pyvelo.client.operations.edge import GetEdgeConfigurationStackResult
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.client.schemas.enterprise import GetEnterpriseEdgesItem
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient, make_firewall_module


def _make_stack() -> GetEdgeConfigurationStackResult:
    """Create a mock edge configuration stack (edge_config, profile_config)."""
    edge_config = EdgeConfiguration(
        name="Edge Specific Profile",
        id_=10,
        modules=[make_firewall_module(mod_id=1, config_id=10)],
    )
    profile_config = EnterpriseConfigurationProfile(
        name="Default Profile",
        id_=20,
        modules=[make_firewall_module(mod_id=2, config_id=20)],
    )
    return (edge_config, profile_config)


@pytest.mark.asyncio
async def test_load_by_edge_id():
    """Load by numeric edge ID should fetch config stack."""
    client = MockVcoClient(stack=_make_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load(42, enterprise_id=1)

    assert isinstance(state, ConfigState)
    assert state.target_type == "edge"
    assert state.edge_config is not None
    assert state.edge_config.name == "Edge Specific Profile"
    assert state.profile_config.name == "Default Profile"
    assert state.resolver_context.edge_id == 42
    assert state.resolver_context.enterprise_id == 1

    # Verify API call was made
    stack_calls = [c for c in client.calls if c[0] == "get_edge_configuration_stack"]
    assert len(stack_calls) == 1
    req = stack_calls[0][1]
    assert isinstance(req, GetEdgeConfigurationStack)
    assert req.edge_id == 42


@pytest.mark.asyncio
async def test_load_takes_snapshot():
    """Loading should take a snapshot for later diff detection."""
    client = MockVcoClient(stack=_make_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load(42)

    # Both loaded configurations should have public snapshots.
    assert isinstance(state.snapshot_for_target(), EdgeConfiguration)
    state.target_type = "profile"
    assert isinstance(state.snapshot_for_target(), EnterpriseConfigurationProfile)

    # No changes yet, so get_changed_modules should return empty
    assert state.get_changed_modules() == []


@pytest.mark.asyncio
async def test_load_by_edge_name():
    """Load by edge name should resolve name via EdgeResolver first."""
    client = MockVcoClient(
        stack=_make_stack(),
        edges=[
            GetEnterpriseEdgesItem.model_construct(
                name="Branch-NYC", id_=42, logical_id=uuid.UUID(int=0)
            )
        ],
    )

    # Set up edge resolver
    registry = ResolverRegistry()
    edge_resolver = EdgeResolver(client)
    registry.register(edge_resolver)

    pipeline = ConfigPipeline(client, resolvers=registry)
    state = await pipeline.load("Branch-NYC")

    assert state.resolver_context.edge_id == 42
    # The get_edge_configuration_stack call should have used edge_id=42
    stack_calls = [c for c in client.calls if c[0] == "get_edge_configuration_stack"]
    assert len(stack_calls) == 1
    req = stack_calls[0][1]
    assert isinstance(req, GetEdgeConfigurationStack)
    assert req.edge_id == 42


@pytest.mark.asyncio
async def test_load_profile_by_id():
    """load_profile by numeric ID should fetch profile config."""
    client = MockVcoClient(stack=_make_stack())
    pipeline = ConfigPipeline(client)

    state = await pipeline.load_profile(20, enterprise_id=1)

    assert state.target_type == "profile"
    assert state.edge_config is None
    assert state.profile_config.name == "Default Profile"
    assert state.resolver_context.enterprise_id == 1
