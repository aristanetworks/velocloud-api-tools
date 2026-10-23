"""Tests for operation logic paths.

These tests mock at the VcoClient.portal() level to avoid constructing
deeply-nested configuration data models.
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import MISSING
from pyvelo.client import VcoClient
from pyvelo.client.exceptions import VcoOperationError
from pyvelo.client.models.configuration.device_settings.lan import LanSettings
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    ConfigurationModuleBase,
    DeviceSettingsModuleUpdate,
    FirewallModuleUpdate,
    InsertConfigurationModuleResult,
    QosModuleUpdate,
    UpdateConfigurationModule,
    WanModuleUpdate,
)
from pyvelo.core import RowsModifiedConfirmation
from pyvelo.core.base import ConfigContext

type ModuleUpdate = (
    DeviceSettingsModuleUpdate
    | QosModuleUpdate
    | WanModuleUpdate
    | FirewallModuleUpdate
)


def _module_update(request: UpdateConfigurationModule) -> ModuleUpdate:
    return cast(ModuleUpdate, request.update)


def _device_settings_data() -> DeviceSettingsData:
    return DeviceSettingsData(lan=LanSettings(interfaces=[], networks=[]), segments=[])


class TestUpdateConfigurationModuleErrors:
    async def test_raises_on_error_field(self):
        """update_configuration_module should raise when result.error is set."""
        client = VcoClient("test.velocloud.net", "test-token")

        error_result = RowsModifiedConfirmation.model_validate(
            {"error": "module not found", "rows": 0}
        )
        client.portal = AsyncMock(return_value=error_result)

        with pytest.raises(VcoOperationError, match="update configuration module"):
            await client.update_configuration_module(AsyncMock())

    async def test_success_returns_none(self):
        """update_configuration_module should return None on success."""
        client = VcoClient("test.velocloud.net", "test-token")

        success_result = RowsModifiedConfirmation.model_validate({"rows": 1})
        client.portal = AsyncMock(return_value=success_result)

        result = await client.update_configuration_module(AsyncMock())
        assert result is None


class TestInsertConfigurationModuleErrors:
    async def test_raises_on_error_field(self):
        """insert_configuration_module should raise when result.error is set."""
        client = VcoClient("test.velocloud.net", "test-token")

        error_result = InsertConfigurationModuleResult.model_validate(
            {"id": 0, "error": "duplicate module", "rows": 0}
        )
        client.portal = AsyncMock(return_value=error_result)

        with pytest.raises(VcoOperationError, match="insert configuration module"):
            _ = await client.insert_configuration_module(AsyncMock())

    async def test_success_returns_result(self):
        """insert_configuration_module should return the result on success."""
        client = VcoClient("test.velocloud.net", "test-token")

        success_result = InsertConfigurationModuleResult.model_validate(
            {"id": 42, "rows": 1}
        )
        client.portal = AsyncMock(return_value=success_result)

        result = await client.insert_configuration_module(AsyncMock())
        assert result.id_ == 42


class TestGetConfiguration:
    """get_configuration branches on result.name to set config context."""

    async def test_edge_specific_profile_calls_for_edge(self):
        """get_configuration should call for_edge() when name is 'Edge Specific Profile'."""
        client = VcoClient("test.velocloud.net", "test-token")

        result = ConfigurationBase.model_validate({"name": "Edge Specific Profile"})
        client.portal = AsyncMock(return_value=result)

        config = await client.get_configuration(AsyncMock())
        assert (
            cast(ConfigContext | None, getattr(config, "_config_context")) is not None
        )
        assert (
            cast(ConfigContext | None, getattr(config, "_config_context"))
            == ConfigContext.EDGE
        )

    async def test_profile_name_calls_for_profile(self):
        """get_configuration should call for_profile() for non-edge profiles."""
        client = VcoClient("test.velocloud.net", "test-token")

        result = ConfigurationBase.model_validate({"name": "Quick Start Profile"})
        client.portal = AsyncMock(return_value=result)

        config = await client.get_configuration(AsyncMock())
        assert (
            cast(ConfigContext | None, getattr(config, "_config_context")) is not None
        )
        assert (
            cast(ConfigContext | None, getattr(config, "_config_context"))
            == ConfigContext.PROFILE
        )

    async def test_returns_same_instance(self):
        """for_edge/for_profile returns self, so result should be the same object."""
        client = VcoClient("test.velocloud.net", "test-token")

        result = ConfigurationBase.model_validate({"name": "Edge Specific Profile"})
        client.portal = AsyncMock(return_value=result)

        config = await client.get_configuration(AsyncMock())
        assert config is result


class TestUpdateModule:
    """update_module dispatches to the correct ModuleUpdate type."""

    def _make_client(self) -> tuple[VcoClient, list[UpdateConfigurationModule]]:
        client = VcoClient("test.velocloud.net", "test-token")
        calls: list[UpdateConfigurationModule] = []

        async def capture(request: UpdateConfigurationModule) -> None:
            calls.append(request)

        client.update_configuration_module = capture  # type: ignore[method-assign]
        return client, calls

    def _make_module(self, module_id: int = 100) -> ConfigurationModuleBase:
        return ConfigurationModuleBase.model_validate(
            {
                "id": module_id,
                "type": "ENTERPRISE",
                "created": "2024-01-01T00:00:00.000Z",
                "modified": "2024-01-01T00:00:00.000Z",
                "description": None,
                "configurationId": None,
                "enterpriseLogicalId": None,
                "schemaVersion": None,
                "version": None,
                "metadata": None,
            }
        )

    async def test_device_settings_dispatch(self):
        """update_module should create DeviceSettingsModuleUpdate for DeviceSettingsData."""
        client, mock = self._make_client()
        module = self._make_module()
        data = _device_settings_data()

        await client.update_module(module, data)

        assert len(mock) == 1
        call_arg = mock[0]
        assert isinstance(call_arg, UpdateConfigurationModule)
        assert isinstance(_module_update(call_arg), DeviceSettingsModuleUpdate)

    async def test_qos_dispatch(self):
        """update_module should create QosModuleUpdate for QosData."""
        client, mock = self._make_client()
        module = self._make_module()
        data = QosData()

        await client.update_module(module, data)

        call_arg = mock[0]
        assert isinstance(_module_update(call_arg), QosModuleUpdate)

    async def test_wan_dispatch(self):
        """update_module should create WanModuleUpdate for WanData."""
        client, mock = self._make_client()
        module = self._make_module()
        data = WanData()

        await client.update_module(module, data)

        call_arg = mock[0]
        assert isinstance(_module_update(call_arg), WanModuleUpdate)

    async def test_firewall_dispatch(self):
        """update_module should create FirewallModuleUpdate for FirewallData."""
        client, mock = self._make_client()
        module = self._make_module()
        data = FirewallData()

        await client.update_module(module, data)

        call_arg = mock[0]
        assert isinstance(_module_update(call_arg), FirewallModuleUpdate)

    async def test_unsupported_type_raises(self):
        """update_module should raise TypeError for unsupported data types."""
        client, _mock = self._make_client()
        module = self._make_module()

        with pytest.raises(TypeError, match="Unsupported data type"):
            await client.update_module(
                module,
                cast(DeviceSettingsData | QosData | WanData | FirewallData, object()),
            )

    async def test_passes_module_id(self):
        """update_module should use the module's id_ for configuration_module_id."""
        client, mock = self._make_client()
        module = self._make_module(module_id=42)
        data = _device_settings_data()

        await client.update_module(module, data)

        call_arg = mock[0]
        assert call_arg.configuration_module_id == 42

    async def test_enterprise_id_passed_when_provided(self):
        """update_module should forward enterprise_id when given."""
        client, mock = self._make_client()
        module = self._make_module()
        data = _device_settings_data()

        await client.update_module(module, data, enterprise_id=99)

        call_arg = mock[0]
        assert call_arg.enterprise_id == 99

    async def test_enterprise_id_missing_when_not_provided(self):
        """update_module should use MISSING for enterprise_id when not given."""
        client, mock = self._make_client()
        module = self._make_module()
        data = _device_settings_data()

        await client.update_module(module, data)

        call_arg = mock[0]
        assert call_arg.enterprise_id is MISSING

    async def test_refs_forwarded_for_device_settings(self):
        """update_module should pass refs to DeviceSettingsModuleUpdate when provided."""
        client, mock = self._make_client()
        module = self._make_module()
        data = MagicMock(spec=DeviceSettingsData)
        refs = {"deviceSettings": {"some": "ref"}}

        await client.update_module(module, data, refs=refs)

        call_arg = mock[0]
        update = _module_update(call_arg)
        assert isinstance(update, DeviceSettingsModuleUpdate)
        assert update.refs is not MISSING


class TestDeleteEdgeErrors:
    async def test_raises_on_error_field(self):
        """delete_edge should raise when result.error is set."""
        client = VcoClient("test.velocloud.net", "test-token")

        error_result = RowsModifiedConfirmation.model_validate(
            {"error": "edge not found", "rows": 0}
        )
        client.portal = AsyncMock(return_value=error_result)

        with pytest.raises(VcoOperationError, match="delete edge"):
            await client.delete_edge(AsyncMock())

    async def test_success_returns_none(self):
        """delete_edge should return None on success."""
        client = VcoClient("test.velocloud.net", "test-token")

        success_result = RowsModifiedConfirmation.model_validate({"rows": 1})
        client.portal = AsyncMock(return_value=success_result)

        result = await client.delete_edge(AsyncMock())
        assert result is None
