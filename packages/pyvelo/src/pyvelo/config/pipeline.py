"""ConfigPipeline: the orchestration layer of a configuration workflow.

The workflow is load → apply → save, and revert.
"""

import contextlib
import json
from collections.abc import Collection, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol, TypeIs, cast, runtime_checkable

from pydantic import MISSING
from pydantic.fields import FieldInfo

from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import (
    ComposedModuleError,
    DetachedObjectError,
    ModuleWriteError,
    UnboundError,
)
from pyvelo.client.hub_services import missing_services
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.models.configuration.ref_owners import ref_only_values
from pyvelo.client.module_check import ModuleWrite
from pyvelo.client.module_write import ModulePlan, with_hub_rows
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    GetConfiguration,
    QosConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.client.schemas.edge import (
    GetEdgeConfigurationModules,
    GetEdgeConfigurationStack,
)
from pyvelo.client.schemas.enterprise import GetEnterpriseSecurityObjects
from pyvelo.config.composed_security import (
    SECURITY_OBJECT_TYPES,
    SecurityObjects,
    rules_naming_a_group,
)
from pyvelo.config.effective import (
    CONVERTED_MODULES,
    EFFECTIVE_IGNORES,
    EffectiveComparison,
    EffectiveDifference,
    compare_module,
    module_attribute,
)
from pyvelo.config.errors import SaveStepError, SaveValidationError
from pyvelo.config.hub_calls import (
    HubServices,
    PreparedStep,
    hub_rows,
    hubs_in_use,
    prepare_steps,
    read_services,
    report_hubs_in_use,
    send_step,
)
from pyvelo.config.hub_steps import HubStep
from pyvelo.config.module_names import (
    resolve_module_name as _resolve_module_name,
)
from pyvelo.config.profile_segments import (
    PROFILE_SEGMENT_MODULES,
    SEGMENTS,
    ProfileSegmentPatch,
    added_segment_name,
    apply_assignment,
    assign_on_trial,
    check_profile_segments,
    entry_path,
    holds_segment_ops,
    plan_assignment,
    prepare_profile_segments,
    segment_modules,
)
from pyvelo.config.resolver import Resolver, ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.state import (
    ConfigPlan,
    ConfigState,
    EffectiveView,
    SaveProgress,
    SaveResult,
    StepOutcome,
    ref_only_changes,
)
from pyvelo.config.validation import ValidationError as ValidatorError
from pyvelo.config.validation import ValidationResult, ValidatorRegistry
from pyvelo.config.validation_warning import ValidationWarning
from pyvelo.core import PatchError, effective_dump
from pyvelo.core.base import IdentityLookup, IdentityResolver, VcoModel, is_dict
from pyvelo.core.fields import get_resolvable
from pyvelo.core.identity import (
    EdgeName,
    HubIdentity,
    NetworkServiceIdentity,
    ProfileName,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
    SegmentName,
)
from pyvelo.core.layers import Edge, Effective, Layered, Profile
from pyvelo.core.merge import nest_patch, read_given_id, select_items
from pyvelo.core.patch import ItemOp, is_new, parse_item_op
from pyvelo.core.rebase import Path, raw_changes


@dataclass
class _StepRun:
    """What the role steps of a save did so far."""

    modules: tuple[str, ...]
    """The names of the modules the save writes."""
    done: list[ModuleWrite[Any]]
    """The writes stored so far; the save's own list."""
    sent: list[HubStep] = field(default_factory=list)
    in_place: list[HubStep] = field(default_factory=list)
    outcomes: list[StepOutcome] = field(default_factory=list)
    warnings: list[ValidationWarning] = field(default_factory=list)
    """The ``hub_in_use`` warnings.

    The edges whose QoS rules use a hub a step removes.
    """
    calling: bool = False
    """Whether a call was made, which may have reached the VCO."""
    services: HubServices | None = None
    """The hub services as read before the first step; None until then."""
    prepared: tuple[PreparedStep, ...] = ()
    """Every step with its decision (``prepare_steps``), made before any is sent."""

    @contextlib.contextmanager
    def guard(self) -> Generator[None]:
        """Raise an error of the steps with what they did so far.

        A ``SaveStepError`` propagates as it is. Any other ``Exception`` once
        a step was sent is a ``SaveStepError``; before that, it and any other
        error, a cancellation included, propagate as they are, with a
        ``SaveProgress`` attached once a call was made or a write was found
        stored.
        """
        try:
            yield
        except SaveStepError:
            raise
        except Exception as error:
            if self.sent:
                raise self.error(error) from error
            if self.calling or self.done:
                self.progress().attach(error)
            raise
        except BaseException as error:
            if self.calling or self.done:
                self.progress().attach(error)
            raise

    def progress(self) -> SaveProgress:
        """The progress of a save that stopped in its steps.

        The writes stored so far, and the modules not yet written.
        """
        stored = {write.name for write in self.done}
        return SaveProgress(
            completed=tuple(self.done),
            failed=None,
            pending=tuple(name for name in self.modules if name not in stored),
        )

    def error(self, error: Exception, *, step: HubStep | None = None) -> SaveStepError:
        """The ``SaveStepError`` for *error*, raised at *step*.

        *step* is None after the steps. The error has the progress attached.
        """
        raised = SaveStepError(
            f"{type(error).__name__}: {error}",
            step=step,
            steps_done=self.sent,
            steps_in_place=self.in_place,
            written=[write.name for write in self.done],
        )
        self.progress().attach(raised)
        return raised


def _leaves_a_hub_list(steps: Sequence[HubStep]) -> bool:
    """Whether a step removes an Edge hub from a Branch-to-Hub list.

    Such a step asks for the services' QoS usage.
    """
    return any(
        s.action == "disable" and s.role == "hub" and s.hub.kind == "edge"
        for s in steps
    )


def _hubs_in_use_warnings(
    steps: Sequence[HubStep],
    services: HubServices,
    module: DeviceSettingsConfigurationModule,
    on_hub_in_use: Literal["warn", "refuse"],
) -> list[ValidationWarning]:
    """The ``hub_in_use`` warnings for the hubs *steps* remove that edge QoS rules use.

    Or, with ``"refuse"``, ``HubListError`` for them.
    """
    return report_hubs_in_use(
        hubs_in_use(
            steps,
            services,
            configuration_id=_configuration_id(module),
            module_id=module.id_,
        ),
        refuse=on_hub_in_use == "refuse",
    )


def _profile_device_settings(state: ConfigState) -> DeviceSettingsConfigurationModule:
    """The device settings module of a profile state.

    It is the module the role steps are for.
    """
    module = state.module(ConfigurationModuleName.DEVICE_SETTINGS)
    if not isinstance(module, DeviceSettingsConfigurationModule):
        raise TypeError(
            f"the hub steps need the profile's device settings module, not "
            f"a {type(module).__name__}"
        )
    return module


def _configuration_id(module: DeviceSettingsConfigurationModule) -> int:
    """The profile's ID, which the role calls and the hub rows name."""
    if module.configuration_id is None:
        raise ValueError(
            "the profile's device settings module has no configuration ID, "
            "which the role calls need: load the profile again"
        )
    return module.configuration_id


type _WritableModule = (
    DeviceSettingsConfigurationModule
    | QosConfigurationModule[Edge]
    | QosConfigurationModule[Profile]
    | WanConfigurationModule
    | FirewallConfigurationModule[Edge]
    | FirewallConfigurationModule[Profile]
)

_WRITABLE_MODULES = (
    DeviceSettingsConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
    FirewallConfigurationModule,
)


@dataclass
class _Sending:
    """The state of one save that sends: what it writes and what it stored so far."""

    state: ConfigState
    result: SaveResult
    changed: list[ModulePlan]
    """The plan of each changed module, in the order they are written."""
    modules: list[_WritableModule]
    """The module of each plan in *changed*.

    Each write replaces only its own module, so these stay the state's.
    """
    dumps: list[object]
    """What each module holds as planned, to find an edit made while a write was out."""
    providers: list[dict[Path, object]]
    """Each module's ``ref_only_values`` as planned: a provider is no part of a dump."""
    done: list[ModuleWrite[Any]]
    """The writes stored so far."""
    device_settings_at: int
    """The position of the device settings plan in *changed*.

    ``len(changed)`` when the save does not write them. The modules before it
    are written before the role steps, and it and the modules after it after
    them.
    """
    failed: str | None = None
    """The name of the module being written, until its write is stored."""
    device_settings_stored: bool = False
    """Whether a profile's device settings write is stored.

    That write can change the modules after it.
    """
    stored_already: ModuleWrite[Any] | None = None
    """The device settings write the version read found stored (its answer was lost)."""
    run: _StepRun | None = None
    """What the role steps did, once they are decided; None for a save without."""

    def is_device_settings(self, mod: _WritableModule) -> bool:
        """Whether *mod* is a profile's device settings module."""
        return (
            self.state.target_type == "profile"
            and mod.name == ConfigurationModuleName.DEVICE_SETTINGS
        )

    def progress_of(self, error: BaseException) -> SaveProgress:
        """The progress of this save, attached to *error*.

        The writes stored, the module being written and the modules after it.
        """
        stored = {write.name for write in self.done}
        progress = SaveProgress(
            completed=tuple(self.done),
            failed=self.failed,
            pending=tuple(
                m.name
                for m in self.modules
                if m.name not in stored and m.name != self.failed
            ),
        )
        progress.attach(error)
        return progress

    def store_write(self, at: int, write: ModuleWrite[Any]) -> None:
        """Count *write* of the module at *at* as stored.

        Also put the module as stored in the state.

        Raises ``DetachedObjectError`` for an edit made while it was written.
        """
        mod = self.modules[at]
        insert = self.changed[at].operation == "insert"
        # Stored: it counts as written even if the state cannot take it back.
        self.done.append(write)
        self.failed = None
        if self.is_device_settings(mod):
            self.device_settings_stored = True
        paths = list(
            dict.fromkeys(
                change.path
                for change in raw_changes(self.dumps[at], _dump(mod), type(mod))
            )
        )
        # A provider is no part of a dump: compare it by itself.
        paths.extend(
            Path(("data", *path.parts))
            for path in ref_only_changes(self.providers[at], ref_only_values(mod.data))
        )
        try:
            self.state.replace_module(mod, write.module)
        except BaseException:
            # The insert is stored but the state still plans it: the
            # next save reads before it inserts again.
            if insert:
                self.state.unconfirmed_inserts.add(mod.name)
            raise
        name = ConfigurationModuleName(write.name)
        self.result.server_changes[name] = write.server_changes
        self.result.left_out[name] = write.left_out
        self.result.written = tuple(self.done)
        if paths:
            error = DetachedObjectError(
                f"{mod.name}: the module was edited while this save was writing "
                f"it, at: {', '.join(paths)}. The write is stored without those "
                "edits and the state holds the module as stored; take the objects "
                "from the state again and redo them. A path that is a placeholder "
                "a lookup added (segment(name), for a segment the edge only "
                "inherits) is no edit and needs no redo. To edit a state from "
                "another task while a save may run, use apply, or hold state.lock "
                "around the edit",
                paths,
            )
            note = _written_note(self.done)
            if later := self.modules[at + 1 :]:
                note += (
                    "; the save stopped here, so these are not written: "
                    + ", ".join(m.name for m in later)
                    + ". The next save writes them"
                )
            error.add_note(note)
            raise error

    def put_stored(self, mod: _WritableModule, error: ModuleWriteError) -> bool:
        """Put the module *error* carries in the state in place of *mod*.

        Say whether it did.

        The module is of the class of *mod* (``write_module`` checks it), or
        None when it could not be read: then the state keeps *mod*. It is
        also kept when the state no longer holds *mod*, so that the write
        error is not hidden behind the ValueError of ``replace_module``.
        """
        stored = error.module
        if isinstance(stored, type(mod)) and self.state.find_module(mod.name) is mod:
            self.state.replace_module(mod, cast("AnyStoredModule", stored))
            return True
        return False


@dataclass(frozen=True)
class _Edit:
    """One ``apply`` call's patch to a module, and what its trial and merge need."""

    state: ConfigState
    mod: _WritableModule
    data: VcoModel
    """The module's typed data, which the merge changes."""
    nested: dict[str, object]
    """The patch, nested under its address (``nest_patch``)."""
    edge: bool
    """Whether the target is an edge."""
    profile_rules: bool
    """Whether the profile's segment rules apply to the module."""
    inherited: frozenset[str]
    """On an edge, the segments the module only inherits; else empty."""
    unserved: frozenset[Path]
    """The QoS entries that lacked their hub service as read; else empty."""

    def patch_for(
        self, data: VcoModel, prepared: ProfileSegmentPatch | None
    ) -> Mapping[str, object]:
        """The patch to merge into *data*, the module's data or a copy of it.

        The prepared profile patch, else on an edge the patch with the edge's
        segment rules applied (``_prepare_segments``), else the nested patch.
        """
        if prepared is not None:
            return prepared.patch
        if self.edge:
            return _prepare_segments(
                data, self.nested, self.state.resolvers, self.inherited
            )
        return self.nested


class ConfigPipeline:
    """Manages configuration load, merge, resolve, validate, save."""

    def __init__(
        self,
        client: VcoClientProtocol | None,
        resolvers: ResolverRegistry | None = None,
        validator_registry: ValidatorRegistry | None = None,
    ) -> None:
        """A pipeline over *client*, with empty registries unless given ones.

        *client* may be None for work that sends no request; ``client`` then
        raises.
        """
        self._client: VcoClientProtocol | None = client
        self._resolvers: ResolverRegistry = resolvers or ResolverRegistry()
        self._validators: ValidatorRegistry = (
            ValidatorRegistry() if validator_registry is None else validator_registry
        )

    @property
    def client(self) -> VcoClientProtocol:
        """The VCO client used by this pipeline."""
        if self._client is None:
            raise RuntimeError(
                "A VCO client is required for network-backed pipeline operations"
            )
        return self._client

    @property
    def resolvers(self) -> ResolverRegistry:
        """The resolvers each state gets a copy of.

        Register a resolver here before loading; ``load`` and ``load_profile``
        give the new state an unloaded copy of each
        (``ResolverRegistry.unloaded``) and load its segment and application
        resolvers for the state's enterprise.
        """
        return self._resolvers

    @property
    def validators(self) -> ValidatorRegistry:
        """The validators that ``save`` and ``validate`` run.

        Register more with ``validators.register(...)``.
        """
        return self._validators

    async def load(
        self,
        edge: int | str,
        *,
        enterprise_id: int | None = None,
        validate: bool = False,
    ) -> ConfigState:
        """Fetch edge config + profile config (template).

        If edge is a string, resolves by name via EdgeResolver.
        Takes a snapshot of the loaded state for diff-on-save.
        """
        edge_id: int
        resolvers = self._resolvers.unloaded()

        if isinstance(edge, str):
            # Resolve edge name to ID
            ctx = ResolverContext(enterprise_id=enterprise_id)
            try:
                edge_resolver = resolvers.get_resolver(EdgeResolver)
            except LookupError:
                raise LookupError(
                    f"load({edge!r}) was given a name, and this pipeline has no edge "
                    "resolver to turn it into an ID. Build the pipeline with "
                    "build_default_pipeline(client), or register EdgeResolver(client) "
                    "on pipeline.resolvers, or pass the edge's numeric ID."
                ) from None
            await edge_resolver.load(ctx)

            identity = await resolvers.resolve(EdgeName(edge))
            edge_id = identity.id
        else:
            edge_id = edge

        # Fetch edge configuration stack: [edge_specific, profile]
        request = GetEdgeConfigurationStack(edge_id=edge_id)
        if enterprise_id is not None:
            request.enterprise_id = enterprise_id

        stack = await self.client.get_edge_configuration_stack(request)
        edge_config: EdgeConfiguration = stack[0]
        profile_config: EnterpriseConfigurationProfile = stack[1]

        # Set resolver context from loaded state
        resolver_context = ResolverContext(
            enterprise_id=enterprise_id,
            edge_id=edge_id,
            configuration_id=(
                edge_config.id_ if edge_config.id_ is not MISSING else None
            ),
        )
        await self._load_state_resolvers(resolvers, resolver_context)

        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=resolver_context,
            resolvers=resolvers,
        )
        state.take_snapshot()

        if validate:
            read_result = self._validators.run(state, phase="read")
            for err in read_result.errors:
                state.load_warnings.append(_validation_error_to_warning(err))
            for warn in read_result.warnings:
                state.load_warnings.append(_validation_error_to_warning(warn))

        return state

    async def load_profile(
        self,
        profile: int | str,
        *,
        enterprise_id: int | None = None,
    ) -> ConfigState:
        """Load a profile configuration directly (no edge/template merge).

        Raises ``ValueError`` for the ID of an edge-specific configuration: load
        the edge with ``load``.
        """
        profile_id: int
        resolvers = self._resolvers.unloaded()

        if isinstance(profile, str):
            # Resolve profile name to ID
            ctx = ResolverContext(enterprise_id=enterprise_id)
            try:
                profile_resolver = resolvers.get_resolver(ProfileResolver)
            except LookupError:
                raise LookupError(
                    f"load_profile({profile!r}) was given a name, and this pipeline "
                    "has no profile resolver to turn it into an ID. Build the "
                    "pipeline with build_default_pipeline(client), or register "
                    "ProfileResolver(client) on pipeline.resolvers, or pass the "
                    "profile's numeric ID."
                ) from None
            await profile_resolver.load(ctx)

            identity = await resolvers.resolve(ProfileName(profile))
            profile_id = identity.id
        else:
            profile_id = profile

        # Fetch the profile configuration with modules
        request = GetConfiguration(
            id_=profile_id,
            with_=[GetConfigurationWith.MODULES],
        )
        if enterprise_id is not None:
            request.enterprise_id = enterprise_id

        config = await self.client.get_configuration(request)

        if isinstance(config, EdgeConfiguration):
            raise ValueError(  # noqa: TRY004 (an edge configuration is a valid answer, which this call refuses)
                f"configuration {profile_id} is the edge-specific configuration "
                "of an edge, not a profile. Load that edge with load(), by its "
                "ID or name; to edit its profile, give load_profile the "
                "profile's ID"
            )

        profile_config = config

        resolver_context = ResolverContext(
            enterprise_id=enterprise_id,
            configuration_id=(
                profile_config.id_ if profile_config.id_ is not MISSING else None
            ),
        )
        await self._load_state_resolvers(resolvers, resolver_context)

        state = ConfigState(
            edge_config=None,
            profile_config=profile_config,
            target_type="profile",
            resolver_context=resolver_context,
            resolvers=resolvers,
        )
        state.take_snapshot()

        return state

    async def apply(
        self,
        state: ConfigState,
        *,
        module: str,
        patch: object,
        segment: str | None = None,
        path: str | None = None,
    ) -> ConfigState:
        """Apply *patch* to the data of *module* on the state's target.

        The patch goes at an address if one is given.

        The patch format is ``VcoModel.merge_patch``'s: values validated as
        on parse, unknown keys refused, list items named by selector and
        added, changed, deleted and moved by ``add``, a plain object,
        ``delete`` and ``move``.

        **Address.** An addressed call is the unaddressed call with *patch*
        nested under the address, and nothing else:

        - ``path="a.b"`` is ``patch={"a": {"b": patch}}``;
        - ``segment="S"`` is ``patch={"segments": {"S": patch}}``;
        - both are ``patch={"segments": {"S": {"a": {"b": patch}}}}``.

        With neither, *patch* must be a ``dict`` (``TypeError`` otherwise).
        With either, it is any JSON value; ``None`` means what ``null`` means
        at that place. *path* is one or more field names joined by dots, each
        by any name ``merge_patch`` accepts. Every part but the last must be
        a field that holds a single sub-model; *path* does not step into list
        items, which the patch names. With *segment*, *path* starts at the
        item class of the module's ``segments`` field, else at the module
        data's class. *segment* is a selector of the ``segments`` list like
        any other: the segment's name in any case (through the segment
        resolver) or its ``segmentId``. The address is checked against the
        model classes, not the instances (``nest_patch``), so an unset
        intermediate field is fine. A part that names no field, a part before
        the last that is not a single sub-model, an empty *path* or part, and
        *segment* for a module with no ``segments`` field (WAN, control
        plane) raise ``PatchError``.

        **Steps.**

        1. Locate the module on the target config. A ``firewall`` or ``QOS``
           module that an edge lacks starts as an empty layer (nothing is
           copied from the profile) and is inserted on save. A module a save
           does not write (control plane) raises ``PatchError``, with an empty
           path, before the patch is read.
        2. On a profile target, the ``segments`` of device settings, QoS and
           firewall have rules of their own, because a segment belongs to all
           three (``prepare_profile_segments``). They apply to a patch whose
           ``segments`` holds an ``add`` or a ``delete``, or is a list value,
           and need the three modules (``PatchError``, ``segment_rule``, for a
           profile that lacks one):

           - an ``add`` of a segment the module had no entry for as read
             assigns it, from any of the three modules and in any order, as
             ``assign_segment`` does: each module that lacks the segment now
             gets the template entry, then the body of the add is merged into
             this module's entry as a change (a ``_position`` moves it). The
             segment is named by the selector, or under ``"+"`` by
             ``segment.name`` of the body (an add under ``"+"`` without one
             raises ``segment_rule``), strictly by the segment resolver
             (``ResolutionError`` for a name the enterprise lacks, and
             ``PatchError``, ``invalid_value``, for a state with no segment
             resolver). A body whose ``segment`` is not the enterprise
             segment's raises ``segment_rule``;
           - an ``add`` of a segment the module had as read raises ``exists``;
             write a plain object to change it;
           - an item of a list value for a segment the module had no entry for
             as read assigns it the same way, and is merged into the template
             entry; the other items replace the module's as given;
           - a ``delete``, or a list value that leaves a segment out, is
             accepted for an orphan (a QoS or firewall segment the device
             settings lacked as read); any other removal, and a rewritten
             descriptor, raise ``segment_rule``
             (``check_profile_segments``). A change or a ``move`` of a segment
             is fine.

           The assignments are tried on copies of the three modules first
           (``assign_on_trial``) and made only when the patch passes, so an
           error leaves all three as they were. A patch of changes and moves
           alone goes through one module. ``merge_patch`` called directly is
           not restricted.
        3. Merge the patch into a copy of the module's typed data, then into
           the data itself. On an edge target each merge is preceded by the
           segment rules (``_prepare_segments``). The edge layer has an entry
           for only some of the segments it inherits from its profile:

           - a change of a segment the edge only inherits first adds its
             placeholder entry, which carries the profile's descriptor and
             IDs (an empty object changes nothing and adds none);
           - a ``_position`` (``{"_position": ...}`` (``move(...)``), or on a
             change) for a segment the edge module did not have when it was
             read raises ``PatchError``, and so does one given ``before`` or
             ``after`` such a segment: a save writes those segments after the
             edge's own, in the profile's order. A segment an earlier patch
             gave a placeholder entry is still one of them; one a save wrote
             an entry for is not;
           - an ``add`` of a segment the edge inherits raises ``PatchError``
             (write a plain object to change it), and so does an ``add``
             under ``"+"`` whose body names one: by ``segment.name``, or by
             an ID that names a segment (one the registered segment
             resolver has loaded, where a resolver that must be loaded
             first is loaded; with no resolver, a ``segmentId`` of a
             segment of the edge layer or the profile). An ID that names
             none is left to ``merge_patch``;
           - a ``delete`` of a segment the edge only inherits raises
             ``PatchError``: the layer has no entry to delete;
           - an edge entry the profile knows under another name (the same
             ``segmentId``) is that segment;
           - an ``add`` of a segment neither layer has raises ``PatchError``
             (assign it on the profile first); any other entry for one is left
             to ``merge_patch``, as is any entry on data not bound to a
             profile; naming one segment twice raises ``PatchError``.
        4. Plan the patched copy as a save would (:meth:`ConfigState.plan_module`).
           A patch that a save could not write raises ``LossyFieldError``: a
           field set to its default where the module as read has no key for it
           (on an edge, one it inherits), or an edit to a section this library
           does not write at the level of the target.
        5. Return the updated :class:`ConfigState`.

        A QoS patch that names a hub in a rule's ``backhaulEdges`` entry (on a
        profile or an edge) gives the entry its ``logicalId``, the hub's
        service for the profile and the rule's segment
        (:meth:`ConfigState.fill_hub_services`), on the copy and again on the
        module's data, so the state and its dump hold it. A hub with no
        service there (one that is not yet saved in the segment's lists)
        raises ``HubServiceNotFoundError`` and leaves the state as it was; an
        entry that lacked its service as read is left alone.

        A patch that gives a network service by name (a provider), or sets a
        provider, needs the enterprise's services loaded. The first such
        patch of a state reads them (:meth:`load_resolvers`), once, and goes
        on; a name that no service has raises ``ResolutionError`` and leaves
        the state as it was, as any other patch that raises does.

        **In place.** ``apply`` changes *state* and returns that same object;
        there is no copy to fall back on.

        All or nothing: a patch that raises (``PatchError``, including one for
        a module a save does not write, ``LossyFieldError`` for a patch that a
        save could not write, a resolver's ``ResolutionError`` or
        ``LookupError``, or ``DetachedObjectError`` for a module that holds an
        object a write replaced, put back as it is, which is refused whole
        before the patch is read, naming where the object sits) leaves the
        state, and ``plan()``, as they were: no placeholder entries, and a
        module this call created is removed.
        ``LossyFieldError`` is raised also when an earlier assignment to the
        models had already made the module unwritable; its paths name that
        field.

        **What apply does not check.** The rules that span modules are checked
        by ``ConfigState.plan()``, not here: the segments of a profile's three
        modules (``SegmentSetError``), a segment's hub lists (``HubListError``)
        and the references (``DanglingReferenceError``: a value that names a
        segment or a backhaul hub the configuration lacks). An edit that leaves
        one of them broken is accepted, so that two patches that are only right
        together go in either order: a hub out of a segment's lists, and out of
        the rule that uses it. ``plan()``, ``save`` and a dry run refuse the
        state until it is mended, by another patch
        (``DanglingReferenceError.mending`` holds them as data) or by taking
        the module's edits back (:meth:`revert`).

        MISSING fields on the patch target are preserved as MISSING so callers
        downstream (reconciler, YAML export) can distinguish "unset" from
        "explicitly set".

        Holds ``state.lock`` for the whole call, so it runs after a ``save`` or
        another ``apply`` of the same state that is running. Raises
        ``RuntimeError`` when the calling task already holds ``state.lock``.
        A task that holds ``state.lock`` and, inside the block, waits for a
        child task that calls ``apply`` or ``save`` waits without end; nothing
        detects it.

        Raises ``ValueError`` for an unknown *module*.
        """
        async with state.lock:
            mod, created = _patched_module(state, module, patch, segment, path)
            try:
                mod.data.check_nothing_detached("apply")
                edit = _edit_of(state, mod, patch, segment, path)
                prepared = await self._trial(edit)
            except BaseException:
                if created:
                    state.drop_module(mod)
                raise
            _merge(edit, prepared)
            return state

    async def _trial(self, edit: _Edit) -> ProfileSegmentPatch | None:
        """Try the patch of *edit* on copies until it passes; the prepared patch.

        On a profile target, a patch with segment operations is prepared first
        (``prepare_profile_segments``); None when it needs no preparing. Each
        attempt is ``_try_once``; a resolver an attempt asks for is loaded, once
        per type in this call, and the attempt is made again. Nothing of the
        state but the resolvers changes, and whatever else fails raises.
        """
        loaded: set[type] = set()
        prepared: ProfileSegmentPatch | None = None
        if edit.profile_rules and holds_segment_ops(edit.nested):
            prepared = await self._prepare_profile_segments(
                edit.state, edit.mod, edit.nested, loaded
            )
        while True:
            try:
                _try_once(edit, prepared)
            except ResolverNotLoadedError as error:
                # The trial worked on a copy: loading and trying again
                # leaves the state as it was.
                await self.load_resolvers(edit.state, _to_load(error, loaded))
            else:
                return prepared

    async def _prepare_profile_segments(
        self,
        state: ConfigState,
        mod: AnyStoredModule,
        nested: Mapping[str, object],
        loaded: set[type],
    ) -> ProfileSegmentPatch:
        """The segment rules of a patch to a profile module.

        From ``prepare_profile_segments``, loading a resolver it asks for.
        """
        while True:
            try:
                return await prepare_profile_segments(
                    state,
                    mod,
                    nested,
                    resolve=lambda name: self._resolve_segment(state, name, loaded),
                )
            except ResolverNotLoadedError as error:
                await self.load_resolvers(state, _to_load(error, loaded))

    async def assign_segment(
        self,
        state: ConfigState,
        name: str,
        *,
        qos_rules: Sequence[QosRule | Mapping[str, object]] | None = None,
    ) -> ConfigState:
        """Assign the enterprise segment *name* to the profile of *state*, in memory.

        Each of the profile's device settings, QoS and firewall modules that
        lacks the segment gets the entry the Orchestrator UI makes for a new
        segment (``new_segment_entry``), and a QoS entry the profile already
        has that lacks some of the template's four ``defaults`` gets those. A
        segment that one or two of the modules have already is completed. The
        next ``save`` writes the modules, device settings first unless the same
        save removes a hub, with the ``deviceSettings:segment`` row the segment
        needs; ``plan()`` and ``dry_run`` show it. An entry nobody edits
        afterwards is no change from the template, and one that is edited
        (``apply``, or assignment to the models) is.

        *name* is resolved strictly by the state's segment resolver: any case
        of the enterprise's name, and ``ResolutionError``, with suggestions,
        for one it does not know. *qos_rules* are the rules of the new QoS
        entry, in place of the template's, in order: each a ``QosRule`` or its
        JSON (a missing ``ruleLogicalId`` is made). Two of one name or one
        ``ruleLogicalId`` raise ``ValueError``, and so do *qos_rules* for a
        profile whose QoS module already has the segment, which would drop
        them: change those rules with a patch.

        Raises ``ValueError`` for an edge target (an edge has the segments of
        its profile), for a profile that lacks one of the three modules, for a
        module that is not at the profile level (``load_profile`` loads them
        so; call ``.for_profile()`` on one you built), and when the segment is
        in all three already, with its defaults complete: change it with a
        patch. Raises ``LookupError`` for a state with no segment resolver
        (``await pipeline.load_resolvers(state)`` gives it one), and what
        ``plan_module`` raises for an entry that could not be written.

        All or nothing: the assignment is planned on copies of the modules
        first, and an error leaves *state*, and ``plan()``, as they were.
        Returns *state*, changed in place. Holds ``state.lock`` for the whole
        call, as :meth:`apply` does, and raises ``RuntimeError`` when the
        calling task holds it already.
        """
        async with state.lock:
            rules = _qos_rules_json(qos_rules)
            modules = segment_modules(state)
            loaded: set[type] = set()
            identity = await self._resolve_segment(state, name, loaded)
            assignment = plan_assignment(state, identity, qos_rules=rules)
            if not assignment.entries and not assignment.defaults:
                raise ValueError(
                    f"{identity.name} is in all three modules already; change it "
                    "with a patch, such as "
                    f'{{"segments": {{"{identity.name}": {{...}}}}}}.'
                )
            while True:
                try:
                    assign_on_trial(state, modules, [assignment])
                    break
                except ResolverNotLoadedError as error:
                    # The trial worked on copies: loading and trying again
                    # leaves the state as it was.
                    await self.load_resolvers(state, _to_load(error, loaded))
            apply_assignment(modules, assignment)
            return state

    async def revert(self, state: ConfigState, *, module: str) -> ConfigState:
        """Take back every edit to *module* of *state*, in memory.

        The module is put back as the VCO last gave it: as loaded, or, after a
        save or a write error that left the module as stored, that one. Every
        edit to it since is gone, however it was made: a patch, an assignment
        to the models, an intent, a segment assignment's entry in this module,
        a placeholder. A module this state created is removed, and a module
        the target lacks changes nothing.

        No other module is touched and nothing is cascaded, so an edit
        elsewhere that relied on this module is judged by the next ``plan()``:
        ``SegmentSetError`` for a segment now in two modules of three,
        ``DanglingReferenceError`` for a rule whose hub is gone again. Objects
        taken from the state before belong to the module taken out and are
        detached, as after a save. Nothing is sent. The layer a state does not
        save is not reverted.

        It takes back edits, not what a save already did on the VCO: a role
        step a save sent before it stopped (``SaveStepError``) stays, and a
        state whose device settings edits are taken back plans nothing that
        mends it. After a ``SaveStepError``, save again instead.

        Returns *state*, changed in place. Holds ``state.lock`` for the whole
        call, as :meth:`apply` does, and raises ``RuntimeError`` when the
        calling task holds it already. Raises ``ValueError`` for an unknown
        *module*, for a new module whose insert may have reached the VCO
        (``state.unconfirmed_inserts``), and for a state with no snapshot
        (``take_snapshot`` was not called).
        """
        async with state.lock:
            state.revert_module(_resolve_module_name(module))
            return state

    async def _resolve_segment(
        self, state: ConfigState, name: str, loaded: set[type]
    ) -> SegmentIdentity:
        """The identity of segment *name* by the state's segment resolver.

        The resolver is loaded if it is registered and not loaded.
        """
        if state.resolvers.resolver_for(SegmentIdentity) is None:
            raise LookupError(
                "This state has no segment resolver, so the name "
                f"{name!r} cannot be resolved. Give the state its resolvers "
                "with `await pipeline.load_resolvers(state)` (`load` and "
                "`load_profile` do), then assign the segment."
            )
        while True:
            try:
                return await state.resolvers.resolve(SegmentName(name))
            except ResolverNotLoadedError as error:
                await self.load_resolvers(state, _to_load(error, loaded))

    async def save(
        self,
        state: ConfigState,
        *,
        dry_run: bool = False,
        validate: bool = True,
        repair_defaults: bool = True,
        on_hub_in_use: Literal["warn", "refuse"] = "warn",
    ) -> SaveResult:
        """Persist changes to VCO.

        1. Plan the write of every module (:meth:`ConfigState.plan`), and
           raise ``TypeError`` if a changed module is not one a save writes
           (device settings, QoS, WAN, firewall), also for ``dry_run``. The
           plan is in the write order: the device settings are written first,
           and the role steps right before them; in a save that removes a hub
           from a list (a plan with a ``disable`` step) they are written last,
           after the other modules, so that a rule stops using a hub before
           the hub loses its role. A profile's three modules (device settings,
           QoS and firewall) hold the same segments, so the plan raises
           ``SegmentSetError`` for an edit that removes a segment from one, or
           adds it to some and not all, and carries a ``segments_disagree``
           warning for a profile whose modules disagree as read.
           *repair_defaults* is false to leave the template ``defaults`` a
           profile QoS segment lacks out of the write; the plan warns of them
           either way (``defaults_repaired``).
        2. Run write-time validators on the changed modules. An error blocks
           the save: it raises ``SaveValidationError`` and sends nothing. A
           validator reports on the whole state, so an error in a module the
           save does not write blocks it too. A ``dry_run`` returns the errors
           in ``SaveResult.validation_errors`` instead, with the modules it
           would write in ``modules_updated`` and ``modules_inserted``.
        3. Send each changed module, in order, as an update or an insert with
           ``returnData`` (``write_module``), and replace the state's module with the
           one the VCO stored (:meth:`ConfigState.replace_module`). A module written
           after a profile's device settings is rebased
           (``write_module(..., rebase=True)``): the device settings write can change it
           too, so its version is read first, and a changed one makes the save read the
           module and apply your edits to it; one written before them (QoS and
           firewall, in a save that removes a hub) is not. Only a path you
           edited that the other change also reached raises
           ``ConfigConflictError``; what changed beside your edits is in
           ``SaveResult.server_changes``, each with ``merged`` true. Per
           module, ``SaveResult.left_out`` holds the planned changes the VCO stores its
           own way (a field it owns, a default it adds back where the plan removed it):
           neither applied nor lost, each with the value read, the value sent and the
           value stored. ``SaveResult.server_changes`` holds what the VCO stored
           otherwise than sent, at paths that are not lost edits: what it or another
           writer changed beside the plan, an ID it issued, an IPv6 address in canonical
           form, the value it stored at a left-out change. A value stored as it was read
           is not listed there; a left-out change kept so is in ``left_out``. In a list
           compared whole (a keyed list read with an item that has no key) nothing is
           left out: a value stored at an owned field otherwise than sent is a server
           change with ``as_read`` ``MISSING``.

        A profile whose edit adds a hub to a segment's hub, bridge or backhaul
        list, or removes one, has role steps (``ConfigPlan.steps``), which a
        write of the modules cannot do. The save sends them in step 3, right
        before the device settings write, in the UI's order. Before its first
        write it reads the device settings module's version (a module another
        writer changed stops the save here, with nothing sent, and the state
        holds the module as stored, as after a refused write), reads the hub
        services and decides every step (in place or not, and the ID its call
        takes), so that a refusal such as a hub whose service the VCO holds
        twice, or ``hub_in_use`` with ``"refuse"``, comes before the first
        request that changes anything. Then it writes the modules the plan
        lists before the device settings, sends each step not already in
        place, reads the services again, and writes the device settings with
        the hub rows the steps made (a hub added to a Branch-to-Hub list has
        its row, one removed from it loses its row), without a second version
        read, and then the modules after them.

        An Edge hub removed from a Branch-to-Hub list that edge QoS rules use
        (a ``deviceSettings:backHaulEdge`` row of an Edge's QoS module) loses
        its service to the role call, and with it the backhaul refs of those
        rules, which keep the hub in their data. The services read says so (it
        asks for ``edgeQOSUsage`` only for such a removal): with
        *on_hub_in_use* ``"warn"`` the save adds a ``hub_in_use`` warning per
        hub and segment to ``SaveResult.warnings``, naming the Edges, and goes
        on; with ``"refuse"`` it raises ``HubListError`` (``edges`` are their
        IDs and names) before it sends any step. A dry run warns or raises the
        same way.

        ``SaveResult.steps`` has an outcome per step: ``"sent"`` or
        ``"in_place"``. An error before any step was sent propagates as it is,
        also that of a module written before the steps. Once one was, or when
        the call of one fails, an ``Exception`` (also that of a module write)
        is raised as ``SaveStepError``, from the VCO's error: nothing is
        undone, the state keeps its edits, and a second ``save`` plans the
        steps again, skips those now in place and sends the rest. A
        cancellation propagates as it is.

        A provider you set (a DNS or authentication provider) goes with a ref
        row, which needs the enterprise's network services: the first plan
        that needs them reads them (:meth:`load_resolvers`), once per state,
        and ``dry_run`` does so too. ``SaveResult.warnings`` also holds the
        warnings of the plan (``ConfigPlan.warnings``): what disagrees in the
        refs as read and the write leaves as it is.

        A later :meth:`ConfigState.plan` is empty, and the state takes more
        edits and saves. Objects taken from the state before the save belong
        to the replaced modules and are detached: an edit through one raises
        ``DetachedObjectError``.

        After ``ConfigConflictError`` or ``WriteNotAppliedError`` the state
        holds the module as stored (they carry it, as ``error.module``; a
        ``ConfigConflictError`` for a module gone from its configuration has
        none, and the state keeps the module as read, with its edits), so the
        edits in ``error.lost_edits`` are to be redone: apply
        ``error.redo_patch()`` (``apply(state, module=error.module_name,
        patch=error.redo_patch())``) and save again;
        ``error.not_redone()`` lists the lost edits it leaves out. After
        ``WriteOutcomeUnknownError`` (the write was sent, or an insert was
        about to be sent again, and the module as stored could not be read)
        the state keeps the module as read, with its edits, and saving again
        is safe: an update carries its version, and an insert is read for
        before it is sent again. Any other error of a request leaves that
        module and the later ones with their edits too. ``error.completed``
        are the writes of this save before a write error. For every error
        raised once a write has been sent, ``save_progress(error)`` gives the
        writes stored, the module whose write raised and the modules not
        sent. ``dry_run`` stops after step 2 and sends nothing; it reads
        nothing either, except that a plan with role steps (below) has it
        read the hub services, two requests, to say which steps are in place.

        When an insert may have reached the VCO and the save failed (the
        answer timed out, the save was cancelled, or the VCO answered an ID
        and the module could not be read back), the state keeps the module as
        new and records its name in ``state.unconfirmed_inserts``. The next
        save first reads the configuration: a module of that name found there
        is the insert's answer, checked against the plan like any write, and
        nothing is inserted again; with none there, the save inserts. If that
        read cannot be used, the save raises ``WriteOutcomeUnknownError``
        before it sends anything. A ``dry_run`` plans the insert and adds a
        warning with the code ``insert_unconfirmed``.

        An update whose answer was lost needs no record: the next save sends
        the same version, which the VCO refuses or ignores when the first
        write was stored, so nothing is written twice. The save goes by the
        module it then holds: it passes when that module has every planned
        change, and otherwise raises one of the errors above with the module
        as stored.

        ``apply`` and ``save`` of one state run one after the other: this holds
        ``state.lock`` from the plan to the return, so a second save plans the
        state as the first one left it, and an ``apply`` made meanwhile waits
        and is then applied to the state as stored. Raises ``RuntimeError``
        when the calling task already holds ``state.lock``.

        An edit made directly on the models, by assignment or by an intent
        (``state.edge.firewall.syslog_forwarding.set_local(True)``), while a
        save is writing is not waited for: it lands in the module that is about
        to be replaced. Once that module is stored, the save puts it in the
        state without the edit and raises ``DetachedObjectError`` with the
        paths of such edits; redo them. So does a lookup that adds a
        placeholder to a module being written
        (``state.edge.firewall.segment("Guest")``, for a segment the edge only
        inherits). To edit from another task while a save may run, use
        ``apply``, or hold ``state.lock`` around the edit; do not wait, inside
        that block, for another task that calls ``apply`` or ``save``,
        which would wait for the lock without end.
        """
        _check_on_hub_in_use(on_hub_in_use)
        async with state.lock:
            plan = await self._plan_loading(state, repair_defaults=repair_defaults)
            result = SaveResult(
                plan=plan, dry_run=dry_run, warnings=list(plan.warnings)
            )

            if not plan.changed and not plan.steps:
                return result

            _list_writes(state, plan, result)
            if validate and not self._validate_writes(
                state, plan, result, dry_run=dry_run
            ):
                return result
            if dry_run:
                return await self._dry_result(state, plan, result, on_hub_in_use)
            return await self._send(state, plan, result, on_hub_in_use)

    def _validate_writes(
        self,
        state: ConfigState,
        plan: ConfigPlan,
        result: SaveResult,
        *,
        dry_run: bool,
    ) -> bool:
        """Run the write-time validators on the modules *plan* changes.

        Say whether the save goes on.

        Their warnings are added to *result*. An error raises
        ``SaveValidationError``, or, for *dry_run*, is put in
        ``result.validation_errors``, and the save stops there.
        """
        validation = self._validators.run(
            state,
            phase="write",
            modules=[ConfigurationModuleName(m.name) for m in plan.changed],
        )
        if not validation.is_valid:
            if not dry_run:
                raise SaveValidationError(validation.errors)
            result.validation_errors = validation.errors
            return False
        for warn in validation.warnings:
            result.warnings.append(_validation_error_to_warning(warn))
        return True

    async def _dry_result(
        self,
        state: ConfigState,
        plan: ConfigPlan,
        result: SaveResult,
        on_hub_in_use: Literal["warn", "refuse"],
    ) -> SaveResult:
        """*result* of a dry run, which sends nothing.

        It warns of each insert that may have reached the VCO, and says what
        the save would do with each step (:meth:`_dry_steps`).
        """
        result.warnings.extend(
            ValidationWarning(
                path=module_plan.name,
                message=(
                    "an earlier insert of this module may have reached the "
                    "VCO; the save reads the configuration first, and takes a "
                    "module of this name it finds there as the insert's answer"
                ),
                code="insert_unconfirmed",
            )
            for module_plan in plan.changed
            if module_plan.name in state.unconfirmed_inserts
        )
        if plan.steps:
            result.steps = await self._dry_steps(
                state, plan, result.warnings, on_hub_in_use
            )
        return result

    async def _dry_steps(
        self,
        state: ConfigState,
        plan: ConfigPlan,
        warnings: list[ValidationWarning],
        on_hub_in_use: Literal["warn", "refuse"],
    ) -> tuple[StepOutcome, ...]:
        """What a save would do with each step of *plan*; sends nothing.

        It is decided from one read of the hub services.

        The ``hub_in_use`` warnings are added to *warnings*, or ``HubListError``
        is raised for *on_hub_in_use* ``"refuse"``.
        """
        module = _profile_device_settings(state)
        services = await read_services(
            self.client,
            state.resolver_context.enterprise_id,
            qos_usage=_leaves_a_hub_list(plan.steps),
        )
        warnings.extend(
            _hubs_in_use_warnings(plan.steps, services, module, on_hub_in_use)
        )
        return tuple(
            StepOutcome(
                prepared.step, "in_place" if prepared.in_place else "would_send"
            )
            for prepared in prepare_steps(
                plan.steps,
                services,
                configuration_id=_configuration_id(module),
                module_id=module.id_,
            )
        )

    async def _check_steps_version(self, sending: _Sending) -> None:
        """Read the version of the device settings, before any step is sent.

        Another writer's change stops the save here: the ``ModuleWriteError``
        propagates as it is, with the module as stored put in the state, as
        a write refused for a stale version does. A write found stored (one
        whose answer was lost) is done before the steps
        (``sending.stored_already``); an error of storing it gets the save's
        progress.
        """
        state = sending.state
        module = _profile_device_settings(state)
        at = sending.device_settings_at
        try:
            sending.stored_already = await self.client.check_module_version(
                module,
                sending.changed[at],
                enterprise_id=state.resolver_context.enterprise_id,
            )
        except ModuleWriteError as error:
            # As a write refused for a stale version does: the state holds
            # the module as stored, so that a second save does not meet
            # the same conflict. No step was sent, so it propagates as it is.
            _ = sending.put_stored(sending.modules[at], error)
            raise
        if sending.stored_already is not None:
            # A write whose answer was lost: it is done, before the steps.
            try:
                sending.store_write(at, sending.stored_already)
            except BaseException as error:
                _ = sending.progress_of(error)
                raise

    async def _decide_steps(
        self,
        sending: _Sending,
        plan: ConfigPlan,
        on_hub_in_use: Literal["warn", "refuse"],
    ) -> _StepRun:
        """Decide every role step of *plan*; sends nothing.

        Reads the hub services, adds the ``hub_in_use`` warnings to the run
        (``HubListError`` with *on_hub_in_use* ``"refuse"``) and decides every
        step with ``prepare_steps``, so that a refusal comes before the first
        call. Returns the run, with the services and the decisions, for
        :meth:`_send_steps`. An error has a ``SaveProgress`` attached once a
        write was found stored (``_StepRun.guard``).
        """
        module = _profile_device_settings(sending.state)
        configuration_id = _configuration_id(module)
        run = _StepRun(tuple(p.name for p in sending.changed), sending.done)
        with run.guard():
            services = await read_services(
                self.client,
                sending.state.resolver_context.enterprise_id,
                qos_usage=_leaves_a_hub_list(plan.steps),
            )
            run.services = services
            run.warnings = _hubs_in_use_warnings(
                plan.steps, services, module, on_hub_in_use
            )
            # Decided, with each call's ID, before the first call is sent.
            run.prepared = prepare_steps(
                plan.steps,
                services,
                configuration_id=configuration_id,
                module_id=module.id_,
            )
        return run

    async def _send_steps(self, sending: _Sending, plan: ConfigPlan) -> None:
        """Send each role step of *plan* not in place, as decided.

        Then have the device settings plan in ``sending.changed`` carry the
        rows they made.

        Sends the steps in order, reads the services again and puts the hub
        rows the steps made in the device settings plan (``with_hub_rows``),
        at its place in ``sending.changed``, unless its write was found stored
        (``sending.stored_already``: it is not written again). An
        ``Exception`` once a step was sent, or in the call of one, is a
        ``SaveStepError``; any other error, a cancellation included,
        propagates as it is. A ``SaveProgress`` is attached to the error once
        a call was made or a write was found stored (``_StepRun.guard``).

        The decisions in ``sending.run`` were made against the device settings
        module and the configuration ID it looks up again here, so nothing
        between deciding and sending the steps may replace the device settings
        module. The writes made in between (the modules the plan lists before
        the device settings) each replace only their own module.
        """
        run = sending.run
        services = None if run is None else run.services
        if run is None or services is None:
            raise RuntimeError("the steps of a save are sent once they are decided")
        enterprise_id = sending.state.resolver_context.enterprise_id
        module = _profile_device_settings(sending.state)
        configuration_id = _configuration_id(module)
        position = sending.device_settings_at
        with run.guard():
            for prepared in run.prepared:
                step = prepared.step
                if prepared.in_place:
                    run.in_place.append(step)
                    run.outcomes.append(StepOutcome(step, "in_place"))
                    continue
                run.calling = True
                try:
                    await send_step(
                        self.client,
                        step,
                        services,
                        configuration_id=configuration_id,
                        module_id=module.id_,
                        enterprise_id=enterprise_id,
                        target_id=prepared.target_id,
                    )
                except Exception as error:
                    raise run.error(error, step=step) from error
                run.sent.append(step)
                run.outcomes.append(StepOutcome(step, "sent"))
            services = await read_services(self.client, enterprise_id, qos_usage=False)
            if sending.stored_already is None:
                added, removed = hub_rows(
                    module.raw_refs,
                    plan.steps,
                    services,
                    configuration_id=configuration_id,
                    module_id=module.id_,
                    now=module.data,
                )
                if added or removed:
                    sending.changed[position] = with_hub_rows(
                        sending.changed[position],
                        added=added,
                        removed=removed,
                        segments={s.segment_object_id: s.segment for s in plan.steps},
                    )

    async def _send(
        self,
        state: ConfigState,
        plan: ConfigPlan,
        result: SaveResult,
        on_hub_in_use: Literal["warn", "refuse"],
    ) -> SaveResult:
        """Write each changed module of *plan* in order, with its role steps.

        The modules are written in the plan's order, and each module as stored
        is put in *state*. The device settings are written first, and the role
        steps right before them; in a save that removes a hub from a list (a
        plan with a ``disable`` step) they are written last, after the other
        modules, so that a rule stops using a hub before the hub loses its
        role.

        Raises ``TypeError`` before the first request when a changed module is
        not one a save writes. A ``ModuleWriteError`` puts the module as
        stored in *state* too, when it carries one, and gets the writes before
        it as ``completed``. Every error raised once the first write may be
        sent, of any class and a cancellation included, gets a
        ``SaveProgress`` (``save_progress(error)``): the writes stored, the
        module being written and the modules after it. An insert that fails
        in any way but with the module as stored is recorded in
        ``state.unconfirmed_inserts``, and the next save reads before it
        inserts that module again. An edit made to a module while it was
        written (or a placeholder a lookup added to it) is not in the module
        as stored: once that module is stored and in *state*, the save stops
        with a ``DetachedObjectError`` whose note also names the modules not
        written.

        Every check comes before the first write: the version read of the
        device settings (:meth:`_check_steps_version`), and the services read,
        the ``hub_in_use`` refusal and the decision of every step
        (:meth:`_decide_steps`). Then the modules the plan lists before the
        device settings are written, the steps are sent (:meth:`_send_steps`),
        and the device settings write that follows carries the rows they made
        and reads no version (``version_checked``). An error of a write before
        the steps propagates as it is. Once a step was sent, an ``Exception``
        of a module write is raised as a ``SaveStepError``, from that error,
        and its ``SaveProgress`` is that of the write.
        """
        changed = list(plan.changed)
        modules = [_require_writable(state.module(p.name)) for p in changed]
        sending = _Sending(
            state=state,
            result=result,
            changed=changed,
            modules=modules,
            dumps=[_dump(mod) for mod in modules],
            providers=[ref_only_values(mod.data) for mod in modules],
            done=[],
            device_settings_at=next(
                (
                    i
                    for i, p in enumerate(changed)
                    if p.name == ConfigurationModuleName.DEVICE_SETTINGS
                ),
                len(changed),
            ),
        )
        if not plan.steps:
            await self._write_modules(sending, range(len(changed)))
            return result
        # Every check comes before the first write.
        await self._check_steps_version(sending)
        run = await self._decide_steps(sending, plan, on_hub_in_use)
        sending.run = run
        result.warnings.extend(run.warnings)
        at = sending.device_settings_at
        await self._write_modules(sending, range(at))
        await self._send_steps(sending, plan)
        result.steps = tuple(run.outcomes)
        await self._write_modules(sending, range(at, len(changed)))
        return result

    async def _write_modules(self, sending: _Sending, positions: range) -> None:
        """Write the changed modules at *positions*, in order.

        Each module as stored is put in the state (``_Sending.store_write``).
        A device settings write the version read found stored is not written
        again.

        A ``ModuleWriteError`` puts the module as stored in the state, when it
        carries one, and gets the writes before it as ``completed``. An insert
        that fails in any way but with the module as stored is recorded in
        ``state.unconfirmed_inserts``. Every error gets the save's
        ``SaveProgress``; once a step was sent, an ``Exception`` is raised as
        a ``SaveStepError``, from that error, with the same progress.
        """
        state = sending.state
        run = sending.run
        position = positions.start
        try:
            for position in positions:
                if (
                    position == sending.device_settings_at
                    and sending.stored_already is not None
                ):
                    continue
                mod = sending.modules[position]
                module_plan = sending.changed[position]
                sending.failed = mod.name
                insert = module_plan.operation == "insert"
                try:
                    write = await _write_module(
                        self.client,
                        mod,
                        module_plan,
                        enterprise_id=state.resolver_context.enterprise_id,
                        if_absent=insert and mod.name in state.unconfirmed_inserts,
                        rebase=sending.device_settings_stored,
                        # The steps' read of the version holds for this write.
                        version_checked=(
                            run is not None and sending.is_device_settings(mod)
                        ),
                        resolvers=state.resolvers,
                        # The module the state holds now: after the device
                        # settings write, the one stored.
                        profile_device_settings=(
                            _profile_device_settings(state)
                            if sending.device_settings_stored
                            else None
                        ),
                    )
                except ModuleWriteError as error:
                    if not sending.put_stored(mod, error) and insert:
                        state.unconfirmed_inserts.add(mod.name)
                    error.completed = tuple(sending.done)
                    raise
                except BaseException:
                    # An insert may have reached the VCO: a transport error, a
                    # cancellation, or an error before the request was sent (the
                    # next save then reads once, finds nothing and inserts).
                    if insert:
                        state.unconfirmed_inserts.add(mod.name)
                    raise
                sending.store_write(position, write)
        except BaseException as error:
            progress = sending.progress_of(error)
            if isinstance(error, Exception) and run is not None and run.sent:
                # A step was sent and nothing undoes it: say what was done.
                step_error = SaveStepError(
                    f"{type(error).__name__}: {error}",
                    module_name=sending.modules[position].name,
                    steps_done=run.sent,
                    steps_in_place=run.in_place,
                    written=[write.name for write in sending.done],
                )
                progress.attach(step_error)
                raise step_error from error
            raise

    async def compare_effective(  # noqa: C901 (existing: split it when it next changes)
        self, state: ConfigState, *, modules: Sequence[str] | None = None
    ) -> EffectiveComparison:
        """Check the client's effective view of an edge against the VCO's modules.

        The modules are those the VCO composes for the edge.

        Reads ``edge/getEdgeConfigurationModules`` for the edge *state* was
        loaded for, without ``encryptKeys`` (a VCO may refuse the parameter),
        and compares each of *modules* (by default every converted one,
        ``CONVERTED_MODULES``) with ``state.effective``. The profile half of
        the state is read again first, so a profile changed since the load is
        not a finding; *state* is not changed, except that a resolver the plan
        needs is loaded into it, as ``save`` does. Every difference is an
        entry, and the ones that say the view and the VCO disagree are
        ``EffectiveComparison.findings``.

        When a rule of the firewall view names a security service group, the
        security objects of the enterprise (the groups, and the URL category,
        URL reputation and IDPS objects they name) are read too, one read for
        each type, and the keys the composer adds to that rule are compared with
        what those objects give: see ``composed_security``.

        Raises ``ValueError`` for a profile state (the VCO composes modules
        for edges), for a state with changes that are not saved (it differs
        from the VCO by design: save, or load the edge again), and for a
        module that is not converted. Raises ``ComposedModuleError`` for a
        module the VCO answers with ``null`` or leaves out: it hides a
        composition error that way, and there is nothing to compare with.
        """
        names = _converted(modules)
        edge_config = state.edge_config
        edge_id = state.resolver_context.edge_id
        if state.target_type != "edge" or edge_config is None or edge_id is None:
            raise ValueError(
                "compare_effective checks an edge, because the VCO composes modules "
                "for edges only, and this state is not one loaded for an edge. "
                "Load the edge with load(edge_id) and compare that state"
            )
        if (await self._plan_loading(state)).changed:
            raise ValueError(
                "compare_effective: this state has changes that are not saved, so "
                "it differs from the VCO by design and every change would be "
                "reported as a difference. Save the changes with save(state), or "
                "load the edge again, and compare then"
            )
        enterprise_id = state.resolver_context.enterprise_id
        profile_id = state.profile_config.id_
        if profile_id is None or profile_id is MISSING:
            raise ValueError(
                "compare_effective: the state's profile configuration has no ID to "
                "read it again with; load the edge with load(edge_id)"
            )

        read = GetConfiguration(id_=profile_id, with_=[GetConfigurationWith.MODULES])
        if enterprise_id is not None:
            read.enterprise_id = enterprise_id
        fresh = await self.client.get_configuration(read)
        if not isinstance(fresh, EnterpriseConfigurationProfile):
            raise ValueError(  # noqa: TRY004 (an edge configuration is a valid answer, which this call refuses)
                f"configuration {profile_id} is no longer a profile's: the VCO "
                "answers it as an edge's configuration. Load the edge again with "
                "load(edge_id)"
            )

        ask = GetEdgeConfigurationModules(
            edge_id=edge_id,
            modules=[ConfigurationModuleName(name) for name in names],
        )
        if enterprise_id is not None:
            ask.enterprise_id = enterprise_id
        composed = await self.client.get_edge_configuration_modules(ask)

        view = EffectiveView(edge_config, fresh)
        entries: list[EffectiveDifference] = []
        objects: SecurityObjects | None = None
        for name in names:
            attribute = module_attribute(name)
            module: object = getattr(composed, attribute, None)
            data: object = getattr(module, "raw_data", MISSING)
            if module is None or module is MISSING:
                said = (
                    f"answered null for the {name} module"
                    if module is None
                    else f"gave no {name} module"
                )
                raise ComposedModuleError(
                    f"edge/getEdgeConfigurationModules {said} of edge {edge_id}. "
                    "The VCO hides a composition error that way, so there is "
                    "nothing to compare the view with. Open the edge in the VCO "
                    "to see why its modules do not compose, or compare the other "
                    f"modules with modules=[...] (not {name!r})",
                    module=name,
                )
            if not isinstance(data, dict):
                raise ComposedModuleError(
                    f"edge/getEdgeConfigurationModules gave the {name} module of "
                    f"edge {edge_id} no data object, so there is nothing to "
                    "compare the view with. Read the module with "
                    "get_edge_configuration_modules to see what the VCO sent",
                    module=name,
                )
            module_view = cast("Layered[Effective]", getattr(view, attribute))
            if (
                name == "firewall"
                and objects is None
                and rules_naming_a_group(effective_dump(module_view)[0])
            ):
                objects = await self._read_security_objects(enterprise_id)
            entries.extend(
                compare_module(
                    name,
                    module_view,
                    cast("dict[str, Any]", data),
                    ignores=EFFECTIVE_IGNORES,
                    security_objects=objects if name == "firewall" else None,
                )
            )
        return EffectiveComparison(tuple(entries))

    async def _read_security_objects(
        self, enterprise_id: int | None
    ) -> SecurityObjects:
        """The enterprise's security service groups and the objects they name.

        The objects are of ``SECURITY_OBJECT_TYPES``; one read for each.
        """
        rows: list[dict[str, object]] = []
        for type_ in SECURITY_OBJECT_TYPES:
            ask = GetEnterpriseSecurityObjects(type_=type_)
            if enterprise_id is not None:
                ask.enterprise_id = enterprise_id
            rows.extend(
                row.model_dump(mode="json", by_alias=True)
                for row in await self.client.get_security_objects(ask)
            )
        return SecurityObjects.from_rows(rows)

    def validate(self, state: ConfigState) -> ValidationResult:
        """Run all write-time validators without saving (dry-run validation)."""
        return self._validators.run(state, phase="write")

    async def load_resolvers(self, state: ConfigState, *identities: type) -> None:
        """Give *state* its own resolvers, or load those it has for some identity types.

        With no *identities*, as ``load`` and ``load_profile`` do it for the
        state they return. Call it for a state built another way, such as from
        a cache: the state gets an unloaded copy of the pipeline's resolvers,
        loaded for ``state.resolver_context``. This replaces the state's whole
        registry, so a resolver registered on ``state.resolvers`` is dropped.
        The network-service and hub resolvers in that copy are not loaded: the
        tables are read when a plan or a patch first needs them.

        With *identities* (``NetworkServiceIdentity``, ``SegmentIdentity``,
        ...), it sets ``state.resolver_context`` on the state's registry and
        loads the resolver of each type in it, whether it was loaded before or
        not. A state that has none for a type gets one first: an unloaded copy
        of the pipeline's, or, for ``NetworkServiceIdentity``, a new
        ``NetworkServiceResolver`` and, for ``HubIdentity``, a new
        ``HubResolver``, on the pipeline's client. This is what
        ``save``, ``dry_run`` and ``apply`` do when they meet a resolver that is
        not loaded; call it to load a table at a time of your choosing. Raises
        ``ValueError`` for a type that neither the state nor the pipeline has a
        resolver for.
        """
        if not identities:
            resolvers = self._resolvers.unloaded()
            await self._load_state_resolvers(resolvers, state.resolver_context)
            state.resolvers = resolvers
            return
        state.resolvers.set_context(state.resolver_context)
        for identity in identities:
            resolver = self._state_resolver(state, identity)
            await resolver.load(state.resolver_context)

    def _state_resolver(self, state: ConfigState, identity: type) -> Resolver[Any]:
        """The resolver of *state* for *identity*.

        It is registered first when the state has none.

        The state's own, else an unloaded copy of the pipeline's, else, for
        ``NetworkServiceIdentity``, a new ``NetworkServiceResolver`` and, for
        ``HubIdentity``, a new ``HubResolver``.
        """
        held = state.resolvers.resolver_for(identity)
        if held is not None:
            return held
        resolver = self._resolvers.unloaded().resolver_for(identity)
        if resolver is None and identity is NetworkServiceIdentity:
            resolver = NetworkServiceResolver(self.client)
        if resolver is None and identity is HubIdentity:
            resolver = HubResolver(self.client)
        if resolver is None:
            raise ValueError(
                f"neither this pipeline nor the state has a resolver for "
                f"{identity.__name__}, so load_resolvers cannot load one. "
                "Register a resolver for it on pipeline.resolvers (a state loaded "
                "afterwards gets a copy) or on state.resolvers"
            )
        state.resolvers.register(resolver)
        return resolver

    async def _plan_loading(
        self, state: ConfigState, *, repair_defaults: bool = True
    ) -> ConfigPlan:
        """Plan *state*, loading a resolver the plan finds not loaded.

        A resolver is loaded once per identity type.

        A second ``ResolverNotLoadedError`` for a type that was loaded
        propagates: the load did not give the plan what it needs.
        """
        loaded: set[type] = set()
        while True:
            try:
                return state.plan(repair_defaults=repair_defaults)
            except ResolverNotLoadedError as error:
                await self.load_resolvers(state, _to_load(error, loaded))

    async def _load_state_resolvers(
        self, resolvers: ResolverRegistry, context: ResolverContext
    ) -> None:
        """Set *context* on *resolvers* and load the segment and application resolvers.

        The segment resolver is added if there is none; the application
        resolver is loaded only if there is one. A network-service resolver and
        a hub resolver are added, and left unloaded, if there are none: a plan
        or a patch that needs the services or the hubs loads them.
        """
        resolvers.set_context(context)
        try:
            _ = resolvers.get_resolver(NetworkServiceResolver)
        except LookupError:
            resolvers.register(NetworkServiceResolver(self.client))
        try:
            _ = resolvers.get_resolver(HubResolver)
        except LookupError:
            resolvers.register(HubResolver(self.client))
        try:
            segments = resolvers.get_resolver(SegmentResolver)
        except LookupError:
            segments = SegmentResolver(self.client)
            resolvers.register(segments)
        await segments.load(context)
        try:
            applications = resolvers.get_resolver(ApplicationResolver)
        except LookupError:
            return
        await applications.load(context)


def build_default_pipeline(
    client: VcoClientProtocol,
    *,
    validator_registry: ValidatorRegistry | None = None,
) -> ConfigPipeline:
    """Build a ConfigPipeline with the default resolvers.

    This is the standard bootstrap for intent operations: registers
    EdgeResolver and ProfileResolver. The validators in *validator_registry*,
    if given, run before a save, and an error from one blocks a save that is
    not a dry run.
    """
    resolvers = ResolverRegistry()
    resolvers.register(EdgeResolver(client))
    resolvers.register(ProfileResolver(client))

    return ConfigPipeline(
        client, resolvers=resolvers, validator_registry=validator_registry
    )


def _converted(modules: Sequence[str] | None) -> tuple[str, ...]:
    """The converted modules *modules* names (all of them for ``None``).

    ``ValueError`` for another.
    """
    if modules is None:
        return CONVERTED_MODULES
    names: list[str] = []
    for module in modules:
        name = _resolve_module_name(module).value
        if name not in CONVERTED_MODULES:
            raise ValueError(
                f"compare_effective: {module!r} has no effective view yet, so "
                "every key the VCO composes for it would be reported as "
                f"server_only. Converted modules: {', '.join(CONVERTED_MODULES)}"
            )
        names.append(name)
    return tuple(names)


class _SegmentMetadata(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def segment_id(self) -> int: ...


class _SegmentEntry(Protocol):
    @property
    def segment(self) -> _SegmentMetadata: ...


@runtime_checkable
class _SegmentLayer(Protocol):
    @property
    def segments(self) -> Sequence[_SegmentEntry]: ...

    def segment(self, name: str) -> _SegmentEntry: ...

    def profile_segment_name(self, selector: object) -> str | None: ...

    def profile_segment_id(self, selector: object) -> object | None: ...


def _is_object(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


# Stands for the selector of a segment entry that is left out of the patch.
_LEFT_OUT = object()


@dataclass(frozen=True)
class _SegmentScope:
    """What ``_prepare_segment`` reads of the target, the same for each entry."""

    data: _SegmentLayer
    """The edge layer whose ``segments`` the patch changes."""
    resolvers: IdentityResolver | None
    inherited: frozenset[str]
    """The ``segmentId`` (in string form) of each segment the edge only inherits."""


@dataclass(frozen=True)
class _ProfileSegment:
    """A segment the profile has, as ``_prepare_segment`` reads it."""

    name: str
    segment_id: object | None


def _prepare_segments(
    data: VcoModel,
    patch: Mapping[str, object],
    resolvers: IdentityResolver | None,
    inherited: frozenset[str],
) -> dict[str, object]:
    """*patch* for edge-layer *data*, with the segment rules of an edge target applied.

    An edge inherits each segment of its profile, and its layer has an entry
    only for some. For each object under ``segments``:

    - a selector that names an entry of the layer (``select_items``) is left
      as it is;
    - one that names a profile segment (``profile_segment_name``) the layer
      has under another name (another case, or by the profile segment's
      ``segmentId``, a name of its own) names that entry, and is keyed under
      the entry's name for any operation;
    - one that names a segment only the profile has:
      a change or a ``move`` first adds the placeholder entry
      (``segment(name)``), which carries the profile's descriptor and IDs, and
      is keyed under the profile's name (an empty object changes nothing and
      adds none: the entry is left out of the patch returned, and the segment
      still counts as named); an ``add`` or a ``delete`` raises
      ``PatchError``, and adds nothing;
    - one that names a segment neither layer has: an ``add`` of it raises
      ``PatchError`` (``segment_rule``) when the data is bound to a profile,
      because an edge uses only its profile's segments; anything else, and
      any entry on data that is not bound to a profile, is left to
      ``merge_patch``.

    *inherited* holds the ``segmentId`` (in string form) of each segment the
    edge module did not have when it was read
    (``ConfigState.inherited_segment_ids``). A save writes those after the
    edge's own, in the profile's order, so a ``_position`` for one of them
    (``{"_position": ...}`` (``move(...)``), or on a change) raises
    ``PatchError``, and so does a ``_position`` given ``before`` or ``after``
    one, whatever the operation. A placeholder an earlier patch added is still
    one of them.

    Under ``"+"`` or ``"+tag"``, an ``add`` whose body gives the segment's
    name, or an ID that names a segment (``_added_segment_key``), is read as
    an ``add`` under that name; one for a segment the layer has under another
    name raises ``PatchError``. An ``add`` that names none is left to
    ``merge_patch``. Raises ``PatchError`` if two selectors name one segment,
    and ``ResolverNotLoadedError`` where an ID is to be looked up in a
    segment resolver that is not loaded.
    """
    segments = patch.get("segments")
    if not isinstance(data, _SegmentLayer) or not _is_object(segments):
        return dict(patch)
    scope = _SegmentScope(data, resolvers, inherited)
    prepared: dict[object, object] = {}
    named: dict[str, Path] = {}
    for selector, value in segments.items():
        key: object = selector
        if _is_object(value):
            where = entry_path(SEGMENTS, selector)
            op = parse_item_op(value, where)
            key, name = _prepare_segment(scope, selector, op, where)
            if name is not None:
                if name in named:
                    raise PatchError(
                        f"{named[name]} names segment {name!r} too: "
                        "one selector per segment",
                        where,
                        "duplicate",
                    )
                named[name] = where
            if key is _LEFT_OUT:
                continue
        prepared[key] = value
    return {**patch, "segments": prepared}


def _prepare_segment(
    scope: _SegmentScope, selector: object, op: ItemOp, where: Path
) -> tuple[object, str | None]:
    """The selector to merge one segment entry under, and the segment it names.

    For ``_prepare_segments``.

    The segment is None where the entry is left to ``merge_patch`` without
    naming one, and for ``"+"``. The selector is ``_LEFT_OUT`` for an empty
    object that names a segment the edge only inherits: it changes nothing, so
    no placeholder is added and the entry is not merged. Raises
    ``PatchError`` for a place the edge cannot store: one for a segment in
    ``scope.inherited``, or one given ``before`` or ``after`` such a segment.
    This comes before a placeholder is added.
    """
    data = scope.data
    _refuse_place_by_inherited(data, op, where, scope.resolvers, scope.inherited)
    looked = _segment_lookup(scope, selector, op)
    if looked is None:
        return selector, None
    new, lookup = looked
    found = _layer_segment(data, lookup, scope.resolvers)
    if found is not None:
        return selector, _name_in_layer(scope, found, new=new, op=op, where=where)
    try:
        name = data.profile_segment_name(lookup)
    except UnboundError:
        return selector, None
    if name is None:
        return _prepare_unknown_segment(selector, lookup, op, where)
    profile = _ProfileSegment(name, data.profile_segment_id(lookup))
    return _prepare_profile_segment(scope, profile, new=new, op=op, where=where)


def _segment_lookup(
    scope: _SegmentScope, selector: object, op: ItemOp
) -> tuple[bool, object] | None:
    """Whether *selector* is ``"+"``, and what to look the segment up by.

    That is the selector, or for ``"+"`` what an ``add`` gives in its body
    (``_added_segment_key``). None for ``"+"`` where *op* names no segment:
    the entry is left to ``merge_patch``.
    """
    new = is_new(selector)
    if not new:
        return new, selector
    given = _added_segment_key(scope, op) if op.kind == "add" else None
    if given is None:
        return None
    return new, given


# Mirrors the ID fields merge_patch finds for SegmentMetadata
# (_identity_of_given_id: its Resolvable fields that are not a value set, under
# their _input_keys); written out, as that derivation is private to core.merge.
_SEGMENT_IDS: Final = (
    ("segment_id", ("segmentId", "segment_id")),
    ("segment_logical_id", ("segmentLogicalId", "segment_logical_id")),
)
"""Each ID field of ``SegmentMetadata`` ``merge_patch`` finds a segment by.

In its order there, with the keys a patch gives it under.
"""


def _added_segment_key(scope: _SegmentScope, op: ItemOp) -> str | None:
    """The segment the body of an ``add`` under ``"+"`` names, or None.

    ``merge_patch`` takes the segment from the body's ``segment.name``, or,
    where the body gives an ID and not the name, from the segment the
    resolver finds by that ID: the first of ``segmentId`` and
    ``segmentLogicalId`` that reads as one (``read_given_id``: ``"7"`` and
    ``7.0`` are the ``segmentId`` 7). So the name, else the segment that ID
    names (``_segment_named_by_id``). None where the body gives neither, or
    an ID that names no segment: the add is left to ``merge_patch``, which
    raises for it.
    """
    name = added_segment_name(op)
    if name is not None:
        return name
    descriptor = op.body.get("segment")
    if not is_dict(descriptor):
        return None
    for field_name, keys in _SEGMENT_IDS:
        given = _given(descriptor, keys)
        if given is None:
            continue
        field_info = SegmentMetadata.model_fields[field_name]
        try:
            value = read_given_id(field_info, given)
        except ValueError:  # merge_patch tries the next ID, as here
            continue
        return _segment_named_by_id(scope, field_info, value)
    return None


def _given(descriptor: Mapping[object, object], keys: Sequence[str]) -> object | None:
    """The first value *descriptor* gives under one of *keys*, or None."""
    return next((descriptor[k] for k in keys if descriptor.get(k) is not None), None)


def _segment_named_by_id(
    scope: _SegmentScope, field_info: FieldInfo, value: object
) -> str | None:
    """The segment the ID *value* of the ``SegmentMetadata`` field names, or None.

    With a segment resolver registered, the name of the loaded segment that
    has the ID, as ``merge_patch`` finds it, and None for an ID no loaded
    segment has: ``merge_patch`` raises its ``ResolutionError`` for that. A
    resolver that must be loaded first (``requires_load``) and is not raises
    ``ResolverNotLoadedError``, so that ``apply`` loads it and tries again;
    the stock ``SegmentResolver`` needs no load, and unloaded it has no
    segment, so every ID is None. With none, a ``segmentId`` in string form
    where the layer or the profile has a segment it names
    (``_names_a_segment``), as a selector does; any other ID names nothing
    the patch can be checked against, and ``merge_patch`` refuses the add
    for its lack of a name.
    """
    resolvable = get_resolvable(field_info)
    if resolvable is None:
        raise TypeError("a segment ID field is not Resolvable")
    resolvers = scope.resolvers
    if resolvers is not None and resolvers.resolver_for(SegmentIdentity) is not None:
        try:
            identity = resolvers.lookup_by(SegmentIdentity, resolvable.accessor, value)
        except ResolverNotLoadedError:
            raise
        except ResolutionError:
            return None
        return identity.name
    if resolvable.attribute != "segment_id":
        return None
    text = str(value)
    return text if _names_a_segment(scope.data, text) else None


def _names_a_segment(data: _SegmentLayer, selector: str) -> bool:
    """Whether *selector* names an entry of the layer or a segment of its profile."""
    if _layer_segment(data, selector, None) is not None:
        return True
    try:
        return data.profile_segment_name(selector) is not None
    except UnboundError:
        return False


def _name_in_layer(
    scope: _SegmentScope,
    found: _SegmentEntry,
    *,
    new: bool,
    op: ItemOp,
    where: Path,
) -> str | None:
    """The name of the segment *found* in the layer, or None for ``"+"``.

    Raises ``PatchError`` for a place the edge cannot store for it.
    """
    _refuse_place_for(
        op, found.segment.name, found.segment.segment_id, where, scope.inherited
    )
    return None if new else found.segment.name


def _prepare_unknown_segment(
    selector: object, name: object, op: ItemOp, where: Path
) -> tuple[object, None]:
    """The entry for a segment the layer lacks and its (bound) profile lacks too.

    *name* is what the segment was looked up by: a selector, or for ``"+"``
    the name ``_added_segment_key`` gives. That is never a bare ID: an ID
    that names no segment there is left to ``merge_patch``, so the advice
    below is always a name the profile side reads. An ``add`` of it raises
    ``PatchError``: an edge uses only its profile's segments, so an entry
    saved for one the profile lacks would never be used. Any other operation
    is left to ``merge_patch``: it raises for a change and for a ``delete``,
    as the layer has no entry for the segment.
    """
    if op.kind == "add":
        patch = json.dumps({"segments": {str(name): {"_op": "add"}}})
        raise PatchError(
            f"the edge's profile has no segment {json.dumps(str(name))}, and an "
            "edge uses only its profile's segments: an entry for it here would "
            "be saved and never used. Assign the segment to the profile first, "
            f"with {patch} (add()) on the "
            "profile's device settings, QoS or firewall, or with "
            "ConfigPipeline.assign_segment; then change it on the edge",
            where,
            "segment_rule",
        )
    return selector, None


def _prepare_profile_segment(
    scope: _SegmentScope,
    profile: _ProfileSegment,
    *,
    new: bool,
    op: ItemOp,
    where: Path,
) -> tuple[object, str | None]:
    """The selector and segment for one only the profile has, or has under another name.

    The layer may have the profile's segment under another name (another
    case, or a name of its own): the patch is for that entry. Otherwise a
    change or a ``move`` adds the placeholder entry, and an ``add`` or a
    ``delete`` raises ``PatchError``. An empty change adds none, and its
    selector is ``_LEFT_OUT``.
    """
    name = profile.name
    own = _own_entry(scope.data, profile)
    if own is not None:
        # The layer has it, under a name the selector does not give (another
        # case, or a name of its own): the patch is for that entry.
        entry = own.segment.name
        if not new:
            _refuse_place_for(op, name, own.segment.segment_id, where, scope.inherited)
            return entry, entry
        raise PatchError(
            f"segment {name!r} is already in the edge layer, as {entry!r}: change "
            f"it with a plain object under {entry!r}, not an add",
            where,
            "exists",
        )
    _refuse_add_or_delete(op, name, where)
    _refuse_place_for(op, name, profile.segment_id, where, scope.inherited)
    if op.kind == "change" and not op.body:
        return _LEFT_OUT, name
    _ = scope.data.segment(name)
    return name, name


def _own_entry(data: _SegmentLayer, profile: _ProfileSegment) -> _SegmentEntry | None:
    """The entry of the layer for *profile*, under its name or its ``segmentId``."""
    own = _layer_segment(data, profile.name, None)
    if own is None and profile.segment_id is not None:
        # The layer may have the profile's segment under another name.
        own = _layer_segment(data, str(profile.segment_id), None)
    return own


def _refuse_add_or_delete(op: ItemOp, name: str, where: Path) -> None:
    """Refuse an ``add`` or a ``delete`` of segment *name*, which the edge inherits.

    Raises ``PatchError`` at *where*: the layer has no entry for it.
    """
    if op.kind == "add":
        raise PatchError(
            f"the edge has segment {name!r} by inheritance from its profile: "
            'change it with a plain object, not {"_op": "add"} (add())',
            where,
            "segment_rule",
        )
    if op.kind == "delete":
        raise PatchError(
            f"the edge layer has no entry to delete for segment {name!r}, which "
            "it inherits from its profile: leave it out of the patch, as "
            '{"_op": "delete"} (delete()) removes only an entry the edge layer holds',
            where,
            "segment_rule",
        )


def _refuse_place_for(
    op: ItemOp,
    name: str,
    segment_id: object,
    where: Path,
    inherited: frozenset[str],
) -> None:
    """Refuse to move or place segment *name* if the edge only inherits it.

    Moving it is a ``move``, placing it a change with a ``_position``.

    Raises ``PatchError`` at *where*. A save writes the segments in
    *inherited* after the edge's own, in the profile's order, so a place for
    one cannot be stored.
    """
    if op.place is None or op.kind not in ("move", "change"):
        return
    if segment_id is None or str(segment_id) not in inherited:
        return
    raise PatchError(
        f"the edge inherits segment {name!r} from its profile and had no entry "
        "of its own for it when it was read. A save writes such a segment "
        "after the edge's own, in the profile's order, so it cannot be given a "
        'place. For a move, such as {"_position": "first"} (`move(first=True)`), '
        'drop the entry. For a change, drop "_position" and keep the fields',
        where,
        "segment_rule",
    )


def _refuse_place_by_inherited(
    data: _SegmentLayer,
    op: ItemOp,
    where: Path,
    resolvers: IdentityResolver | None,
    inherited: frozenset[str],
) -> None:
    """Refuse a ``_position`` of *op* that anchors on an inherited segment.

    That is ``before`` or ``after`` a segment the edge only inherits.

    Raises ``PatchError`` at *where*. An anchor that names no segment, in the
    layer or the profile, is left to ``merge_patch``.
    """
    place = op.place
    if place is None or place.kind not in ("before", "after") or not inherited:
        return
    anchor = place.anchor
    found = _layer_segment(data, anchor, resolvers)
    if found is not None:
        anchor_id: object = found.segment.segment_id
    else:
        try:
            anchor_id = data.profile_segment_id(anchor)
        except UnboundError:
            return
    if anchor_id is None or str(anchor_id) not in inherited:
        return
    raise PatchError(
        f"the place is given by segment {anchor!r}, which the edge inherits "
        "from its profile and had no entry of its own for when it was read. "
        "A save writes such a segment after the edge's own, in the profile's "
        "order, so a place before or after it cannot be stored. Place the "
        'segment "first" or "last" ({"_position": "first"} (`move(first=True)`)), '
        "or before or after one of the edge's own segments "
        '({"_position": {"before": "<own segment>"}} '
        '(`move(before="<own segment>")`))',
        where,
        "segment_rule",
    )


def _layer_segment(
    data: _SegmentLayer, selector: object, resolvers: IdentityResolver | None
) -> _SegmentEntry | None:
    """The entry of the layer's ``segments`` that *selector* names, or None.

    A name a resolver does not know names no entry here, so None. The caller
    goes on to the profile's segments, and an ``add`` or a change of a
    segment neither layer has is refused there (``_prepare_unknown_segment``)
    or by ``merge_patch``.
    """
    if not isinstance(data, VcoModel):  # _prepare_segments is given module data
        raise TypeError(f"the segment layer is a {type(data).__name__}, not a model")
    try:
        positions = select_items(data, "segments", selector, resolvers)
    except ResolutionError:
        return None
    if not positions:
        return None
    return data.segments[positions[0]]


def _refuse_segment_ops_to_a_missing_module(
    state: ConfigState,
    module_name: str,
    patch: object,
    segment: str | None,
    path: str | None,
) -> None:
    """Raise ``PatchError`` for segment operations to a module the profile lacks.

    The patch has segment operations, and the module is one of the three.

    A profile target only, for device settings, QoS and firewall: an ``add``,
    a ``delete`` or a list value for ``segments`` works on all three modules,
    and inserting a module into a profile is not supported. A patch without
    them is left to ``state.module``, which raises ``ValueError`` as ever.
    """
    if state.target_type != "profile" or module_name not in PROFILE_SEGMENT_MODULES:
        return
    nested: object
    if segment is not None and path is None:
        nested = {"segments": {segment: patch}}
    elif segment is None and path is None:
        nested = patch
    else:
        return
    if isinstance(nested, dict) and holds_segment_ops(
        cast("dict[str, object]", nested)
    ):
        raise PatchError(
            f"the profile has no {module_name} module, and a patch that adds, "
            "removes or replaces a segment works on all three of device "
            "settings, QoS and firewall together. Inserting a module into a "
            "profile is not supported: add it in the VCO",
            SEGMENTS,
            "segment_rule",
        )


def _device_settings_as_read(state: ConfigState) -> object:
    """The raw data of the target's device settings module as read.

    ``MISSING`` when it has none.
    """
    module = state.find_module("deviceSettings")
    return MISSING if module is None else module.raw_data


def _try_assigning(
    state: ConfigState,
    mod: AnyStoredModule,
    prepared: ProfileSegmentPatch,
    unserved: Collection[Path],
) -> None:
    """Try *prepared* on copies of the three modules.

    *prepared* is the patch to profile module *mod* and the assignments it needs.

    The patch is merged into the copy of *mod* after the assignments, and
    must pass ``check_profile_segments``; each copy is then planned.
    Nothing of *state* changes, and whatever fails raises.
    """
    modules = segment_modules(state)
    name = str(mod.name)
    ds_as_read = _device_settings_as_read(state)
    before = _require_writable(mod).data

    def edit(copies: Mapping[str, AnyStoredModule]) -> None:
        after = _require_writable(copies[name]).data
        _ = after.merge_patch(prepared.patch, resolvers=state.resolvers)
        check_profile_segments(before, after, mod, ds_as_read, prepared.assignments)
        if isinstance(mod, QosConfigurationModule):
            state.fill_hub_services(after, skip=unserved)

    assign_on_trial(state, modules, prepared.assignments, edit)


def _patched_module(
    state: ConfigState,
    module: str,
    patch: object,
    segment: str | None,
    path: str | None,
) -> tuple[_WritableModule, bool]:
    """The module of *state* that ``apply``'s patch goes to, and whether this made it.

    A ``firewall`` or ``QOS`` module that an edge lacks is created empty.
    Segment operations to a module a profile lacks raise ``PatchError``
    (``_refuse_segment_ops_to_a_missing_module``), and so does a module a
    save does not write, after a module made here is dropped again.
    """
    internal_name = _resolve_module_name(module)
    existing = state.find_module(internal_name)
    if existing is None:
        _refuse_segment_ops_to_a_missing_module(
            state, str(internal_name), patch, segment, path
        )
    mod = existing if existing is not None else state.module(internal_name, create=True)
    if not isinstance(mod, _WRITABLE_MODULES):
        if existing is None:
            state.drop_module(mod)
        raise PatchError(
            f"{mod.name}: a save does not write this module (it writes "
            "device settings, QoS, WAN and firewall modules), so a "
            "patch to it is not applied. Make the change in the VCO",
            Path(()),
            "address",
        )
    return mod, existing is None


def _edit_of(
    state: ConfigState,
    mod: _WritableModule,
    patch: object,
    segment: str | None,
    path: str | None,
) -> _Edit:
    """The ``_Edit`` of *patch* at its address to *mod* of *state*.

    Raises ``PatchError`` for an address the class of the module's data does
    not have (``nest_patch``).
    """
    data = mod.data
    nested = nest_patch(type(data), patch, segment=segment, path=path)
    edge = state.target_type == "edge"
    profile_rules = (
        not edge
        and isinstance(data, _SegmentLayer)
        and str(mod.name) in PROFILE_SEGMENT_MODULES
    )
    unserved = (
        frozenset(missing_services(data))
        if isinstance(mod, QosConfigurationModule)
        else frozenset[Path]()
    )
    inherited = (
        state.inherited_segment_ids(mod)
        if edge and _is_object(nested.get("segments"))
        else frozenset[str]()
    )
    return _Edit(
        state=state,
        mod=mod,
        data=data,
        nested=nested,
        edge=edge,
        profile_rules=profile_rules,
        inherited=inherited,
        unserved=unserved,
    )


def _try_once(edit: _Edit, prepared: ProfileSegmentPatch | None) -> None:
    """Try the patch of *edit* once, on copies.

    With *prepared*, on copies of the three profile modules
    (``_try_assigning``). Else the patch is merged into a copy of the
    module's data, checked against the profile's segment rules where they
    apply, given its hub services on QoS, and planned as a save would.
    Nothing of the state changes, and whatever fails raises.
    """
    if prepared is not None:
        _try_assigning(edit.state, edit.mod, prepared, edit.unserved)
        return
    state = edit.state
    trial = edit.data.model_copy(deep=True)
    _ = trial.merge_patch(edit.patch_for(trial, None), resolvers=state.resolvers)
    if edit.profile_rules:
        check_profile_segments(
            edit.data, trial, edit.mod, _device_settings_as_read(state), ()
        )
    if isinstance(edit.mod, QosConfigurationModule):
        state.fill_hub_services(trial, skip=edit.unserved)
    _ = state.plan_module(edit.mod, trial)


def _merge(edit: _Edit, prepared: ProfileSegmentPatch | None) -> None:
    """Make the edit that passed its trial.

    The assignments, the merge into the module's data, and the hub services.
    """
    state = edit.state
    if prepared is not None:
        modules = segment_modules(state)
        for assignment in prepared.assignments:
            apply_assignment(modules, assignment)
    _ = edit.data.merge_patch(
        edit.patch_for(edit.data, prepared), resolvers=state.resolvers
    )
    if isinstance(edit.mod, QosConfigurationModule):
        # Cannot fail after the trial passed: the hub table and the
        # segments are the trial's, and the patch is the same.
        state.fill_hub_services(edit.data, skip=edit.unserved)


def _qos_rules_json(
    rules: Sequence[QosRule | Mapping[str, object]] | None,
) -> list[dict[str, object]] | None:
    """The JSON of *rules*, each validated as a ``QosRule``, in order; None for None.

    Raises ``ValueError`` for two rules of one name or one ``ruleLogicalId``,
    and pydantic's ``ValidationError`` (a ``ValueError``) for a rule that is not one.
    """
    if rules is None:
        return None
    dumped = [
        (
            rule if isinstance(rule, QosRule) else QosRule.model_validate(rule)
        ).model_dump(mode="json", by_alias=True)
        for rule in rules
    ]
    for key in ("name", "ruleLogicalId"):
        seen: set[object] = set()
        for rule in dumped:
            if rule[key] in seen:
                raise ValueError(
                    f"qos_rules holds more than one rule with the {key} "
                    f"{rule[key]!r}: each rule of a QoS entry has a name and a "
                    "ruleLogicalId of its own."
                )
            seen.add(rule[key])
    return dumped


def _written_note(done: Sequence[ModuleWrite[Any]]) -> str:
    """The note of an error that stops a save: the modules it wrote before."""
    return "written before this failure: " + ", ".join(
        f"{w.name} ({w.operation})" for w in done
    )


def _to_load(error: ResolverNotLoadedError, loaded: set[type]) -> type:
    """The identity type *error* asks to have loaded, noted in *loaded*.

    Raises *error* when that type was loaded before in the same call: the load
    did not give the plan what it needs.
    """
    kind = error.identity_type
    if not isinstance(kind, type) or kind in loaded:
        raise error
    loaded.add(kind)
    return kind


def _dump(mod: AnyStoredModule) -> object:
    """The JSON form of *mod*, to find an edit made while it was written."""
    return mod.model_dump(mode="json", by_alias=True)


def _require_writable(mod: AnyStoredModule) -> _WritableModule:
    """*mod*, if it is a module a save writes; else ``TypeError``."""
    if not isinstance(mod, _WRITABLE_MODULES):
        raise TypeError(
            f"{mod.name}: this state has an edit to a {type(mod).__name__}, which a "
            "save does not write (it writes device settings, QoS, WAN and firewall "
            f"modules). Take the edit back with revert (module {mod.name}), or "
            "load the state again; nothing was sent"
        )
    return mod


def _check_on_hub_in_use(on_hub_in_use: Literal["warn", "refuse"]) -> None:
    """``ValueError`` for an *on_hub_in_use* not ``"warn"`` or ``"refuse"``."""
    if on_hub_in_use not in ("warn", "refuse"):
        raise ValueError(  # pyright: ignore[reportUnreachable]  # a caller may pass any string
            f"on_hub_in_use is {on_hub_in_use!r}; give 'warn' to remove a "
            "hub that edge QoS rules use and say so in the result's "
            "warnings, or 'refuse' to stop before any step"
        )


def _list_writes(state: ConfigState, plan: ConfigPlan, result: SaveResult) -> None:
    """Put the modules *plan* writes in *result*, as updated or inserted.

    Raises ``TypeError`` first if a changed module is not one a save writes.
    """
    for module_plan in plan.changed:
        _ = _require_writable(state.module(module_plan.name))

    for module_plan in plan.changed:
        name = ConfigurationModuleName(module_plan.name)
        if module_plan.operation == "insert":
            result.modules_inserted.append(name)
        else:
            result.modules_updated.append(name)


def _validation_error_to_warning(err: ValidatorError) -> ValidationWarning:
    """Convert a ValidationError to a ValidationWarning for load-time use."""
    return ValidationWarning(
        path=err.path,
        message=err.message,
        code=err.code,
        severity=err.severity,
    )


async def _write_module(  # noqa: PLR0913 (existing: split it when it next changes)
    client: VcoClientProtocol,
    module: _WritableModule,
    plan: ModulePlan,
    *,
    enterprise_id: int | None,
    if_absent: bool,
    rebase: bool,
    version_checked: bool,
    resolvers: IdentityLookup,
    profile_device_settings: DeviceSettingsConfigurationModule | None,
) -> ModuleWrite[Any]:
    """``client.write_module``, with ``version_checked`` passed only when it is true.

    A save without steps calls it as it always did.

    *resolvers* and *profile_device_settings* are for the rebase's derivation
    of the module's refs, and make no difference to a write that is not rebased.
    """
    if version_checked:
        return await client.write_module(
            module,
            plan,
            enterprise_id=enterprise_id,
            if_absent=if_absent,
            rebase=rebase,
            version_checked=True,
            resolvers=resolvers,
            profile_device_settings=profile_device_settings,
        )
    return await client.write_module(
        module,
        plan,
        enterprise_id=enterprise_id,
        if_absent=if_absent,
        rebase=rebase,
        resolvers=resolvers,
        profile_device_settings=profile_device_settings,
    )
