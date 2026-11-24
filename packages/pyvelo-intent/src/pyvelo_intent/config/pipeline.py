"""ConfigPipeline: orchestration layer for load → apply → save configuration workflow."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeIs, cast, runtime_checkable

from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import (
    DetachedObjectError,
    ModuleWriteError,
    UnboundError,
)
from pyvelo.client.hub_services import missing_services
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.models.configuration.ref_owners import ref_only_values
from pyvelo.client.module_check import ModuleWrite
from pyvelo.client.module_write import ModulePlan, with_hub_rows
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    GetConfiguration,
    QosConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.client.schemas.edge import GetEdgeConfigurationStack
from pyvelo.config.resolver import Resolver, ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.validation import ValidationError as ValidatorError
from pyvelo.config.validation import ValidationResult, ValidatorRegistry
from pyvelo.core import ConfigContext, PatchError
from pyvelo.core.base import IdentityResolver, VcoModel
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
from pyvelo.core.merge import nest_patch, select_items
from pyvelo.core.patch import ItemOp, is_new, parse_item_op
from pyvelo.core.rebase import Path, raw_changes

from pyvelo_intent.config.hub_steps import (
    HubStep,
    hub_rows,
    in_place,
    read_services,
    send_step,
)
from pyvelo_intent.config.module_names import (
    resolve_module_name as _resolve_module_name,
)
from pyvelo_intent.config.profile_segments import (
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
from pyvelo_intent.config.state import (
    ConfigPlan,
    ConfigState,
    SaveProgress,
    SaveResult,
    StepOutcome,
    ValidationWarning,
    ref_only_changes,
)
from pyvelo_intent.errors import SaveStepError, SaveValidationError


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
    calling: bool = False
    """Whether a call was made, which may have reached the VCO."""

    def progress(self) -> SaveProgress:
        """The progress of a save that stopped in its steps: the writes stored so far, and the modules not yet written."""
        stored = {write.name for write in self.done}
        return SaveProgress(
            completed=tuple(self.done),
            failed=None,
            pending=tuple(name for name in self.modules if name not in stored),
        )

    def error(self, error: Exception, *, step: HubStep | None = None) -> SaveStepError:
        """The ``SaveStepError`` for *error*, raised at *step* (None after the steps), with the progress attached."""
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
    """Whether a step removes an Edge hub from a Branch-to-Hub list, which asks for the services' QoS usage."""
    return any(
        s.action == "disable" and s.role == "hub" and s.hub.kind == "edge"
        for s in steps
    )


def _profile_device_settings(state: ConfigState) -> DeviceSettingsConfigurationModule:
    """The device settings module of a profile state, the module the role steps are for."""
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


class ConfigPipeline:
    """Manages configuration load, merge, resolve, validate, save."""

    def __init__(
        self,
        client: VcoClientProtocol | None,
        resolvers: ResolverRegistry | None = None,
        validator_registry: ValidatorRegistry | None = None,
    ) -> None:
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

        if config.config_context is ConfigContext.EDGE:
            raise ValueError(
                f"configuration {profile_id} is the edge-specific configuration "
                "of an edge, not a profile. Load that edge with load(), by its "
                "ID or name; to edit its profile, give load_profile the "
                "profile's ID"
            )

        # Wrap it as a profile. Reuse the module objects: they hold the raw data
        # that a save is planned against, which a dump and re-validate would lose.
        if not isinstance(config, EnterpriseConfigurationProfile):
            profile_config = EnterpriseConfigurationProfile.model_construct(
                _fields_set=config.model_fields_set, **dict(config)
            ).for_profile()
        else:
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
        """Apply *patch* to the data of *module* on the state's target, at an address if one is given.

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
             under ``"+"`` whose body names one;
           - a ``delete`` of a segment the edge only inherits raises
             ``PatchError``: the layer has no entry to delete;
           - an edge entry the profile knows under another name (the same
             ``segmentId``) is that segment;
           - a segment neither layer has is left to ``merge_patch``; naming
             one segment twice raises ``PatchError``.
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
        save could not write, or a resolver's ``ResolutionError`` or
        ``LookupError``) leaves the state, and ``plan()``, as they were: no
        placeholder entries, and a module this call created is removed.
        ``LossyFieldError`` is raised also when an earlier assignment to the
        models had already made the module unwritable; its paths name that
        field.

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
            internal_name = _resolve_module_name(module)
            existing = state.find_module(internal_name)
            if existing is None:
                _refuse_segment_ops_to_a_missing_module(
                    state, str(internal_name), patch, segment, path
                )
            mod = (
                existing
                if existing is not None
                else state.module(internal_name, create=True)
            )
            edge = state.target_type == "edge"
            try:
                if not isinstance(mod, _WRITABLE_MODULES):
                    raise PatchError(
                        f"{mod.name}: a save does not write this module (it writes "
                        "device settings, QoS, WAN and firewall modules), so a "
                        "patch to it is not applied. Make the change in the VCO",
                        Path(()),
                        "address",
                    )
                data = mod.data
                nested = nest_patch(type(data), patch, segment=segment, path=path)
                segments = nested.get("segments")
                profile_rules = (
                    not edge
                    and isinstance(data, _SegmentLayer)
                    and str(mod.name) in PROFILE_SEGMENT_MODULES
                )
                prepared: ProfileSegmentPatch | None = None
                loaded: set[type] = set()
                unserved = (
                    frozenset(missing_services(data))
                    if isinstance(mod, QosConfigurationModule)
                    else frozenset[Path]()
                )
                if profile_rules and holds_segment_ops(nested):
                    prepared = await self._prepare_profile_segments(
                        state, mod, nested, loaded
                    )
                inherited = (
                    state.inherited_segment_ids(mod)
                    if edge and _is_object(segments)
                    else frozenset()
                )
                while True:
                    try:
                        if prepared is not None:
                            _try_assigning(state, mod, prepared, unserved)
                        else:
                            trial = data.model_copy(deep=True)
                            _ = trial.merge_patch(
                                _prepare_segments(
                                    trial, nested, state.resolvers, inherited
                                )
                                if edge
                                else nested,
                                resolvers=state.resolvers,
                            )
                            if profile_rules:
                                check_profile_segments(
                                    data,
                                    trial,
                                    mod,
                                    _device_settings_as_read(state),
                                    (),
                                )
                            if isinstance(mod, QosConfigurationModule):
                                state.fill_hub_services(trial, skip=unserved)
                            _ = state.plan_module(mod, trial)
                        break
                    except ResolverNotLoadedError as error:
                        # The trial worked on a copy: loading and trying again
                        # leaves the state as it was.
                        await self.load_resolvers(state, _to_load(error, loaded))
            except BaseException:
                if existing is None:
                    state.drop_module(mod)
                raise
            if prepared is not None:
                modules = segment_modules(state)
                for assignment in prepared.assignments:
                    apply_assignment(modules, assignment)
            _ = data.merge_patch(
                prepared.patch
                if prepared is not None
                else _prepare_segments(data, nested, state.resolvers, inherited)
                if edge
                else nested,
                resolvers=state.resolvers,
            )
            if isinstance(mod, QosConfigurationModule):
                state.fill_hub_services(data, skip=unserved)
            return state

    async def _prepare_profile_segments(
        self,
        state: ConfigState,
        mod: AnyConfigurationModule,
        nested: Mapping[str, object],
        loaded: set[type],
    ) -> ProfileSegmentPatch:
        """The segment rules of a patch to a profile module (``prepare_profile_segments``), loading a resolver it asks for."""
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
        next ``save`` writes the modules, device settings first, with the
        ``deviceSettings:segment`` row the segment needs; ``plan()`` and
        ``dry_run`` show it. An entry nobody edits afterwards is no change from
        the template, and one that is edited (``apply``, or assignment to the
        models) is.

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

    async def _resolve_segment(
        self, state: ConfigState, name: str, loaded: set[type]
    ) -> SegmentIdentity:
        """The identity of segment *name* by the state's segment resolver, loading it if it is registered and not loaded."""
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
    ) -> SaveResult:
        """Persist changes to VCO.

        1. Plan the write of every module (:meth:`ConfigState.plan`), and
           raise ``TypeError`` if a changed module is not one a save writes
           (device settings, QoS, WAN, firewall), also for ``dry_run``. A
           profile's device settings module is planned, and written, first.
           A profile's three modules (device settings, QoS and firewall) hold
           the same segments, so the plan raises ``SegmentSetError`` for an
           edit that removes a segment from one, or adds it to some and not
           all, and carries a ``segments_disagree`` warning for a profile
           whose modules disagree as read.
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
           ``returnData`` (``write_module``), and replace the state's module
           with the one the VCO stored (:meth:`ConfigState.replace_module`).
           A module written after a profile's device settings is rebased
           (``write_module(..., rebase=True)``): the device settings write can
           change it too, so its version is read first, and a changed one
           makes the save read the module and apply your edits to it. Only a
           path you edited that the other change also reached raises
           ``ConfigConflictError``; what changed beside your edits is in
           ``SaveResult.server_changes``, each with ``merged`` true.
           Per module, ``SaveResult.left_out`` holds the planned changes the
           VCO stores its own way (a field it owns, a default it adds back
           where the plan removed it): neither applied nor lost, each with
           the value read, the value sent and the value stored. ``SaveResult.server_changes``
           holds what the VCO stored otherwise than sent, at paths that are
           not lost edits: what it or another writer changed beside the
           plan, an ID it issued, an IPv6 address in canonical form, the
           value it stored at a left-out change. A value stored as it was
           read is not listed there; a left-out change kept so is in
           ``left_out``. In a list compared whole (a keyed list read with an
           item that has no key) nothing is left out: a value stored at an
           owned field otherwise than sent is a server change with
           ``as_read`` ``MISSING``.

        A profile whose edit adds a hub to a segment's hub, bridge or backhaul
        list, or removes one, has role steps (``ConfigPlan.steps``), which a
        write of the modules cannot do. The save sends them before step 3, in
        the UI's order: it reads the device settings module's version (a
        module another writer changed stops the save here, with nothing
        sent), reads the hub services, sends each step not already in place,
        reads the services again, and writes the device settings with the hub
        rows the steps made (a hub added to a Branch-to-Hub list has its
        row, one removed from it loses its row), without a second version
        read. ``SaveResult.steps`` has an outcome per step: ``"sent"`` or
        ``"in_place"``. An error before any step was sent propagates as it
        is. Once one was, or when the call of one fails, an ``Exception``
        (also that of a module write) is raised as ``SaveStepError``, from
        the VCO's error: nothing is undone, the state keeps its edits, and a
        second ``save`` plans the steps again, skips those now in place and
        sends the rest. A cancellation propagates as it is.

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
        edits in ``error.lost_edits`` are to be redone: apply ``error.redo_patch()`` (``apply(state,
        module=error.module_name, patch=error.redo_patch())``) and save again;
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

        An edit made by assignment (``state.edge.firewall.syslog_forwarding =
        True``) while a save is writing is not waited for: it lands in the
        module that is about to be replaced. Once that module is stored, the
        save puts it in the state without the edit and raises
        ``DetachedObjectError`` with the paths of such edits; redo them. So does
        a lookup that adds a placeholder to a module being written
        (``state.edge.firewall.segment("Guest")``, for a segment the edge only
        inherits). To edit by assignment from another task while a save may
        run, use ``apply``, or hold ``state.lock`` around the edit; do not wait,
        inside that block, for another task that calls ``apply`` or ``save``,
        which would wait for the lock without end.
        """
        async with state.lock:
            plan = await self._plan_loading(state, repair_defaults=repair_defaults)
            result = SaveResult(
                plan=plan, dry_run=dry_run, warnings=list(plan.warnings)
            )

            if not plan.changed and not plan.steps:
                return result

            for module_plan in plan.changed:
                _ = _require_writable(state.module(module_plan.name))

            for module_plan in plan.changed:
                name = ConfigurationModuleName(module_plan.name)
                if module_plan.operation == "insert":
                    result.modules_inserted.append(name)
                else:
                    result.modules_updated.append(name)

            # Run write-time validators
            if validate:
                validation = self._validators.run(
                    state,
                    phase="write",
                    modules=[ConfigurationModuleName(m.name) for m in plan.changed],
                )
                if not validation.is_valid:
                    if not dry_run:
                        raise SaveValidationError(validation.errors)
                    result.validation_errors = validation.errors
                    return result
                for warn in validation.warnings:
                    result.warnings.append(_validation_error_to_warning(warn))

            if dry_run:
                result.warnings.extend(
                    ValidationWarning(
                        path=module_plan.name,
                        message=(
                            "an earlier insert of this module may have reached the VCO; "
                            + "the save reads the configuration first, and takes a module "
                            + "of this name it finds there as the insert's answer"
                        ),
                        code="insert_unconfirmed",
                    )
                    for module_plan in plan.changed
                    if module_plan.name in state.unconfirmed_inserts
                )
                if plan.steps:
                    result.steps = await self._dry_steps(state, plan)
                return result
            return await self._send(state, plan, result)

    async def _dry_steps(
        self, state: ConfigState, plan: ConfigPlan
    ) -> tuple[StepOutcome, ...]:
        """What a save would do with each step of *plan*, from one read of the hub services: sends nothing."""
        module = _profile_device_settings(state)
        services = await read_services(
            self.client,
            state.resolver_context.enterprise_id,
            qos_usage=_leaves_a_hub_list(plan.steps),
        )
        return tuple(
            StepOutcome(
                step,
                "in_place"
                if in_place(
                    step,
                    services,
                    configuration_id=_configuration_id(module),
                    module_id=module.id_,
                )
                else "would_send",
            )
            for step in plan.steps
        )

    async def _run_steps(
        self,
        state: ConfigState,
        plan: ConfigPlan,
        changed: list[ModulePlan],
        done: list[ModuleWrite[Any]],
        *,
        device_settings_stored: bool,
    ) -> _StepRun:
        """Send the role steps of *plan*, and have the device settings plan in *changed* carry the rows they made.

        Reads the hub services, sends each step not in place, in order, reads
        the services again and puts the hub rows the steps made in the device
        settings plan (``with_hub_rows``), at its place in *changed*, unless
        *device_settings_stored* (its write was found stored and is in *done*:
        it is not written again). Returns what the steps did. An ``Exception``
        once a step was sent, or in the call of one, is a ``SaveStepError``;
        any other error, a cancellation included, propagates as it is. A
        ``SaveProgress`` is attached to the error once a call was made or a
        write was found stored; an error before either has none.
        """
        enterprise_id = state.resolver_context.enterprise_id
        module = _profile_device_settings(state)
        configuration_id = _configuration_id(module)
        position = next(i for i, p in enumerate(changed) if p.name == module.name)
        run = _StepRun(tuple(p.name for p in changed), done)
        try:
            services = await read_services(
                self.client, enterprise_id, qos_usage=_leaves_a_hub_list(plan.steps)
            )
            for step in plan.steps:
                if in_place(
                    step,
                    services,
                    configuration_id=configuration_id,
                    module_id=module.id_,
                ):
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
                    )
                except Exception as error:
                    raise run.error(error, step=step) from error
                run.sent.append(step)
                run.outcomes.append(StepOutcome(step, "sent"))
            services = await read_services(self.client, enterprise_id, qos_usage=False)
            if not device_settings_stored:
                added, removed = hub_rows(
                    module.raw_refs,
                    plan.steps,
                    services,
                    configuration_id=configuration_id,
                    module_id=module.id_,
                    now=module.data,
                )
                if added or removed:
                    changed[position] = with_hub_rows(
                        changed[position],
                        added=added,
                        removed=removed,
                        segments={s.segment_object_id: s.segment for s in plan.steps},
                    )
        except SaveStepError:
            raise
        except Exception as error:
            if run.sent:
                raise run.error(error) from error
            if run.calling or run.done:
                run.progress().attach(error)
            raise
        except BaseException as error:
            if run.calling or run.done:
                run.progress().attach(error)
            raise
        return run

    async def _send(
        self, state: ConfigState, plan: ConfigPlan, result: SaveResult
    ) -> SaveResult:
        """Send the role steps of *plan*, if it has any, then write each changed module, in order, and put the module as stored in *state*.

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

        The steps come first (:meth:`_run_steps`), and the device settings
        write that follows carries the rows they made and reads no version
        (``version_checked``). Once a step was sent, an ``Exception`` of a
        module write is raised as a ``SaveStepError``, from that error, and
        its ``SaveProgress`` is that of the write.
        """
        enterprise_id = state.resolver_context.enterprise_id
        changed = list(plan.changed)
        # Each write replaces only its own module, so these stay the state's.
        modules = [_require_writable(state.module(p.name)) for p in changed]
        # What each module holds as planned, to find an edit made while a write was out.
        dumps = [_dump(mod) for mod in modules]
        providers = [ref_only_values(mod.data) for mod in modules]
        done: list[ModuleWrite[Any]] = []
        failed: str | None = None
        position = 0
        # A profile's device settings write can change the modules after it.
        device_settings_stored = False

        def is_device_settings(mod: _WritableModule) -> bool:
            return (
                state.target_type == "profile"
                and mod.name == ConfigurationModuleName.DEVICE_SETTINGS
            )

        def progress_of(error: BaseException) -> SaveProgress:
            """The progress of this save, attached to *error*: the writes stored, the module being written and the modules after it."""
            stored = {write.name for write in done}
            progress = SaveProgress(
                completed=tuple(done),
                failed=failed,
                pending=tuple(
                    m.name for m in modules if m.name not in stored and m.name != failed
                ),
            )
            progress.attach(error)
            return progress

        def store_write(at: int, write: ModuleWrite[Any]) -> None:
            """Count *write* of the module at *at* as stored, and put the module as stored in the state.

            Raises ``DetachedObjectError`` for an edit made while it was written.
            """
            nonlocal failed, device_settings_stored
            mod = modules[at]
            insert = changed[at].operation == "insert"
            # Stored: it counts as written even if the state cannot take it back.
            done.append(write)
            failed = None
            if is_device_settings(mod):
                device_settings_stored = True
            paths = list(
                dict.fromkeys(
                    change.path
                    for change in raw_changes(dumps[at], _dump(mod), type(mod))
                )
            )
            # A provider is no part of a dump: compare it by itself.
            paths.extend(
                Path(("data", *path.parts))
                for path in ref_only_changes(providers[at], ref_only_values(mod.data))
            )
            try:
                state.replace_module(mod, write.module)
            except BaseException:
                # The insert is stored but the state still plans it: the
                # next save reads before it inserts again.
                if insert:
                    state.unconfirmed_inserts.add(mod.name)
                raise
            name = ConfigurationModuleName(write.name)
            result.server_changes[name] = write.server_changes
            result.left_out[name] = write.left_out
            result.written = tuple(done)
            if paths:
                error = DetachedObjectError(
                    f"{mod.name}: the module was edited while this save was writing it, at: "
                    + f"{', '.join(paths)}. The write is stored without those edits and the "
                    + "state holds the module as stored; take the objects from the state again "
                    + "and redo them. A path that is a placeholder a lookup added "
                    + "(segment(name), for a segment the edge only inherits) is no edit "
                    + "and needs no redo. To edit a state from another task while a save "
                    + "may run, use apply, or hold state.lock around the edit",
                    paths,
                )
                note = _written_note(done)
                if later := modules[at + 1 :]:
                    note += (
                        "; the save stopped here, so these are not written: "
                        + ", ".join(m.name for m in later)
                        + ". The next save writes them"
                    )
                error.add_note(note)
                raise error

        run: _StepRun | None = None
        stored_already: ModuleWrite[Any] | None = None
        device_settings_at = -1
        if plan.steps:
            module = _profile_device_settings(state)
            device_settings_at = next(
                i for i, p in enumerate(changed) if p.name == module.name
            )
            # Before anything is sent: another writer's change stops the save here.
            stored_already = await self.client.check_module_version(
                module, changed[device_settings_at], enterprise_id=enterprise_id
            )
            if stored_already is not None:
                # A write whose answer was lost: it is done, before the steps.
                try:
                    store_write(device_settings_at, stored_already)
                except BaseException as error:
                    _ = progress_of(error)
                    raise
            run = await self._run_steps(
                state,
                plan,
                changed,
                done,
                device_settings_stored=stored_already is not None,
            )
            result.steps = tuple(run.outcomes)
        try:
            for position, (module_plan, mod) in enumerate(
                zip(changed, modules, strict=True)
            ):
                if position == device_settings_at and stored_already is not None:
                    continue
                failed = mod.name
                insert = module_plan.operation == "insert"
                try:
                    write = await _write_module(
                        self.client,
                        mod,
                        module_plan,
                        enterprise_id=enterprise_id,
                        if_absent=insert and mod.name in state.unconfirmed_inserts,
                        rebase=device_settings_stored,
                        # The steps' read of the version holds for this write.
                        version_checked=run is not None and is_device_settings(mod),
                    )
                except ModuleWriteError as error:
                    stored = error.module
                    # Of the class of mod (write_module checks it), or None when
                    # the module could not be read: then the state keeps mod. It
                    # is also kept when the state no longer holds mod, so that the
                    # write error is not hidden behind the ValueError of replace_module.
                    if (
                        isinstance(stored, type(mod))
                        and state.find_module(mod.name) is mod
                    ):
                        state.replace_module(mod, stored)
                    elif insert:
                        state.unconfirmed_inserts.add(mod.name)
                    error.completed = tuple(done)
                    raise
                except BaseException:
                    # An insert may have reached the VCO: a transport error, a
                    # cancellation, or an error before the request was sent (the
                    # next save then reads once, finds nothing and inserts).
                    if insert:
                        state.unconfirmed_inserts.add(mod.name)
                    raise
                store_write(position, write)
        except BaseException as error:
            progress = progress_of(error)
            if isinstance(error, Exception) and run is not None and run.sent:
                # A step was sent and nothing undoes it: say what was done.
                step_error = SaveStepError(
                    f"{type(error).__name__}: {error}",
                    module_name=modules[position].name,
                    steps_done=run.sent,
                    steps_in_place=run.in_place,
                    written=[write.name for write in done],
                )
                progress.attach(step_error)
                raise step_error from error
            raise
        return result

    def validate(self, state: ConfigState) -> ValidationResult:
        """Run all write-time validators without saving (dry-run validation)."""
        return self._validators.run(state, phase="write")

    async def load_resolvers(self, state: ConfigState, *identities: type) -> None:
        """Give *state* resolvers of its own, or load the ones it has for some identity types.

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

    def _state_resolver(self, state: ConfigState, identity: type) -> Resolver[Any, Any]:
        """The resolver of *state* for *identity*, registered first when the state has none.

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
        """Plan *state*, loading a resolver the plan finds not loaded, once per identity type.

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
        """Set *context* on *resolvers*, then load the segment resolver, adding
        one if there is none, and the application resolver if there is one.

        A network-service resolver and a hub resolver are added, and left
        unloaded, if there are none: a plan or a patch that needs the services
        or the hubs loads them.
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
    - one that names a segment neither layer has, or any on data that is not
      bound to a profile, is left to ``merge_patch``.

    *inherited* holds the ``segmentId`` (in string form) of each segment the
    edge module did not have when it was read
    (``ConfigState.inherited_segment_ids``). A save writes those after the
    edge's own, in the profile's order, so a ``_position`` for one of them
    (``{"_position": ...}`` (``move(...)``), or on a change) raises
    ``PatchError``, and so does a ``_position`` given ``before`` or ``after``
    one, whatever the operation. A placeholder an earlier patch added is still
    one of them.

    Under ``"+"`` or ``"+tag"``, an ``add`` whose body gives the segment's
    name is read as an ``add`` under that name; one for a segment the layer
    has under another name raises ``PatchError``. Raises ``PatchError`` if two
    selectors name one segment.
    """
    segments = patch.get("segments")
    if not isinstance(data, _SegmentLayer) or not _is_object(segments):
        return dict(patch)
    prepared: dict[object, object] = {}
    named: dict[str, Path] = {}
    for selector, value in segments.items():
        key: object = selector
        if _is_object(value):
            where = entry_path(SEGMENTS, selector)
            op = parse_item_op(value, where)
            key, name = _prepare_segment(
                data, selector, op, where, resolvers, inherited
            )
            if name is not None:
                if name in named:
                    raise PatchError(
                        f"{named[name]} names segment {name!r} too: one selector per segment",
                        where,
                        "duplicate",
                    )
                named[name] = where
            if key is _LEFT_OUT:
                continue
        prepared[key] = value
    return {**patch, "segments": prepared}


def _prepare_segment(
    data: VcoModel,
    selector: object,
    op: ItemOp,
    where: Path,
    resolvers: IdentityResolver | None,
    inherited: frozenset[str],
) -> tuple[object, str | None]:
    """The selector to merge one segment entry under, and the segment it names (``_prepare_segments``).

    The segment is None where the entry is left to ``merge_patch`` without
    naming one, and for ``"+"``. The selector is ``_LEFT_OUT`` for an empty
    object that names a segment the edge only inherits: it changes nothing, so
    no placeholder is added and the entry is not merged. Raises
    ``PatchError`` for a place the edge cannot store: one for a segment in
    *inherited*, or one given ``before`` or ``after`` such a segment. This
    comes before a placeholder is added.
    """
    assert isinstance(data, _SegmentLayer)  # _prepare_segments checked
    _refuse_place_by_inherited(data, op, where, resolvers, inherited)
    new = is_new(selector)
    lookup: object = selector
    if new:
        given = added_segment_name(op) if op.kind == "add" else None
        if given is None:
            return selector, None
        lookup = given
    found = _layer_segment(data, lookup, resolvers)
    if found is not None:
        _refuse_place_for(
            op, found.segment.name, found.segment.segment_id, where, inherited
        )
        return selector, None if new else found.segment.name
    try:
        name = data.profile_segment_name(lookup)
    except UnboundError:
        return selector, None
    if name is None:
        return selector, None
    segment_id = data.profile_segment_id(lookup)
    own = _layer_segment(data, name, None)
    if own is None and segment_id is not None:
        # The layer may have the profile's segment under another name.
        own = _layer_segment(data, str(segment_id), None)
    if own is not None:
        # The layer has it, under a name the selector does not give (another
        # case, or a name of its own): the patch is for that entry.
        entry = own.segment.name
        if not new:
            _refuse_place_for(op, name, own.segment.segment_id, where, inherited)
            return entry, entry
        raise PatchError(
            f"segment {name!r} is already in the edge layer, as {entry!r}: change "
            + f"it with a plain object under {entry!r}, not an add",
            where,
            "exists",
        )
    if op.kind == "add":
        raise PatchError(
            f"the edge has segment {name!r} by inheritance from its profile: "
            + 'change it with a plain object, not {"_op": "add"} (add())',
            where,
            "segment_rule",
        )
    if op.kind == "delete":
        raise PatchError(
            f"the edge layer has no entry to delete for segment {name!r}, which "
            + "it inherits from its profile: leave it out of the patch, as "
            + '{"_op": "delete"} (delete()) removes only an entry the edge layer holds',
            where,
            "segment_rule",
        )
    _refuse_place_for(op, name, segment_id, where, inherited)
    if op.kind == "change" and not op.body:
        return _LEFT_OUT, name
    _ = data.segment(name)
    return name, name


def _refuse_place_for(
    op: ItemOp,
    name: str,
    segment_id: object,
    where: Path,
    inherited: frozenset[str],
) -> None:
    """Refuse a ``move``, or a change with a ``_position``, of segment *name* if the edge only inherits it.

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
        + "of its own for it when it was read. A save writes such a segment "
        + "after the edge's own, in the profile's order, so it cannot be given a "
        + 'place. For a move, such as {"_position": "first"} (`move(first=True)`), '
        + 'drop the entry. For a change, drop "_position" and keep the fields',
        where,
        "segment_rule",
    )


def _refuse_place_by_inherited(
    data: VcoModel,
    op: ItemOp,
    where: Path,
    resolvers: IdentityResolver | None,
    inherited: frozenset[str],
) -> None:
    """Refuse a ``_position`` of *op* given ``before`` or ``after`` a segment the edge only inherits.

    Raises ``PatchError`` at *where*. An anchor that names no segment, in the
    layer or the profile, is left to ``merge_patch``.
    """
    place = op.place
    if place is None or place.kind not in ("before", "after") or not inherited:
        return
    assert isinstance(data, _SegmentLayer)  # _prepare_segment checked
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
        + "from its profile and had no entry of its own for when it was read. "
        + "A save writes such a segment after the edge's own, in the profile's "
        + "order, so a place before or after it cannot be stored. Place the "
        + 'segment "first" or "last" ({"_position": "first"} (`move(first=True)`)), '
        + "or before or after one of the edge's own segments "
        + '({"_position": {"before": "<own segment>"}} '
        + '(`move(before="<own segment>")`))',
        where,
        "segment_rule",
    )


def _layer_segment(
    data: VcoModel, selector: object, resolvers: IdentityResolver | None
) -> _SegmentEntry | None:
    """The entry of the layer's ``segments`` that *selector* names, or None.

    A name a resolver does not know names no entry here: ``merge_patch``
    raises for it, if the profile does not have it either.
    """
    try:
        positions = select_items(data, "segments", selector, resolvers)
    except ResolutionError:
        return None
    if not positions:
        return None
    assert isinstance(data, _SegmentLayer)
    return data.segments[positions[0]]


def _refuse_segment_ops_to_a_missing_module(
    state: ConfigState,
    module_name: str,
    patch: object,
    segment: str | None,
    path: str | None,
) -> None:
    """Raise ``PatchError`` for a patch with segment operations to a module of three that the profile lacks.

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
    """The raw data of the target's device settings module as read; ``MISSING`` when it has none."""
    module = state.find_module("deviceSettings")
    return MISSING if module is None else module.raw_data


def _try_assigning(
    state: ConfigState,
    mod: AnyConfigurationModule,
    prepared: ProfileSegmentPatch,
    unserved: Collection[Path],
) -> None:
    """Try *prepared*, the patch to profile module *mod* and the assignments it needs, on copies of the three modules.

    The patch is merged into the copy of *mod* after the assignments, and
    must pass ``check_profile_segments``; each copy is then planned.
    Nothing of *state* changes, and whatever fails raises.
    """
    modules = segment_modules(state)
    name = str(mod.name)
    ds_as_read = _device_settings_as_read(state)
    before = _require_writable(mod).data

    def edit(copies: Mapping[str, AnyConfigurationModule]) -> None:
        after = _require_writable(copies[name]).data
        _ = after.merge_patch(prepared.patch, resolvers=state.resolvers)
        check_profile_segments(before, after, mod, ds_as_read, prepared.assignments)
        if isinstance(mod, QosConfigurationModule):
            state.fill_hub_services(after, skip=unserved)

    assign_on_trial(state, modules, prepared.assignments, edit)


type _WritableModule = (
    DeviceSettingsConfigurationModule
    | QosConfigurationModule
    | WanConfigurationModule
    | FirewallConfigurationModule
)

_WRITABLE_MODULES = (
    DeviceSettingsConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
    FirewallConfigurationModule,
)


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


def _dump(mod: AnyConfigurationModule) -> object:
    """The JSON form of *mod*, to find an edit made while it was written."""
    return mod.model_dump(mode="json", by_alias=True)


def _require_writable(mod: AnyConfigurationModule) -> _WritableModule:
    """*mod*, if it is a module a save writes; else ``TypeError``."""
    if not isinstance(mod, _WRITABLE_MODULES):
        raise TypeError(
            f"{mod.name}: this state has an edit to a {type(mod).__name__}, which a "
            "save does not write (it writes device settings, QoS, WAN and firewall "
            "modules). Load the state again to drop that edit; nothing was sent"
        )
    return mod


def _validation_error_to_warning(err: ValidatorError) -> ValidationWarning:
    """Convert a ValidationError to a ValidationWarning for load-time use."""
    return ValidationWarning(
        path=err.path,
        message=err.message,
        code=err.code,
        severity=err.severity,
    )


async def _write_module(
    client: VcoClientProtocol,
    module: _WritableModule,
    plan: ModulePlan,
    *,
    enterprise_id: int | None,
    if_absent: bool,
    rebase: bool,
    version_checked: bool,
) -> ModuleWrite[Any]:
    """``client.write_module`` with ``version_checked`` passed only when it is true: a save without steps calls it as it always did."""
    if version_checked:
        return await client.write_module(
            module,
            plan,
            enterprise_id=enterprise_id,
            if_absent=if_absent,
            rebase=rebase,
            version_checked=True,
        )
    return await client.write_module(
        module, plan, enterprise_id=enterprise_id, if_absent=if_absent, rebase=rebase
    )
