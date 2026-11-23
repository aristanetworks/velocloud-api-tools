"""ConfigPipeline: orchestration layer for load → apply → save configuration workflow."""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeIs, runtime_checkable

from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import (
    DetachedObjectError,
    ModuleWriteError,
    UnboundError,
)
from pyvelo.client.module_check import ModuleWrite
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
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.validation import ValidationError as ValidatorError
from pyvelo.config.validation import ValidationResult, ValidatorRegistry
from pyvelo.core import ConfigContext, PatchError
from pyvelo.core.base import IdentityResolver, VcoModel
from pyvelo.core.identity import EdgeName, ProfileName, ResolutionError
from pyvelo.core.merge import nest_patch, select_items
from pyvelo.core.patch import ItemOp, is_new, parse_item_op, position_of
from pyvelo.core.rebase import Key, Path, raw_changes

from pyvelo_intent.config.module_names import (
    resolve_module_name as _resolve_module_name,
)
from pyvelo_intent.config.state import (
    ConfigPlan,
    ConfigState,
    SaveResult,
    ValidationWarning,
)


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
        self._validators: ValidatorRegistry = validator_registry or ValidatorRegistry()

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
            edge_resolver = resolvers.get_resolver(EdgeResolver)
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
            profile_resolver = resolvers.get_resolver(ProfileResolver)
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
        2. On a profile target, refuse ``add`` and ``delete`` of a
           ``segments`` item, whatever the selector, ``"+"`` included
           (``_profile_segment_ops``): a segment belongs to all three of
           device settings, QoS and firewall, and adding one to a profile is
           not supported yet. A change or a ``move`` of a segment is fine.
           The same holds for any other form of patch: a list value for
           ``segments`` that leaves a segment out or brings a new one, and a
           change to a segment's descriptor, raise ``PatchError`` at
           ``segments``. A list value that keeps every descriptor is taken.
           ``merge_patch`` called directly is not restricted.
        3. Merge the patch into a copy of the module's typed data, then into
           the data itself. On an edge target each merge is preceded by the
           segment rules (``_prepare_segments``). The edge layer has an entry
           for only some of the segments it inherits from its profile:

           - a change of a segment the edge only inherits first adds its
             placeholder entry, which carries the profile's descriptor and
             IDs;
           - a ``_position`` (``{"_op": "move", ...}`` (``move(...)``), or on a
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

        Raises ``ValueError`` for an unknown *module*.
        """
        async with state.lock:
            internal_name = _resolve_module_name(module)
            existing = state.find_module(internal_name)
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
                        "patch to it is not applied",
                        Path(()),
                    )
                data = mod.data
                nested = nest_patch(type(data), patch, segment=segment, path=path)
                segments = nested.get("segments")
                if (
                    not edge
                    and _is_object(segments)
                    and isinstance(data, _SegmentLayer)
                ):
                    _profile_segment_ops(segments, _SEGMENTS)
                inherited = state.inherited_segment_ids(mod) if edge else frozenset()
                trial = data.model_copy(deep=True)
                _ = trial.merge_patch(
                    _prepare_segments(trial, nested, state.resolvers, inherited)
                    if edge
                    else nested,
                    resolvers=state.resolvers,
                )
                if not edge and isinstance(trial, _SegmentLayer):
                    _profile_segments_unchanged(data, trial)
                _ = state.plan_module(mod, trial)
            except BaseException:
                if existing is None:
                    state.drop_module(mod)
                raise
            _ = data.merge_patch(
                _prepare_segments(data, nested, state.resolvers, inherited)
                if edge
                else nested,
                resolvers=state.resolvers,
            )
            return state

    async def save(
        self,
        state: ConfigState,
        *,
        dry_run: bool = False,
        validate: bool = True,
    ) -> SaveResult:
        """Persist changes to VCO.

        1. Plan the write of every module (:meth:`ConfigState.plan`), and
           raise ``TypeError`` if a changed module is not one a save writes
           (device settings, QoS, WAN, firewall), also for ``dry_run``.
        2. Run write-time validators on the changed modules. Errors block save.
        3. Send each changed module, in order, as an update or an insert with
           ``returnData`` (``write_module``), and replace the state's module
           with the one the VCO stored (:meth:`ConfigState.replace_module`).
           Per module, ``SaveResult.left_out`` holds the planned changes the
           VCO stores its own way (a field it owns, a default it adds back
           where the plan removed it, the removal of a row of an append-only
           ref): neither applied nor lost, each with the value read, the
           value sent and the value stored. ``SaveResult.server_changes``
           holds what the VCO stored otherwise than sent, at paths that are
           not lost edits: what it or another writer changed beside the
           plan, an ID it issued, an IPv6 address in canonical form, the
           value it stored at a left-out change. A value stored as it was
           read is not listed there; a left-out change kept so is in
           ``left_out``. In a list compared whole (a keyed list read with an
           item that has no key) nothing is left out: a value stored at an
           owned field otherwise than sent is a server change with
           ``as_read`` ``MISSING``.

        A later :meth:`ConfigState.plan` is empty, and the state takes more
        edits and saves. Objects taken from the state before the save belong
        to the replaced modules and are detached: an edit through one raises
        ``DetachedObjectError``.

        After ``ConfigConflictError`` or ``WriteNotAppliedError`` the state
        holds the module as stored, when the error carries it
        (``error.module``): redo the edits in ``error.lost_edits`` and save
        again. ``error.completed`` are the writes of this save before it. Any
        other error of a request leaves that module and the later ones with
        their edits, and carries a note that names the modules written before
        it. ``dry_run`` stops after step 2 and sends nothing.

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
            plan = state.plan()
            result = SaveResult(plan=plan)

            if not plan.changed:
                return result

            for module_plan in plan.changed:
                _ = _require_writable(state.module(module_plan.name))

            # Run write-time validators
            if validate:
                validation = self._validators.run(
                    state,
                    phase="write",
                    modules=[ConfigurationModuleName(m.name) for m in plan.changed],
                )
                if not validation.is_valid:
                    result.validation_errors = validation.errors
                    return result
                for warn in validation.warnings:
                    result.warnings.append(_validation_error_to_warning(warn))

            for module_plan in plan.changed:
                name = ConfigurationModuleName(module_plan.name)
                if module_plan.operation == "insert":
                    result.modules_inserted.append(name)
                else:
                    result.modules_updated.append(name)

            if dry_run:
                return result
            return await self._send(state, plan, result)

    async def _send(
        self, state: ConfigState, plan: ConfigPlan, result: SaveResult
    ) -> SaveResult:
        """Write each changed module of *plan*, in order, and put the module as stored in *state*.

        Raises ``TypeError`` before the first request when a changed module is
        not one a save writes. A ``ModuleWriteError`` puts the module as
        stored in *state* too, when it carries one, and gets the writes before
        it as ``completed``. Any other error gets a note that names them, if
        there are any. So does the ``DetachedObjectError`` of an edit made
        while a module was written: it stops the save once that module is
        stored, and its note also names the modules not written.
        """
        enterprise_id = state.resolver_context.enterprise_id
        # Each write replaces only its own module, so these stay the state's.
        modules = [_require_writable(state.module(p.name)) for p in plan.changed]
        # What each module holds as planned, to find an edit made while a write was out.
        dumps = [_dump(mod) for mod in modules]
        done: list[ModuleWrite[Any]] = []
        for i, (module_plan, mod, planned) in enumerate(
            zip(plan.changed, modules, dumps, strict=True)
        ):
            try:
                write = await self.client.write_module(
                    mod, module_plan, enterprise_id=enterprise_id
                )
            except ModuleWriteError as error:
                stored = error.module
                # Of the class of mod (write_module checks it), or None when
                # the module could not be read: then the state keeps mod. It
                # is also kept when the state no longer holds mod, so that the
                # write error is not hidden behind the ValueError of replace_module.
                if isinstance(stored, type(mod)) and state.find_module(mod.name) is mod:
                    state.replace_module(mod, stored)
                error.completed = tuple(done)
                raise
            except Exception as error:
                if done:
                    error.add_note(_written_note(done))
                raise
            paths = list(
                dict.fromkeys(
                    change.path
                    for change in raw_changes(planned, _dump(mod), type(mod))
                )
            )
            state.replace_module(mod, write.module)
            done.append(write)
            name = ConfigurationModuleName(write.name)
            result.server_changes[name] = write.server_changes
            result.left_out[name] = write.left_out
            if paths:
                error = DetachedObjectError(
                    f"{mod.name}: the module was edited while this save was writing it, at: "
                    + f"{', '.join(paths)}. The write is stored without those edits and the "
                    + "state holds the module as stored; take the objects from the state again "
                    + "and redo them. To edit a state from another task while a save may run, "
                    + "use apply, or hold state.lock around the edit",
                    paths,
                )
                note = _written_note(done)
                if later := modules[i + 1 :]:
                    note += (
                        "; the save stopped here, so these are not written: "
                        + ", ".join(m.name for m in later)
                        + ". The next save writes them"
                    )
                error.add_note(note)
                raise error
        return result

    def validate(self, state: ConfigState) -> ValidationResult:
        """Run all write-time validators without saving (dry-run validation)."""
        return self._validators.run(state, phase="write")

    async def load_resolvers(self, state: ConfigState) -> None:
        """Give *state* resolvers of its own, loaded for its enterprise.

        ``load`` and ``load_profile`` already do this. Call it for a state
        built another way, such as from a cache: the state gets an unloaded
        copy of the pipeline's resolvers, loaded for ``state.resolver_context``.
        """
        resolvers = self._resolvers.unloaded()
        await self._load_state_resolvers(resolvers, state.resolver_context)
        state.resolvers = resolvers

    async def _load_state_resolvers(
        self, resolvers: ResolverRegistry, context: ResolverContext
    ) -> None:
        """Set *context* on *resolvers*, then load the segment resolver, adding
        one if there is none, and the application resolver if there is one."""
        resolvers.set_context(context)
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


def build_default_pipeline(client: VcoClientProtocol) -> ConfigPipeline:
    """Build a ConfigPipeline with the default resolvers.

    This is the standard bootstrap for intent operations: registers
    EdgeResolver and ProfileResolver.
    """
    resolvers = ResolverRegistry()
    resolvers.register(EdgeResolver(client))
    resolvers.register(ProfileResolver(client))

    return ConfigPipeline(client, resolvers=resolvers)


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


_SEGMENTS = Path(("segments",))


def _is_object(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def _entry_path(path: Path, selector: object) -> Path:
    """Where the entry under *selector* of a ``segments`` object is in the patch.

    The place ``merge_patch`` reports it at: a position for ``"#n"``, else the
    selector in string form.
    """
    n = position_of(selector)
    return path.child(n if n is not None else Key(str(selector)))


def _profile_segment_ops(segments: Mapping[object, object], path: Path) -> None:
    """Refuse an ``add`` or a ``delete`` of a segment of a profile through a patch.

    *segments* is the ``segments`` object of a patch for one profile module
    and *path* where it is. A segment is a profile's in all three of device
    settings, QoS and firewall, and a patch reaches one, so neither is
    supported yet; whatever the selector, ``"+"`` included. A change and a
    ``move`` are left as they are. Raises ``PatchError``; changes nothing.
    """
    for selector, value in segments.items():
        if not _is_object(value):
            continue
        where = _entry_path(path, selector)
        op = parse_item_op(value, where)
        if op.kind == "add":
            raise PatchError(
                "adding a segment to a profile through a patch is not supported yet",
                where,
            )
        if op.kind == "delete":
            raise PatchError(
                "removing a segment from a profile is not supported", where
            )


def _segment_descriptors(layer: object) -> list[dict[str, object]]:
    """The wire form of the ``segment`` object of each entry of *layer*, in order."""
    assert isinstance(layer, VcoModel)  # a profile module's data
    dumped = layer.model_dump(mode="json", by_alias=True)
    return [entry["segment"] for entry in dumped["segments"]]


def _canonical(descriptor: Mapping[str, object]) -> str:
    return json.dumps(descriptor, sort_keys=True)


def _profile_segments_unchanged(data: object, trial: object) -> None:
    """Refuse a patch after which a profile module has other segments than before.

    *trial* is a copy of *data* with the patch merged in. Whatever the form of
    the patch (an item operation, a list value, an object that rewrites a
    descriptor), the descriptors of *trial* must be those of *data*, in any
    order: a ``move`` is fine. Raises ``PatchError`` at ``segments``; changes
    nothing.
    """
    before = _segment_descriptors(data)
    after = _segment_descriptors(trial)
    if sorted(map(_canonical, before)) == sorted(map(_canonical, after)):
        return
    names_before = {str(d.get("name")) for d in before}
    names_after = {str(d.get("name")) for d in after}
    parts: list[str] = []
    if removed := sorted(names_before - names_after):
        parts.append("it removes " + ", ".join(removed))
    if added := sorted(names_after - names_before):
        parts.append("it adds " + ", ".join(added))
    detail = "; ".join(parts) or "it changes a segment's descriptor"
    raise PatchError(
        "a patch does not add, remove or re-describe a segment of a profile ("
        + detail
        + "): a segment belongs to device settings, QoS and firewall together, "
        + 'so neither a list value for "segments", nor {"_op": "add", ...} '
        + '(`add(...)`) or {"_op": "delete"} (`delete()`) under a selector, '
        + "nor a rewritten descriptor can change which segments a profile has. "
        + "Adding or removing a segment of a profile through a patch is not supported yet. "
        + 'To change what a segment holds, name it: {"segments": {"<name>": {...}}}',
        _SEGMENTS,
    )


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
      is keyed under the profile's name; an ``add`` or a ``delete`` raises
      ``PatchError``, and adds nothing;
    - one that names a segment neither layer has, or any on data that is not
      bound to a profile, is left to ``merge_patch``.

    *inherited* holds the ``segmentId`` (in string form) of each segment the
    edge module did not have when it was read
    (``ConfigState.inherited_segment_ids``). A save writes those after the
    edge's own, in the profile's order, so a ``_position`` for one of them
    (``{"_op": "move", ...}`` (``move(...)``), or on a change) raises
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
            where = _entry_path(_SEGMENTS, selector)
            op = parse_item_op(value, where)
            key, name = _prepare_segment(
                data, selector, op, where, resolvers, inherited
            )
            if name is not None:
                if name in named:
                    raise PatchError(
                        f"{named[name]} names segment {name!r} too: one selector per segment",
                        where,
                    )
                named[name] = where
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
    naming one, and for ``"+"``. Raises ``PatchError`` for a place the edge
    cannot store: one for a segment in *inherited*, or one given ``before`` or
    ``after`` such a segment. This comes before a placeholder is added.
    """
    assert isinstance(data, _SegmentLayer)  # _prepare_segments checked
    _refuse_place_by_inherited(data, op, where, resolvers, inherited)
    new = is_new(selector)
    lookup: object = selector
    if new:
        given = _added_segment_name(op) if op.kind == "add" else None
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
            f"segment {name!r} is already in the edge layer, as {entry!r}", where
        )
    if op.kind == "add":
        raise PatchError(
            f"the edge has segment {name!r} by inheritance from its profile: "
            + "write a plain object to change it, not add()",
            where,
        )
    if op.kind == "delete":
        raise PatchError(
            f"the edge layer has no entry to delete for segment {name!r}, which "
            + "it inherits from its profile",
            where,
        )
    _refuse_place_for(op, name, segment_id, where, inherited)
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
        + "after the edge's own, in the profile's order, so a place for it "
        + 'cannot be stored: leave out "_position" (move())',
        where,
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
        + "order, so a place before or after it cannot be stored: give "
        + '"first", "last", or one of the edge\'s own segments in "_position"',
        where,
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


def _added_segment_name(op: ItemOp) -> str | None:
    """The segment name the body of an ``add`` gives at ``segment.name``, or None.

    That is the key path of every ``segments`` list, and neither ``segment``
    nor ``name`` has an alias or a camelCase form of its own, so these are
    the only keys ``merge_patch`` reads the name under.
    """
    descriptor = op.body.get("segment")
    name = descriptor.get("name") if _is_object(descriptor) else None
    return None if name is None else str(name)


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


def _written_note(done: Sequence[ModuleWrite[Any]]) -> str:
    """The note of an error that stops a save: the modules it wrote before."""
    return "written before this failure: " + ", ".join(
        f"{w.name} ({w.operation})" for w in done
    )


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
