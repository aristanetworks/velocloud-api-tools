from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import MISSING

from pyvelo.client.exceptions import VcoOperationError
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.operations._tagging import CONFIG, PM_FLOW, domain_tag
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    ConfigurationModuleBase,
    ConfigurationModuleList,
    DeleteConfiguration,
    DeviceSettingsModuleUpdate,
    DeviceSettingsRefs,
    FirewallModuleUpdate,
    GetAllApplicationsResult,
    GetApplicationsResult,
    GetConfiguration,
    GetConfigurationModules,
    GetIdentifiableApplications,
    GetRoutableApplications,
    InsertConfigurationModule,
    InsertConfigurationModuleResult,
    QosModuleUpdate,
    UpdateConfigurationModule,
    WanModuleUpdate,
)
from pyvelo.client.schemas.profiles import (
    CreateProfile,
    CreateProfileResult,
)
from pyvelo.core import RowsModifiedConfirmation

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


class ConfigurationMixin(Protocol):
    """Operations for configuration/ API endpoints."""

    @domain_tag(CONFIG)
    async def create_enterprise_profile(
        self: VcoClientBaseProtocol, request: CreateProfile
    ) -> CreateProfileResult:
        """Create a new enterprise configuration profile."""
        return await self.portal(
            "configuration/cloneEnterpriseTemplate", request, CreateProfileResult
        )

    @domain_tag(CONFIG)
    async def get_configuration(
        self: VcoClientBaseProtocol, request: GetConfiguration
    ) -> ConfigurationBase:
        """Retrieve a configuration by ID, optionally with its modules. The ID can be for an enterprise profile or for an edge configuration. Edge configuration IDs can be retrieved in the edge's configuration stack and subsequently passed to this tool."""
        result = await self.portal(
            "configuration/getConfiguration", request, ConfigurationBase
        )

        if result.name == "Edge Specific Profile":
            return result.for_edge()
        else:
            return result.for_profile()

    @domain_tag(CONFIG)
    async def delete_enterprise_profile(
        self: VcoClientBaseProtocol, request: DeleteConfiguration
    ) -> RowsModifiedConfirmation:
        """Delete a configuration/profile from the enterprise."""
        return await self.portal(
            "configuration/deleteConfiguration",
            request,
            RowsModifiedConfirmation,
        )

    @domain_tag(CONFIG)
    async def get_configuration_modules(
        self: VcoClientBaseProtocol, request: GetConfigurationModules
    ) -> Sequence[AnyConfigurationModule]:
        """Get configuration modules for a specific configuration.

        Returns typed configuration modules associated with the specified
        configuration. Each module is parsed through the discriminated
        ``AnyConfigurationModule`` union so callers receive strongly-typed
        data (``DeviceSettingsData``, ``QosData``, etc.).
        """
        result = await self.portal(
            "configuration/getConfigurationModules",
            request,
            ConfigurationModuleList,
        )
        return result.root

    @domain_tag(CONFIG)
    async def insert_configuration_module(
        self: VcoClientBaseProtocol, request: InsertConfigurationModule
    ) -> InsertConfigurationModuleResult:
        """Insert a new configuration module into a profile or edge-specific configuration."""
        result = await self.portal(
            "configuration/insertConfigurationModule",
            request,
            InsertConfigurationModuleResult,
        )
        if isinstance(result.error, str):
            raise VcoOperationError("insert configuration module", result.error)
        return result

    @domain_tag(CONFIG)
    async def update_configuration_module(
        self: VcoClientBaseProtocol, request: UpdateConfigurationModule
    ) -> None:
        """Update an existing configuration module's data and, optionally, refs."""
        result = await self.portal(
            "configuration/updateConfigurationModule",
            request,
            RowsModifiedConfirmation,
        )
        if result.error:
            raise VcoOperationError("update configuration module", result.error)

    @domain_tag(CONFIG)
    async def update_module(
        self: VcoClientBaseProtocol,
        module: ConfigurationModuleBase,
        data: DeviceSettingsData | WanData | QosData | FirewallData,
        *,
        enterprise_id: int | None = None,
        refs: DeviceSettingsRefs | dict[str, Any] | None = None,
    ) -> None:
        if isinstance(data, DeviceSettingsData):
            refs_value = (
                DeviceSettingsRefs.model_validate(refs) if refs is not None else MISSING
            )
            update = DeviceSettingsModuleUpdate(data=data, refs=refs_value)
        elif isinstance(data, QosData):
            qos_refs = refs if isinstance(refs, dict) else MISSING
            update = QosModuleUpdate(data=data, refs=qos_refs)
        elif isinstance(data, WanData):
            update = WanModuleUpdate(data=data)
        elif isinstance(data, FirewallData):
            update = FirewallModuleUpdate(data=data)
        else:
            raise TypeError(f"Unsupported data type: {type(data).__name__}")

        await self.update_configuration_module(
            UpdateConfigurationModule(
                configuration_module_id=module.id_,
                enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
                update=update,
            )
        )

    @domain_tag(CONFIG)
    async def get_identifiable_applications(
        self: VcoClientBaseProtocol, request: GetIdentifiableApplications
    ) -> GetApplicationsResult:
        """Get all applications identifiable through Deep Packet Inspection (DPI).

        Returns the complete application map of all applications that the VeloCloud DPI engine
        can identify in network traffic. The application map includes well-known SaaS applications
        (e.g., Office 365, Salesforce, Zoom), protocol-based applications, and custom-defined
        applications. Each entry includes the application name, category, and identification
        signatures. This map is used by business policies and QoS rules to steer and prioritize
        traffic by application. Useful for building application-aware policies and understanding
        what applications the SD-WAN can classify.

        Returns:
            Application map with application names, IDs, categories, and DPI signatures
        """
        return await self.portal(
            "configuration/getIdentifiableApplications",
            request,
            GetApplicationsResult,
        )

    @domain_tag(CONFIG)
    async def get_routable_applications(
        self: VcoClientBaseProtocol, request: GetRoutableApplications
    ) -> GetApplicationsResult:
        """Get all first-packet routable applications for an enterprise.

        Returns all applications that support first-packet routing, meaning the SD-WAN can
        identify and route traffic for these applications from the very first packet without
        waiting for DPI classification to complete. First-packet routing uses DNS-based or
        IP-based identification to immediately steer traffic to the correct path (direct internet,
        VPN, or gateway). This enables latency-sensitive applications to be correctly routed
        without the delay of full DPI analysis. Useful for understanding which applications
        benefit from immediate path selection.

        Returns:
            Array of first-packet routable application objects with application name, ID,
            and routing classification data
        """
        return await self.portal(
            "configuration/getRoutableApplications",
            request,
            GetApplicationsResult,
        )

    @domain_tag(CONFIG, PM_FLOW)
    async def get_applications(
        self: VcoClientBaseProtocol, request: GetRoutableApplications
    ) -> GetAllApplicationsResult:
        """Get all applications, both DPI and routable.

        Returns:
            Object with separate sections for routable and all application details
        """

        return await self.portal(
            "configuration/getApplications",
            request,
            GetAllApplicationsResult,
        )
