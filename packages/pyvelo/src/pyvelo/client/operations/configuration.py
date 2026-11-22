from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import MISSING

from pyvelo.client.exceptions import (
    ConfigConflictError,
    VcoOperationError,
    is_precondition_failure,
    is_version_conflict,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.module_write import plan_module, update_request
from pyvelo.client.operations._tagging import CONFIG, PM_FLOW, domain_tag
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    ConfigurationModuleBase,
    ConfigurationModuleList,
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
    InsertedConfigurationModule,
    UpdateConfigurationModule,
    UpdateConfigurationModuleResult,
)
from pyvelo.client.schemas.profiles import (
    CreateProfile,
    CreateProfileResult,
)
from pyvelo.core import ConfigContext, RowsModifiedConfirmation

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


def _level_context(level: ConfigContext | None) -> dict[str, ConfigContext] | None:
    """The validation context that puts a returned module at config level *level*."""
    return None if level is None else {"config_level": level}


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
        self: VcoClientBaseProtocol,
        request: InsertConfigurationModule,
        *,
        level: ConfigContext | None = None,
    ) -> InsertConfigurationModuleResult | AnyConfigurationModule:
        """Insert a new configuration module into a profile or edge-specific configuration.

        With ``return_data`` the result is the module row as stored, validated
        as a module at config level *level*; otherwise, or from a VCO that
        ignores ``returnData``, it is the new module's ID.
        """
        result: InsertConfigurationModuleResult | AnyConfigurationModule
        if request.return_data is True:
            answer = await self.portal(
                "configuration/insertConfigurationModule",
                request,
                InsertedConfigurationModule,
                validation_context=_level_context(level),
            )
            result = answer.root
        else:
            result = await self.portal(
                "configuration/insertConfigurationModule",
                request,
                InsertConfigurationModuleResult,
            )
        if isinstance(result, InsertConfigurationModuleResult) and isinstance(
            result.error, str
        ):
            raise VcoOperationError("insert configuration module", result.error)
        return result

    @domain_tag(CONFIG)
    async def update_configuration_module(
        self: VcoClientBaseProtocol,
        request: UpdateConfigurationModule,
        *,
        level: ConfigContext | None = None,
    ) -> AnyConfigurationModule | None:
        """Update an existing configuration module's data and, optionally, refs.

        Raises ``ConfigConflictError`` if the request carries a ``version`` that
        is not the module's current one and the VCO says so: a VCO with its
        conditional check on refuses it with a precondition error, also when
        the request asks for data, and one with the check off answers that no
        rows were modified. A request without a version is not checked.

        With ``return_data`` the result is the module row as stored, validated
        as a module at config level *level*, with its raw data and refs
        captured as on any read; a row that does not validate raises as a read
        does. At this level a stale ``version`` cannot be told from a write: a
        VCO with the check off answers both with a row, the stale one with the
        current row unchanged. ``write_module`` compares the row with what was
        sent. Without ``return_data``, or from a VCO that ignores it and
        answers with a row count, the result is None.
        """
        version = request.version
        result: AnyConfigurationModule | RowsModifiedConfirmation
        try:
            if request.return_data is True:
                answer = await self.portal(
                    "configuration/updateConfigurationModule",
                    request,
                    UpdateConfigurationModuleResult,
                    validation_context=_level_context(level),
                )
                result = answer.root
            else:
                result = await self.portal(
                    "configuration/updateConfigurationModule",
                    request,
                    RowsModifiedConfirmation,
                )
        except VcoOperationError as error:
            if is_precondition_failure(error):
                raise ConfigConflictError(
                    request.configuration_module_id,
                    "" if version is MISSING else version,
                    "the VCO refused the version",
                ) from error
            raise
        if not isinstance(result, RowsModifiedConfirmation):
            return result
        if version is not MISSING and is_version_conflict(
            error=result.error, rows=result.rows
        ):
            raise ConfigConflictError(
                request.configuration_module_id, version, "no rows were modified"
            )
        if result.error:
            raise VcoOperationError("update configuration module", result.error)
        if result.rows is None:
            raise VcoOperationError(
                "update configuration module",
                "the result is neither a module row nor a row count",
            )
        return None

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
        _ = await self.update_configuration_module(
            update_request(module, plan, enterprise_id)
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
