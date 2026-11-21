from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import MISSING

from pyvelo.client.exceptions import (
    ConfigConflictError,
    VcoApiError,
    VcoOperationError,
    is_version_conflict,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.module_write import plan_module
from pyvelo.client.operations._tagging import CONFIG, PM_FLOW, domain_tag
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    ConfigurationModuleBase,
    ConfigurationModuleList,
    ConfigurationModuleUpdate,
    DeleteConfiguration,
    DeviceSettingsRefs,
    GetAllApplicationsResult,
    GetApplicationsResult,
    GetConfiguration,
    GetConfigurationModules,
    GetIdentifiableApplications,
    GetRoutableApplications,
    InsertConfigurationModule,
    InsertConfigurationModuleResult,
    UpdateConfigurationModule,
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
        """Update an existing configuration module's data and, optionally, refs.

        Raises ``ConfigConflictError`` if the request carries a ``version`` and
        the response says that version is stale.
        """
        version = request.version
        try:
            result = await self.portal(
                "configuration/updateConfigurationModule",
                request,
                RowsModifiedConfirmation,
            )
        except (VcoApiError, VcoOperationError) as exc:
            # ``portal`` reports a JSON-RPC error as a VcoOperationError raised
            # from the VcoApiError.
            api_error = exc if isinstance(exc, VcoApiError) else exc.__cause__
            if (
                version is not MISSING
                and isinstance(api_error, VcoApiError)
                and is_version_conflict(error=api_error.message, rows=None)
            ):
                raise ConfigConflictError(
                    request.configuration_module_id, version, api_error.message
                ) from exc
            raise
        if version is not MISSING and is_version_conflict(
            error=result.error, rows=result.rows
        ):
            raise ConfigConflictError(
                request.configuration_module_id,
                version,
                result.error or "no rows were modified",
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
        profile: ConfigurationModuleBase | None = None,
    ) -> None:
        """Write *data* to *module*: its raw data as read, plus the caller's changes.

        Sends nothing if *data* (and *refs*) change nothing. Give the *profile*'s
        module of the same name to reconcile the segments an edge module lacks.
        Raises ``ConfigConflictError`` if the module changed since it was read.
        """
        if not isinstance(data, DeviceSettingsData | WanData | QosData | FirewallData):
            raise TypeError(f"Unsupported data type: {type(data).__name__}")
        if module.is_new:
            raise ValueError(
                "module has not been inserted yet; insert it instead of updating"
            )
        plan = plan_module(module, data, refs=refs, profile=profile)
        if plan.operation == "none":
            return
        await self.update_configuration_module(
            UpdateConfigurationModule(
                configuration_module_id=module.id_,
                enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
                version=module.version or MISSING,
                update=ConfigurationModuleUpdate(
                    data=plan.data,
                    refs=plan.refs if plan.refs is not None else MISSING,
                ),
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
