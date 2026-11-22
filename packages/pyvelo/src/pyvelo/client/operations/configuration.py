from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import MISSING

from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import (
    ConfigConflictError,
    ModuleWriteError,
    VcoOperationError,
    is_precondition_failure,
    is_version_conflict,
)
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.module_check import (
    ModuleWrite,
    check_module_write,
    check_refs,
    data_model,
)
from pyvelo.client.module_write import (
    ModulePlan,
    insert_request,
    plan_module,
    update_request,
)
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
from pyvelo.core.rebase import raw_changes
from pyvelo.core.response_check import PlannedChange

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


def _level_context(level: ConfigContext | None) -> dict[str, ConfigContext] | None:
    """The validation context that puts a returned module at config level *level*."""
    return None if level is None else {"config_level": level}


async def _read_stored(
    client: VcoClientBaseProtocol,
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    enterprise_id: int | None,
    *,
    refused: bool,
    module_id: int | None = None,
) -> AnyConfigurationModule:
    """*module* as the VCO holds it, read from its configuration after a write that returned no row.

    It is the module with *module_id*, by default the ID of *module* (for an
    insert, the ID the VCO answered), put at the config level of *module*
    when it has one. *refused* says the VCO refused the write's version.
    Raises ``ConfigConflictError`` for that ID, with no module and every
    planned change lost, when *module* has no ``configuration_id`` or its
    configuration does not hold the module.
    """
    wanted = module.id_ if module_id is None else module_id
    answer = (
        "the VCO refused the version" if refused else "the VCO returned no module row"
    )
    configuration_id = module.configuration_id
    if configuration_id is None:
        detail = f"{answer}, and the module has no configuration ID to read it by"
        raise _not_read(module, plan, detail, module_id=wanted)
    config = await client.get_configuration(
        GetConfiguration(
            id_=configuration_id,
            enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
            with_=[GetConfigurationWith.MODULES],
        )
    )
    held = config.modules if config.modules is not MISSING else []
    found = next((m for m in held if m.id_ == wanted), None)
    if found is None:
        detail = f"{answer}, and its configuration does not hold the module"
        raise _not_read(module, plan, detail, module_id=wanted)
    level = module.config_context
    return found if level is None else found.for_context(level)


def _not_read(
    module: ConfigurationModuleBase, plan: ModulePlan, detail: str, *, module_id: int
) -> ConfigConflictError:
    """The error for a write whose module could not be read back: every planned change lost, nothing stored.

    The lost edits are each change of the data and, when the plan sends refs,
    each ref row it adds.
    """
    as_read = module.raw_data
    changes = raw_changes(
        {} if as_read is MISSING else as_read, plan.data, data_model(module)
    )
    lost_edits = [
        PlannedChange(change.path, change.before, change.after, MISSING)
        for change in changes
    ]
    if plan.refs is not None:
        lost_edits.extend(check_refs(module.raw_refs, plan.refs, MISSING).not_applied)
    return ConfigConflictError(
        module_id,
        module.version or "",
        detail,
        module_name=plan.name,
        plan=plan,
        module=None,
        lost_edits=lost_edits,
    )


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

    async def write_module[M: ConfigurationModuleBase](
        self: VcoClientBaseProtocol,
        module: M,
        plan: ModulePlan,
        *,
        enterprise_id: int | None = None,
    ) -> ModuleWrite[M]:
        """Send *plan*, made by ``plan_module`` for *module*, and check what the VCO stored.

        An update or an insert, as the plan says, at the config level of
        *module*, asking for the module row as stored. When the VCO answers
        without one (it refused the version, or it ignores ``returnData``), the
        module is read back from its configuration; an error of that read
        propagates. Sends nothing when the plan's operation is ``none``, and
        returns *module* itself.

        Returns a ``ModuleWrite`` with the module as stored, of the class of
        *module* and at its config level when it has one, when every planned
        change is stored. Otherwise raises ``ConfigConflictError`` or
        ``WriteNotAppliedError`` (``check_module_write``), with the module as
        stored and the lost edits; a ``ConfigConflictError`` with no module
        when the read does not find it; ``VcoOperationError`` when the VCO
        holds it as another class of module. After a refusal the error's
        cause is the VCO's refusal.
        """
        if plan.operation == "none":
            return ModuleWrite(plan.name, "none", module, plan)
        level = module.config_context
        refusal: ConfigConflictError | None = None
        stored: ConfigurationModuleBase | None
        if plan.operation == "insert":
            inserted = await self.insert_configuration_module(
                insert_request(module, plan, enterprise_id), level=level
            )
            if isinstance(inserted, InsertConfigurationModuleResult):
                stored = await _read_stored(
                    self,
                    module,
                    plan,
                    enterprise_id,
                    refused=False,
                    module_id=inserted.id_,
                )
            else:
                stored = inserted
        else:
            request = update_request(module, plan, enterprise_id)
            try:
                stored = await self.update_configuration_module(request, level=level)
            except ConfigConflictError as error:
                # The VCO stated the refusal (its precondition error, or no rows
                # modified) and returned no row. Read in this block, so that an
                # error of the read has the refusal as its context.
                refusal = error
                stored = await _read_stored(
                    self, module, plan, enterprise_id, refused=True
                )
            if stored is None:
                stored = await _read_stored(
                    self, module, plan, enterprise_id, refused=False
                )
        if not isinstance(stored, type(module)):
            held, sent = type(stored).__name__, type(module).__name__
            raise VcoOperationError(
                "write configuration module",
                f"the VCO holds module {stored.id_} as a {held}, not a {sent}",
            )
        try:
            return check_module_write(module, plan, stored, refused=refusal is not None)
        except ModuleWriteError as outcome:
            if refusal is None:
                raise
            # The refusal, and the VCO's own error under it, stay in the chain.
            raise outcome from refusal

    @domain_tag(CONFIG)
    async def update_module[M: ConfigurationModuleBase](
        self: VcoClientBaseProtocol,
        module: M,
        data: DeviceSettingsData | WanData | QosData | FirewallData,
        *,
        enterprise_id: int | None = None,
        refs: DeviceSettingsRefs | dict[str, Any] | None = None,
        profile: ConfigurationModuleBase | None = None,
    ) -> M:
        """Write *data* to *module*: its raw data as read, plus the caller's changes.

        Returns the module as stored (``write_module``), of the class of
        *module*; that is *module* itself when *data* (and *refs*) change
        nothing, and nothing is sent. Give the *profile*'s module of the same
        name to reconcile the segments an edge module lacks.

        Raises ``ConfigConflictError`` if the module changed since it was
        read, and ``WriteNotAppliedError`` if the VCO stored the write
        otherwise than as sent. Each carries the module as stored, when it
        could be read, and the lost edits to redo on it. The rules that need
        more than this one module are not run here: they need a state
        (``ConfigState``, in pyvelo-intent).
        """
        if not isinstance(data, DeviceSettingsData | WanData | QosData | FirewallData):
            raise TypeError(f"Unsupported data type: {type(data).__name__}")
        if module.is_new:
            raise ValueError(
                "module has not been inserted yet; insert it instead of updating"
            )
        write = await self.write_module(
            module,
            plan_module(module, data, refs=refs, profile=profile),
            enterprise_id=enterprise_id,
        )
        return write.module

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
