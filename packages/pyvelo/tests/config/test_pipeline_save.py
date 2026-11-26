"""Tests for ConfigPipeline.save()."""

from typing import cast

import httpx
import pytest
from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import VersionRequiredError
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    UpdateConfigurationModule,
)
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.state import ConfigState
from pyvelo.core.layers import Profile
from pyvelo.testing import MockVcoClient, make_firewall_module


def _make_state() -> ConfigState:
    edge_config = EdgeConfiguration(
        name="Edge Specific Profile",
        id_=10,
        modules=[make_firewall_module(mod_id=1, config_id=10)],
    )
    profile_config = EnterpriseConfigurationProfile(
        name="Default Profile",
        id_=20,
        modules=[make_firewall_module(mod_id=2, config_id=20, layer=Profile)],
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
async def test_save_no_changes() -> None:
    """Save with no changes should return empty SaveResult."""
    state = _make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)

    result = await pipeline.save(state)

    assert result.modules_updated == []
    assert result.modules_inserted == []
    assert (result.server_changes, result.left_out) == ({}, {})
    assert len(client.calls) == 0


@pytest.mark.asyncio
async def test_save_updates_changed_module() -> None:
    """Save should call update_configuration_module for changed modules."""
    state = _make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)

    # Mutate the firewall module
    assert state.edge_config is not None
    state.edge_config.firewall.data.firewall_enabled.set_local(False)

    result = await pipeline.save(state)

    assert ConfigurationModuleName.FIREWALL in result.modules_updated
    assert len(client.calls) == 1
    call_name, request = client.calls[0]
    assert call_name == "update_configuration_module"
    assert isinstance(request, UpdateConfigurationModule)
    assert request.configuration_module_id == 1


@pytest.mark.asyncio
async def test_save_dry_run() -> None:
    """dry_run=True should report changes without making API calls."""
    client = MockVcoClient()
    pipeline = ConfigPipeline(client)
    state = _make_state()

    # Mutate the firewall module
    assert state.edge_config is not None
    state.edge_config.firewall.data.firewall_enabled.set_local(False)

    result = await pipeline.save(state, dry_run=True)

    assert ConfigurationModuleName.FIREWALL in result.modules_updated
    assert len(client.calls) == 0  # No API calls


@pytest.mark.asyncio
async def test_keyword_built_module_saves_only_the_change() -> None:
    state = _make_state()
    client = MockVcoClient.for_state(state)
    assert state.edge_config is not None
    before = cast("dict[str, object]", state.edge_config.firewall.raw_data)
    state.edge_config.firewall.data.firewall_enabled.set_local(False)
    await ConfigPipeline(client).save(state)
    assert client.update_requests[0].update.data == {
        **before,
        "firewall_enabled": False,
    }


@pytest.mark.asyncio
async def test_mock_client_seeds_from_a_state_and_records_requests() -> None:
    state = _make_state()
    client = MockVcoClient.for_state(state)
    assert set(client.store.rows) == {1, 2}
    assert set(client.store.configurations) == {10, 20}
    assert state.edge_config is not None
    state.edge_config.firewall.data.firewall_enabled.set_local(False)
    await ConfigPipeline(client).save(state)
    (request,) = client.update_requests
    assert request.configuration_module_id == 1
    assert client.store.rows[1]["data"] == request.update.data
    assert [method for method, _ in client.store.requests] == [
        "configuration/updateConfigurationModule"
    ]


@pytest.mark.asyncio
async def test_mock_client_under_async_with_still_talks_to_its_store() -> None:
    state = _make_state()
    client = MockVcoClient.for_state(state)
    async with client:
        # Checked before anything is sent: without the store's transport a
        # request would leave the machine.
        http = client._http_client()  # pyright: ignore[reportPrivateUsage]
        assert isinstance(http._transport, httpx.MockTransport)  # pyright: ignore[reportPrivateUsage]
        assert state.edge_config is not None
        state.edge_config.firewall.data.firewall_enabled.set_local(False)
        _ = await ConfigPipeline(client).save(state)
    assert [method for method, _ in client.store.requests] == [
        "configuration/updateConfigurationModule"
    ]


@pytest.mark.parametrize("dry_run", [True, False])
async def test_save_refuses_a_module_with_no_version_before_it_sends(
    dry_run: bool,  # noqa: FBT001 (a parameter of the test)
) -> None:
    state = _make_state()
    assert state.edge_config is not None
    module = state.edge_config.firewall
    module.version = None
    client = MockVcoClient.for_state(state)
    module.data.firewall_enabled.set_local(False)

    with pytest.raises(VersionRequiredError, match='"unconditional": true'):
        _ = await ConfigPipeline(client).save(state, dry_run=dry_run)

    assert client.update_requests == []
    assert [p.name for p in state.plan().changed] == ["firewall"]


async def test_save_writes_a_module_with_no_version_when_asked_to() -> None:
    state = _make_state()
    assert state.edge_config is not None
    module = state.edge_config.firewall
    module.version = None
    client = MockVcoClient.for_state(state)
    module.data.firewall_enabled.set_local(False)

    result = await ConfigPipeline(client).save(state, unconditional=True)

    assert [str(name) for name in result.modules_updated] == ["firewall"]
    assert client.update_requests[0].version is MISSING
    assert client.update_requests[0].update.data["firewall_enabled"] is False
