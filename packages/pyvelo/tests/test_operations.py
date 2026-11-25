"""Tests for operation logic paths.

Most of these tests mock at the VcoClient.portal() level to avoid constructing
deeply-nested configuration data models. ``update_module`` runs over a
``ModuleStore``, which answers its writes as a VCO does.
"""

import inspect
from typing import TYPE_CHECKING, cast, overload
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import MISSING
from pyvelo.client import VcoClient
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import VcoOperationError
from pyvelo.client.models.configuration.device_settings.segment import (
    DnsPrimaryProvider,
    SegmentDns,
)
from pyvelo.client.module_check import ModuleWrite
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    GetConfiguration,
    InsertConfigurationModuleResult,
    UpdateConfigurationModule,
    new_edge_module,
)
from pyvelo.core import RowsModifiedConfirmation
from pyvelo.core.fields import ConfigContext
from pyvelo.core.identity import NetworkServiceIdentity
from pyvelo.core.layers import Edge, Profile
from pyvelo.testing import ModuleStore, make_module_json, store_of
from refs_helpers import (
    DNS_A,
    SEG0,
    SEG0_OBJECT,
    dns,
    ds_module,
    segment_entry,
    segment_row,
    table_registry,
)

from ._type_support import MockVcoClientFactory

if TYPE_CHECKING:
    from pyvelo.client.models.configuration.device_settings.root import (
        DeviceSettingsData,
    )
    from pyvelo.client.models.configuration.firewall.root import FirewallData
    from pyvelo.client.models.configuration.qos.root import QosData
    from pyvelo.client.models.configuration.wan.root import WanData

UPDATE = "configuration/updateConfigurationModule"


class TestUpdateConfigurationModuleErrors:
    async def test_raises_on_error_field(self) -> None:
        """update_configuration_module should raise when result.error is set."""
        client = VcoClient("test.velocloud.net", "test-token")

        error_result = RowsModifiedConfirmation.model_validate(
            {"error": "module not found", "rows": 0}
        )
        client.portal = AsyncMock(return_value=error_result)

        with pytest.raises(VcoOperationError, match="update configuration module"):
            _ = await client.update_configuration_module(AsyncMock())

    async def test_success_returns_none(self) -> None:
        """update_configuration_module should return None on success."""
        client = VcoClient("test.velocloud.net", "test-token")

        success_result = RowsModifiedConfirmation.model_validate({"rows": 1})
        client.portal = AsyncMock(return_value=success_result)

        result = await client.update_configuration_module(AsyncMock())
        assert result is None


class TestInsertConfigurationModuleErrors:
    async def test_raises_on_error_field(self) -> None:
        """insert_configuration_module should raise when result.error is set."""
        client = VcoClient("test.velocloud.net", "test-token")

        error_result = InsertConfigurationModuleResult.model_validate(
            {"id": 0, "error": "duplicate module", "rows": 0}
        )
        client.portal = AsyncMock(return_value=error_result)

        with pytest.raises(VcoOperationError, match="insert configuration module"):
            _ = await client.insert_configuration_module(AsyncMock())

    async def test_success_returns_result(self) -> None:
        """insert_configuration_module should return the result on success."""
        client = VcoClient("test.velocloud.net", "test-token")

        success_result = InsertConfigurationModuleResult.model_validate(
            {"id": 42, "rows": 1}
        )
        client.portal = AsyncMock(return_value=success_result)

        result = await client.insert_configuration_module(AsyncMock())
        assert result.id_ == 42


class TestGetConfiguration:
    """get_configuration learns the layer from the name of the answer."""

    async def test_edge_specific_profile_is_an_edge_configuration(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        store = ModuleStore()
        store.add_configuration({"id": 10, "name": "Edge Specific Profile"})
        client = await mock_vco_client(store)

        config = await client.get_configuration(GetConfiguration(id_=10))
        assert type(config) is EdgeConfiguration
        assert config.config_context is ConfigContext.EDGE

    async def test_profile_name_is_a_profile_configuration(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        store = ModuleStore()
        store.add_configuration({"id": 20, "name": "Quick Start Profile"})
        client = await mock_vco_client(store)

        config = await client.get_configuration(GetConfiguration(id_=20))
        assert type(config) is EnterpriseConfigurationProfile
        assert config.config_context is ConfigContext.PROFILE


class TestUpdateModule:
    """update_module sends the module's raw data with the caller's changes."""

    async def _make_client(
        self, mock_vco_client: MockVcoClientFactory, *modules: ConfigurationModuleBase
    ) -> tuple[VcoClient, ModuleStore]:
        store = store_of(*modules)
        return await mock_vco_client(store), store

    def _updates(self, store: ModuleStore) -> list[UpdateConfigurationModule]:
        """Each update the store was sent, as a request."""
        return [
            UpdateConfigurationModule.model_validate(params)
            for method, params in store.requests
            if method == UPDATE
        ]

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

    @overload
    def _fw(
        self,
        raw: dict[str, object],
        *,
        layer: type[Edge],
        module_id: int = ...,
        version: str | None = ...,
    ) -> FirewallConfigurationModule[Edge]: ...
    @overload
    def _fw(
        self,
        raw: dict[str, object],
        *,
        layer: type[Profile],
        module_id: int = ...,
        version: str | None = ...,
    ) -> FirewallConfigurationModule[Profile]: ...
    def _fw(
        self,
        raw: dict[str, object],
        *,
        layer: type[Edge] | type[Profile],
        module_id: int = 1,
        version: str | None = "v1",
    ) -> FirewallConfigurationModule[Edge] | FirewallConfigurationModule[Profile]:
        row = make_module_json("firewall", raw, module_id=module_id, version=version)
        if layer is Edge:
            return FirewallConfigurationModule[Edge].model_validate(row)
        return FirewallConfigurationModule[Profile].model_validate(row)

    async def test_sends_raw_payload_with_version(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        module = self._fw(
            {"segments": [], "future": 1}, layer=Edge, module_id=42, version="1700"
        )
        client, store = await self._make_client(mock_vco_client, module)
        data = module.mutable_data()
        data.firewall_enabled.set_local(False)
        _ = await client.update_module(module, data, enterprise_id=99)
        (req,) = self._updates(store)
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
        assert store.requests == [
            (
                UPDATE,
                {
                    "id": 42,
                    "enterpriseId": 99,
                    "version": "1700",
                    "_update": {
                        "data": {"segments": [], "future": 1, "firewall_enabled": False}
                    },
                    "returnData": True,
                },
            )
        ]

    async def test_no_change_sends_nothing(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        module = self._fw({"segments": []}, layer=Edge)
        client, store = await self._make_client(mock_vco_client, module)
        _ = await client.update_module(module, module.mutable_data())
        assert store.requests == []

    async def test_null_version_and_enterprise_are_omitted(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        module = self._fw({"segments": []}, layer=Edge, version=None)
        client, store = await self._make_client(mock_vco_client, module)
        data = module.mutable_data()
        data.firewall_enabled.set_local(False)
        _ = await client.update_module(module, data)
        (req,) = self._updates(store)
        assert req.version is MISSING
        assert req.enterprise_id is MISSING

    def test_update_module_has_no_refs_parameter(self) -> None:
        parameters = inspect.signature(VcoClient.update_module).parameters
        assert "refs" not in parameters
        assert "resolvers" in parameters

    async def test_update_module_passes_resolvers_to_the_plan(self) -> None:
        module = ds_module(
            [segment_entry(0, "Global Segment", SEG0, dns=dns(primary=True))],
            {"deviceSettings:segment": segment_row(0, SEG0_OBJECT, SEG0)},
        )
        data = module.mutable_data()
        dns_section = data.segments[0].dns
        assert isinstance(dns_section, SegmentDns)
        provider = dns_section.primary_provider
        assert isinstance(provider, DnsPrimaryProvider)
        provider.service = UUID(DNS_A)
        resolvers = await table_registry(
            NetworkServiceIdentity("dns-a", UUID(DNS_A), 701, "dns")
        )
        client = VcoClient("test.velocloud.net", "test-token")
        plans: list[ModulePlan] = []

        async def write_module(
            module: ConfigurationModuleBase, plan: ModulePlan, **_: object
        ) -> ModuleWrite[ConfigurationModuleBase]:
            plans.append(plan)
            return ModuleWrite(plan.name, plan.operation, module, plan)

        client.write_module = write_module
        _ = await client.update_module(module, data, resolvers=resolvers)
        (plan,) = plans
        (change,) = plan.ref_changes
        assert (change.added, change.row["enterpriseObjectId"]) == (True, 701)
        assert plan.refs is not None
        assert plan.refs["deviceSettings:dns:primaryProvider"] == [change.row]

    async def test_profile_argument_reconciles_segments(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        seg = {
            "segment": {"segmentId": 2, "name": "Guest"},
            "outbound": [
                {"name": "p", "match": {}, "action": {"allow_or_deny": "allow"}}
            ],
        }
        edge = self._fw({"segments": []}, layer=Edge)
        client, store = await self._make_client(mock_vco_client, edge)
        data = edge.mutable_data().for_edge()
        data.firewall_enabled.set_local(False)
        _ = await client.update_module(
            edge,
            data,
            profile=self._fw({"segments": [seg]}, layer=Profile, module_id=2),
        )
        assert self._updates(store)[0].update.data["segments"] == [
            {"segment": {"segmentId": 2, "name": "Guest"}, "outbound": []}
        ]

    async def test_new_module_is_rejected(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        mod = new_edge_module(ConfigurationModuleName.FIREWALL, 10)
        client, _ = await self._make_client(mock_vco_client, mod)
        with pytest.raises(ValueError, match="insert"):
            _ = await client.update_module(mod, mod.data)

    async def test_unsupported_type_raises(
        self, mock_vco_client: MockVcoClientFactory
    ) -> None:
        """update_module should raise TypeError for unsupported data types."""
        client, _store = await self._make_client(mock_vco_client)
        module = self._make_module()

        with pytest.raises(TypeError, match="Unsupported data type"):
            _ = await client.update_module(
                module,
                cast(
                    "DeviceSettingsData | QosData[Edge] | WanData | FirewallData[Edge]",
                    object(),
                ),
            )


class TestDeleteEdgeErrors:
    async def test_raises_on_error_field(self) -> None:
        """delete_edge should raise when result.error is set."""
        client = VcoClient("test.velocloud.net", "test-token")

        error_result = RowsModifiedConfirmation.model_validate(
            {"error": "edge not found", "rows": 0}
        )
        client.portal = AsyncMock(return_value=error_result)

        with pytest.raises(VcoOperationError, match="delete edge"):
            await client.delete_edge(AsyncMock())

    async def test_success_returns_none(self) -> None:
        """delete_edge should return None on success."""
        client = VcoClient("test.velocloud.net", "test-token")

        success_result = RowsModifiedConfirmation.model_validate({"rows": 1})
        client.portal = AsyncMock(return_value=success_result)

        result = await client.delete_edge(AsyncMock())
        assert result is None
