from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import MISSING, ValidationError

from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    ModuleWriteError,
    VcoHttpError,
    VcoOperationError,
    WriteAnswer,
    WriteOutcomeUnknownError,
    is_precondition_answer,
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
    has_checkable_change,
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
    ConfigurationModuleVersion,
    DeleteConfiguration,
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
from pyvelo.core import ConfigContext, RowsModifiedConfirmation, VcoModel
from pyvelo.core.base import IdentityLookup

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol


def _level_context(
    level: ConfigContext | None, *, refs_loaded: bool = True
) -> dict[str, object] | None:
    """The validation context that puts a returned module at config level *level*.

    *refs_loaded* is false for an answer that does not promise the module's refs.
    """
    context: dict[str, object] = {}
    if level is not None:
        context["config_level"] = level
    if not refs_loaded:
        context["refs_loaded"] = False
    return context or None


class _ConfigurationRows(VcoModel):
    """A configuration read with its modules, each module row left as the JSON object it came as."""

    modules: list[dict[str, Any]] | MISSING = MISSING


def _unknown_outcome(
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    detail: str,
    *,
    answer: WriteAnswer,
    module_id: int,
) -> WriteOutcomeUnknownError:
    """The error for a write that was sent, or was about to be sent again, and whose module as stored could not be read."""
    return WriteOutcomeUnknownError(
        module_id,
        module.version or "",
        detail,
        answer=answer,
        module_name=plan.name,
        plan=plan,
    )


async def _read_rows(
    client: VcoClientBaseProtocol, configuration_id: int, enterprise_id: int | None
) -> list[dict[str, Any]]:
    """The module rows of configuration *configuration_id*, each left as the JSON object it came as.

    Raises ``ValidationError`` when what the VCO answers is not a
    configuration; an error of the request itself propagates.
    """
    config = await client.portal(
        "configuration/getConfiguration",
        GetConfiguration(
            id_=configuration_id,
            enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
            with_=[GetConfigurationWith.MODULES],
        ),
        _ConfigurationRows,
    )
    return config.modules if config.modules is not MISSING else []


def _parse_row(
    module: ConfigurationModuleBase, row: dict[str, Any]
) -> AnyConfigurationModule:
    """The module row *row* as a module at the config level of *module*, or at none when it has none.

    Raises ``ValidationError`` when the row does not parse.
    """
    return ConfigurationModuleList.model_validate_json(
        json.dumps([row]), context=_level_context(module.config_context)
    ).root[0]


async def _read_version(
    client: VcoClientBaseProtocol,
    module: ConfigurationModuleBase,
    enterprise_id: int | None,
) -> str | None:
    """The ``version`` the VCO holds for *module* now: the row with its ID, read without its data; None when no row has it.

    One request: ``configuration/getConfigurationModules`` with ``noData``,
    filtered to the module's name. Raises ``ValueError`` when *module* has no
    ``configuration_id``. Any error of the request propagates.
    """
    configuration_id = module.configuration_id
    if configuration_id is None:
        raise ValueError("the module has no configuration ID to read its version by")
    rows = await client.portal_list(
        "configuration/getConfigurationModules",
        GetConfigurationModules(
            enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
            configuration_id=configuration_id,
            no_data=True,
            modules=[ConfigurationModuleName(str(getattr(module, "name", MISSING)))],
        ),
        ConfigurationModuleVersion,
    )
    return next((row.version for row in rows if row.id_ == module.id_), None)


async def _check_stale[M: ConfigurationModuleBase](
    client: VcoClientBaseProtocol,
    module: M,
    plan: ModulePlan,
    enterprise_id: int | None,
) -> ModuleWrite[M]:
    """The outcome of *plan* for a module whose version on the VCO is not the one read, with nothing sent.

    The module is read with its refs, as the VCO holds it now. It is returned
    as the write when it already holds the plan's changes (a retry of a write
    whose answer was lost); otherwise this raises ``ConfigConflictError``
    (``check_module_write`` with ``"stale"``), and for a module its
    configuration no longer holds, one with no module. An error of the read, or
    a read that does not parse, propagates.
    """
    configuration_id = module.configuration_id
    assert configuration_id is not None
    held = await _read_rows(client, configuration_id, enterprise_id)
    row = next((r for r in held if r.get("id") == module.id_), None)
    if row is None:
        raise ConfigConflictError(
            module.id_,
            module.version or "",
            (
                "its configuration no longer holds the module, and nothing was "
                "sent: read the configuration again to see what is there"
            ),
            stated=False,
            sent=False,
            module_name=plan.name,
            plan=plan,
        )
    return check_module_write(module, plan, _parse_row(module, row), answer="stale")


async def _read_stored(
    client: VcoClientBaseProtocol,
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    enterprise_id: int | None,
    *,
    answer: WriteAnswer,
    module_id: int | None = None,
) -> AnyConfigurationModule:
    """*module* as the VCO holds it, read from its configuration after a write that returned no row.

    It is the module with *module_id*, by default the ID of *module* (for an
    insert, the ID the VCO answered). Only that module's row is parsed, as an
    update's returned row is: at the config level of *module*, or at none
    when it has none. *answer* is what the VCO said of the write. Raises
    ``WriteOutcomeUnknownError`` for that ID when *module* has no
    ``configuration_id``, the configuration read does not parse, it does not
    hold the module, or the row does not parse. An error of the read request
    itself propagates.
    """
    wanted = module.id_ if module_id is None else module_id
    configuration_id = module.configuration_id
    if configuration_id is None:
        raise _unknown_outcome(
            module,
            plan,
            "the module has no configuration ID to read it by",
            answer=answer,
            module_id=wanted,
        )
    try:
        held = await _read_rows(client, configuration_id, enterprise_id)
    except ValidationError as error:
        raise _unknown_outcome(
            module,
            plan,
            "the configuration read back does not parse",
            answer=answer,
            module_id=wanted,
        ) from error
    row = next((r for r in held if r.get("id") == wanted), None)
    if row is None:
        raise _unknown_outcome(
            module,
            plan,
            "its configuration does not hold the module",
            answer=answer,
            module_id=wanted,
        )
    try:
        return _parse_row(module, row)
    except ValidationError as error:
        raise _unknown_outcome(
            module,
            plan,
            "the module row read back does not parse",
            answer=answer,
            module_id=wanted,
        ) from error


async def _read_named(
    client: VcoClientBaseProtocol,
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    enterprise_id: int | None,
) -> AnyConfigurationModule | None:
    """The module of the name of *plan* that the configuration of *module* already holds, or None.

    Of several rows of that name it is the one with the highest ID, the last
    inserted. Only that row is parsed, as ``_read_stored`` parses its row.
    None when *module* has no ``configuration_id`` or the configuration holds
    no row of the name. Raises ``WriteOutcomeUnknownError`` when the
    configuration read does not parse or the row found does not. An error of
    the read request itself propagates.
    """
    configuration_id = module.configuration_id
    if configuration_id is None:
        return None
    try:
        held = await _read_rows(client, configuration_id, enterprise_id)
    except ValidationError as error:
        raise _unknown_outcome(
            module,
            plan,
            (
                "the configuration read before sending the insert again does not "
                "parse, and an earlier insert of this module may be stored"
            ),
            answer="row",
            module_id=module.id_,
        ) from error
    row: dict[str, Any] | None = None
    row_id = -1
    for candidate in held:
        candidate_id = candidate.get("id")
        if (
            candidate.get("name") == plan.name
            and isinstance(candidate_id, int)
            and candidate_id > row_id
        ):
            row, row_id = candidate, candidate_id
    if row is None:
        return None
    try:
        return _parse_row(module, row)
    except ValidationError as error:
        raise WriteOutcomeUnknownError(
            row_id,
            "",
            "the configuration holds a module of this name whose row does not parse",
            answer="row",
            module_name=plan.name,
            plan=plan,
        ) from error


def _not_this_module(
    module: ConfigurationModuleBase, plan: ModulePlan, stored: ConfigurationModuleBase
) -> str | None:
    """Why *stored* is not the module *module* was written as, or None when it is.

    It is not of the class of *module*; for an update it has another ID; or
    it is of another configuration than *module*'s, when *module* has one.
    """
    if not isinstance(stored, type(module)):
        held, sent = type(stored).__name__, type(module).__name__
        return f"the VCO holds module {stored.id_} as a {held}, not a {sent}"
    if plan.operation == "update" and stored.id_ != module.id_:
        return f"the VCO answered with module {stored.id_}, not module {module.id_}"
    if (
        module.configuration_id is not None
        and stored.configuration_id != module.configuration_id
    ):
        return (
            f"the VCO answered with a module of configuration "
            f"{stored.configuration_id}, not {module.configuration_id}"
        )
    return None


def _require_this_module(
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    stored: ConfigurationModuleBase,
    *,
    answer: WriteAnswer,
) -> ConfigurationModuleBase:
    """*stored*, when it is the module *module* was written as; otherwise raises ``WriteOutcomeUnknownError``."""
    mismatch = _not_this_module(module, plan, stored)
    if mismatch is not None:
        raise _unknown_outcome(
            module,
            plan,
            mismatch,
            answer=answer,
            module_id=stored.id_ if plan.operation == "insert" else module.id_,
        )
    return stored


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
            validation_context=_level_context(None, refs_loaded=False),
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
                validation_context=_level_context(level, refs_loaded=False),
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
        the request asks for data, whatever HTTP status it comes with, and one
        with the check off answers that no rows were modified. A request
        without a version is not checked.

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
        except VcoHttpError as error:
            if is_precondition_answer(error.text):
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
        if_absent: bool = False,
    ) -> ModuleWrite[M]:
        """Send *plan*, made by ``plan_module`` for *module*, and check what the VCO stored.

        An update or an insert, as the plan says, at the config level of
        *module*, asking for the module row as stored. When the VCO answers
        without one (it refused the version, or it ignores ``returnData``), the
        module is read back from its configuration, and only that module's
        row is parsed; an error of that read request propagates. An update
        with no change the check can find stored (``has_checkable_change``:
        every change is to a value the VCO stores its own way) is sent
        without ``returnData`` and read back. A VCO with its conditional
        check off answers a stale update that asked for the row with the row
        as it is, so it looks stored; asked for no row, it answers a refusal
        with a row count of zero.

        Sends nothing when the plan's operation is ``none``, and returns
        *module* itself.

        An update whose plan carries refs (``plan.refs`` is not None; a
        module with a ``version`` and a ``configuration_id``) first reads the
        module's ``version`` (``_read_version``, one request). With the VCO's
        precheck off, a stale write is refused for the data but still replaces
        the refs, so a version that is not the one read is not sent over. The
        configuration is read with its modules, and the module there is
        checked as after a refusal (``check_module_write`` with
        ``"stale"``): it is returned as the write when it already holds the
        changes (a retry whose first copy was stored), and otherwise
        ``ConfigConflictError`` is raised with ``sent`` False. Neither read
        sends anything, and an error of either propagates. A write that sends
        no refs has no extra request. The read narrows the race and does not
        close it.

        With *if_absent*, an insert first reads the configuration: a module of
        the plan's name found there (of several, the one with the highest ID)
        is taken as the insert's answer and checked like one, and nothing is
        inserted. Use it to repeat an insert whose answer was not read. An
        error of that read request itself propagates, as it does for the
        read-back.

        A row the VCO answers with that has no version is replaced by the
        module read from its configuration, so that the module returned
        makes the next write conditional. When the module read back has no
        version either, it is returned as it is: the VCO holds none for it.

        Returns a ``ModuleWrite`` with the module as stored, of the class of
        *module* and at its config level (at none when it has none), whichever
        way the VCO answered, when every planned change is stored. Otherwise
        raises one of:

        - ``ConfigConflictError`` when the VCO refused the write's version, or
          answered with the module as read, unchanged; or, with nothing sent,
          when the version read before a write that sends refs found the
          module changed and not holding the changes. Also with nothing sent,
          and with no module, when the module is gone from its configuration.
        - ``WriteNotAppliedError`` when the VCO stored the write otherwise than
          as planned. ``RefsDroppedError``, a subclass, when every change not
          stored is a ref row. An update the VCO answered with the module
          needs a change that is stored beside: a write of ref rows alone
          that stores none is a conflict.
        - ``WriteOutcomeUnknownError`` when the write was sent, or was about
          to be sent again, and the module as stored cannot be read: the
          answer or the row read back does not parse, the read-back does not
          find the module, or the module the VCO answered with is not this
          one (another ID, another configuration or another class of
          module). With *if_absent* it also says that the module found by
          name does not parse or is not this one.

        The first two carry the lost edits and the module as stored, except a
        conflict about a module gone from its configuration, which has no
        module; ``WriteOutcomeUnknownError`` carries neither.
        ``error.redo_patch()`` is the patch that redoes the lost edits on that
        module, and ``error.not_redone()`` the lost edits it leaves out.

        After a refusal, the refusal is the error's cause (its context, when
        the read-back fails). Raises ``ValueError``, before sending, when *plan*
        is for a module of another name.
        """
        # The name as plan_module takes it: the base class declares none.
        module_name = str(getattr(module, "name", MISSING))
        if plan.name != module_name:
            raise ValueError(f"the plan is for {plan.name}, not {module_name}")
        if plan.operation == "none":
            return ModuleWrite(plan.name, "none", module, plan)
        level = module.config_context
        refusal: ConfigConflictError | None = None
        answer: WriteAnswer = "row"
        stored: ConfigurationModuleBase | None
        if plan.operation == "insert":
            insert = insert_request(module, plan, enterprise_id)
            found = (
                await _read_named(self, module, plan, enterprise_id)
                if if_absent
                else None
            )
            if found is not None:
                stored = found
            else:
                try:
                    inserted = await self.insert_configuration_module(
                        insert, level=level
                    )
                except ValidationError as error:
                    raise _unknown_outcome(
                        module,
                        plan,
                        "the VCO's answer to the insert does not parse",
                        answer="row",
                        module_id=module.id_,
                    ) from error
                if isinstance(inserted, InsertConfigurationModuleResult):
                    answer = "counted"
                    stored = await _read_stored(
                        self,
                        module,
                        plan,
                        enterprise_id,
                        answer=answer,
                        module_id=inserted.id_,
                    )
                else:
                    stored = inserted
        else:
            if (
                plan.refs is not None
                and module.version
                and module.configuration_id is not None
                and await _read_version(self, module, enterprise_id) != module.version
            ):
                return await _check_stale(self, module, plan, enterprise_id)
            update = update_request(
                module,
                plan,
                enterprise_id,
                return_data=has_checkable_change(module, plan),
            )
            try:
                stored = await self.update_configuration_module(update, level=level)
            except ConfigConflictError as error:
                # The VCO stated the refusal (its precondition error, or no rows
                # modified) and returned no row. Read in this block, so that an
                # error of the read has the refusal as its context.
                refusal = error
                answer = "refused"
                stored = await _read_stored(
                    self, module, plan, enterprise_id, answer=answer
                )
            except ValidationError as error:
                raise _unknown_outcome(
                    module,
                    plan,
                    "the VCO's answer to the update does not parse",
                    answer="row",
                    module_id=module.id_,
                ) from error
            if stored is None:
                answer = "counted"
                stored = await _read_stored(
                    self, module, plan, enterprise_id, answer=answer
                )
        stored = _require_this_module(module, plan, stored, answer=answer)
        if answer == "row" and not stored.version:
            # The next write of the module is conditional on this version.
            stored = await _read_stored(
                self, module, plan, enterprise_id, answer=answer, module_id=stored.id_
            )
            stored = _require_this_module(module, plan, stored, answer=answer)
        try:
            return check_module_write(module, plan, stored, answer=answer)
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
        profile: ConfigurationModuleBase | None = None,
        resolvers: IdentityLookup | None = None,
    ) -> M:
        """Write *data* to *module*: its raw data as read, plus the caller's changes.

        Returns the module as stored (``write_module``), of the class of
        *module*; that is *module* itself when *data* changes nothing, and
        nothing is sent. Give the *profile*'s module of the same name to
        reconcile the segments an edge module lacks.

        The refs are derived from *data*, not given (``plan_module``): an
        edit to an owner of a ref row adds or removes the row, and *resolvers*
        finds the ``enterpriseObjectId`` of an identity it adds. A device
        settings or QoS module with segments is always written with its
        refs. ``RefsNotLoadedError`` is raised for a module read without its
        refs when the edit needs them.

        The module passed in is not updated: it still holds the data and the
        ``version`` from before the write. It is marked as replaced (an
        assignment to it raises) when the write stored something, and when
        ``ConfigConflictError`` or ``WriteNotAppliedError`` read the module as
        stored. It is not marked when nothing changed (the call returns the
        module itself), after ``WriteOutcomeUnknownError``, or after an error
        before the VCO answered. Continue from the module returned
        (``module = await client.update_module(module, data)``); after
        ``ConfigConflictError`` or ``WriteNotAppliedError``, continue from
        ``error.module``. After ``WriteOutcomeUnknownError`` the module as
        stored could not be read and the write may be stored: read the
        configuration again and compare before writing this module again.
        ``write_module`` does not mark the module it is given.
        Only the module is returned: what the VCO stored its own way
        (``left_out``, ``server_changes``) is on the ``ModuleWrite`` that
        ``write_module`` returns.

        Raises ``DetachedObjectError`` if *module* was replaced by an earlier
        write. Raises ``ConfigConflictError`` if the module changed since it was
        read, and ``WriteNotAppliedError`` if the VCO stored the write
        otherwise than as sent. Each carries the module as stored and the lost
        edits to redo on it, except a ``ConfigConflictError`` for a module gone
        from its configuration, which has no module (``error.module`` is None,
        and nothing was sent). ``WriteOutcomeUnknownError`` says the write was
        sent and the module as stored could not be read. The rules that need
        more than this one module are not run here: they need a state
        (``ConfigState``, in pyvelo-intent).
        """
        if not isinstance(data, DeviceSettingsData | WanData | QosData | FirewallData):
            raise TypeError(f"Unsupported data type: {type(data).__name__}")
        if module.is_new:
            raise ValueError(
                "module has not been inserted yet; insert it instead of updating"
            )
        if module.is_detached:
            raise DetachedObjectError(
                f"Configuration module {module.id_} was replaced by an earlier "
                "write: it holds the data and the version from before that write. "
                "Use the module that write returned (or error.module, after a "
                "write error)."
            )
        try:
            write = await self.write_module(
                module,
                plan_module(module, data, profile=profile, resolvers=resolvers),
                enterprise_id=enterprise_id,
            )
        except ModuleWriteError as error:
            # Only a write error that read the module as stored replaced this one.
            if error.module is not None:
                _ = module.mark_detached()
            raise
        if write.module is not module:
            _ = module.mark_detached()
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
