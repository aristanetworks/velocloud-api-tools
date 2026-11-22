"""Tests for operation logic paths.

These tests mock at the VcoClient.portal() level to avoid constructing
deeply-nested configuration data models.
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import MISSING
from pyvelo.client import VcoClient
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import VcoOperationError
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    ConfigurationModuleBase,
    FirewallConfigurationModule,
    InsertConfigurationModuleResult,
    QosConfigurationModule,
    UpdateConfigurationModule,
    new_edge_module,
)
from pyvelo.core import RowsModifiedConfirmation
from pyvelo.core.base import ConfigContext
from pyvelo.testing import make_module_json


class TestUpdateConfigurationModuleErrors:
    async def test_raises_on_error_field(self):
        """update_configuration_module should raise when result.error is set."""
        client = VcoClient("test.velocloud.net", "test-token")

        error_result = RowsModifiedConfirmation.model_validate(
            {"error": "module not found", "rows": 0}
        )
        client.portal = AsyncMock(return_value=error_result)

        with pytest.raises(VcoOperationError, match="update configuration module"):
            _ = await client.update_configuration_module(AsyncMock())

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
        assert cast(ConfigContext | None, config._config_context) is not None
        assert cast(ConfigContext | None, config._config_context) == ConfigContext.EDGE

    async def test_profile_name_calls_for_profile(self):
        """get_configuration should call for_profile() for non-edge profiles."""
        client = VcoClient("test.velocloud.net", "test-token")

        result = ConfigurationBase.model_validate({"name": "Quick Start Profile"})
        client.portal = AsyncMock(return_value=result)

        config = await client.get_configuration(AsyncMock())
        assert cast(ConfigContext | None, config._config_context) is not None
        assert (
            cast(ConfigContext | None, config._config_context) == ConfigContext.PROFILE
        )

    async def test_returns_same_instance(self):
        """for_edge/for_profile returns self, so result should be the same object."""
        client = VcoClient("test.velocloud.net", "test-token")

        result = ConfigurationBase.model_validate({"name": "Edge Specific Profile"})
        client.portal = AsyncMock(return_value=result)

        config = await client.get_configuration(AsyncMock())
        assert config is result


class TestUpdateModule:
    """update_module sends the module's raw data with the caller's changes."""

    def _make_client(self) -> tuple[VcoClient, list[UpdateConfigurationModule]]:
        client = VcoClient("test.velocloud.net", "test-token")
        calls: list[UpdateConfigurationModule] = []

        async def capture(
            request: UpdateConfigurationModule, *, level: ConfigContext | None = None
        ) -> AnyConfigurationModule | None:
            calls.append(request)
            return None

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

    def _fw(
        self,
        raw: dict[str, object],
        *,
        module_id: int = 1,
        version: str | None = "v1",
    ) -> FirewallConfigurationModule:
        return FirewallConfigurationModule.model_validate(
            make_module_json("firewall", raw, module_id=module_id, version=version)
        )

    async def test_sends_raw_payload_with_version(self):
        client, calls = self._make_client()
        module = self._fw({"segments": [], "future": 1}, module_id=42, version="1700")
        data = module.mutable_data()
        data.firewall_enabled = False
        await client.update_module(module, data, enterprise_id=99)
        (req,) = calls
        assert (req.configuration_module_id, req.enterprise_id, req.version) == (
            42,
            99,
            "1700",
        )
        assert req.update.data == {
            "segments": [],
            "future": 1,
            "firewall_enabled": False,
        }
        assert req.update.refs is MISSING
        assert req.model_dump(mode="json", by_alias=True) == {
            "id": 42,
            "enterpriseId": 99,
            "version": "1700",
            "_update": {
                "data": {"segments": [], "future": 1, "firewall_enabled": False}
            },
        }

    async def test_no_change_sends_nothing(self):
        client, calls = self._make_client()
        module = self._fw({"segments": []})
        await client.update_module(module, module.mutable_data())
        assert calls == []

    async def test_null_version_and_enterprise_are_omitted(self):
        client, calls = self._make_client()
        module = self._fw({"segments": []}, version=None)
        data = module.mutable_data()
        data.firewall_enabled = False
        await client.update_module(module, data)
        assert calls[0].version is MISSING and calls[0].enterprise_id is MISSING

    async def test_refs_forwarded_only_when_changed(self):
        client, calls = self._make_client()
        qos = QosConfigurationModule.model_validate(
            make_module_json("QOS", {"segments": []}, refs={"a": 1})
        )
        await client.update_module(qos, qos.mutable_data(), refs={"a": 1})
        assert calls == []
        await client.update_module(qos, qos.mutable_data(), refs={"a": 2})
        assert calls[0].update.refs == {"a": 2}

    async def test_profile_argument_reconciles_segments(self):
        client, calls = self._make_client()
        seg = {
            "segment": {"segmentId": 2, "name": "Guest"},
            "outbound": [
                {"name": "p", "match": {}, "action": {"allow_or_deny": "allow"}}
            ],
        }
        edge = self._fw({"segments": []})
        data = edge.mutable_data().for_edge()
        data.firewall_enabled = False
        await client.update_module(
            edge, data, profile=self._fw({"segments": [seg]}, module_id=2)
        )
        assert calls[0].update.data["segments"] == [
            {"segment": {"segmentId": 2, "name": "Guest"}, "outbound": []}
        ]

    async def test_module_without_snapshot_sends_the_whole_dump(self):
        client, calls = self._make_client()
        data = FirewallData(segments=[])
        await client.update_module(self._make_module(), data)
        assert calls[0].update.data == data.model_dump(mode="json", by_alias=True)

    async def test_new_module_is_rejected(self):
        client, _ = self._make_client()
        mod = new_edge_module(ConfigurationModuleName.FIREWALL, 10)
        with pytest.raises(ValueError, match="insert"):
            await client.update_module(mod, mod.data)

    async def test_unsupported_type_raises(self):
        """update_module should raise TypeError for unsupported data types."""
        client, _mock = self._make_client()
        module = self._make_module()

        with pytest.raises(TypeError, match="Unsupported data type"):
            await client.update_module(
                module,
                cast(DeviceSettingsData | QosData | WanData | FirewallData, object()),
            )


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
