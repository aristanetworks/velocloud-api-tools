"""Tests for ConfigPipeline.save()."""

import pytest
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    UpdateConfigurationModule,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState, SaveResult
from pyvelo_intent.testing import MockVcoClient, make_firewall_module


def _make_state() -> ConfigState:
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
    state = ConfigState(
        edge_config=edge_config,
        profile_config=profile_config,
        target_type="edge",
        resolver_context=ResolverContext(),
    )
    state.take_snapshot()
    return state


@pytest.mark.asyncio
async def test_save_no_changes():
    """Save with no changes should return empty SaveResult."""
    client = MockVcoClient()
    pipeline = ConfigPipeline(client)
    state = _make_state()

    result = await pipeline.save(state)

    assert result.modules_updated == []
    assert result.modules_inserted == []
    assert len(client.calls) == 0


@pytest.mark.asyncio
async def test_save_updates_changed_module():
    """Save should call update_configuration_module for changed modules."""
    client = MockVcoClient()
    pipeline = ConfigPipeline(client)
    state = _make_state()

    # Mutate the firewall module
    assert state.edge_config is not None
    state.edge_config.firewall.data.firewall_enabled = False

    result = await pipeline.save(state)

    assert ConfigurationModuleName.FIREWALL in result.modules_updated
    assert len(client.calls) == 1
    call_name, request = client.calls[0]
    assert call_name == "update_configuration_module"
    assert isinstance(request, UpdateConfigurationModule)
    assert request.configuration_module_id == 1


@pytest.mark.asyncio
async def test_save_dry_run():
    """dry_run=True should report changes without making API calls."""
    client = MockVcoClient()
    pipeline = ConfigPipeline(client)
    state = _make_state()

    # Mutate the firewall module
    assert state.edge_config is not None
    state.edge_config.firewall.data.firewall_enabled = False

    result = await pipeline.save(state, dry_run=True)

    assert ConfigurationModuleName.FIREWALL in result.modules_updated
    assert len(client.calls) == 0  # No API calls


@pytest.mark.asyncio
async def test_save_result_type():
    """SaveResult should have correct types."""
    result = SaveResult(
        modules_updated=[ConfigurationModuleName.FIREWALL],
        modules_inserted=[ConfigurationModuleName.QOS],
    )
    assert result.modules_updated == [ConfigurationModuleName.FIREWALL]
    assert result.modules_inserted == [ConfigurationModuleName.QOS]
    assert result.warnings == []
