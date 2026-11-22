"""ConfigPipeline: orchestration layer for load → apply → save configuration workflow."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeIs, runtime_checkable

from pydantic import MISSING
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.enums.profiles import GetConfigurationWith
from pyvelo.client.exceptions import ModuleWriteError, UnboundError
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
from pyvelo.core import PatchError
from pyvelo.core.base import IdentityResolver, VcoModel
from pyvelo.core.identity import EdgeName, ProfileName, ResolutionError
from pyvelo.core.merge import nest_patch, select_items
from pyvelo.core.patch import ItemOp, is_new, parse_item_op, position_of
from pyvelo.core.rebase import Key, Path

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
        """The resolver registry used by this pipeline."""
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

        if isinstance(edge, str):
            # Resolve edge name to ID
            ctx = ResolverContext(enterprise_id=enterprise_id)
            self._resolvers.set_context(ctx)
            edge_resolver = self._resolvers.get_resolver(EdgeResolver)
            await edge_resolver.load(ctx)

            identity = await self._resolvers.resolve(EdgeName(edge))
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
        self._resolvers.set_context(resolver_context)

        await self._ensure_segment_resolver(resolver_context)
        await self._ensure_application_resolver(resolver_context)

        state = ConfigState(
            edge_config=edge_config,
            profile_config=profile_config,
            target_type="edge",
            resolver_context=resolver_context,
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
        """Load a profile configuration directly (no edge/template merge)."""
        profile_id: int

        if isinstance(profile, str):
            # Resolve profile name to ID
            ctx = ResolverContext(enterprise_id=enterprise_id)
            self._resolvers.set_context(ctx)
            profile_resolver = self._resolvers.get_resolver(ProfileResolver)
            await profile_resolver.load(ctx)

            identity = await self._resolvers.resolve(ProfileName(profile))
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
        self._resolvers.set_context(resolver_context)

        await self._ensure_segment_resolver(resolver_context)
        await self._ensure_application_resolver(resolver_context)

        state = ConfigState(
            edge_config=None,
            profile_config=profile_config,
            target_type="profile",
            resolver_context=resolver_context,
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

        With neither, *patch* must be a mapping (``TypeError`` otherwise).
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
           copied from the profile) and is inserted on save.
        2. On a profile target, refuse ``add`` and ``delete`` of a
           ``segments`` item, whatever the selector, ``"+"`` included
           (``_profile_segment_ops``): a segment belongs to all three of
           device settings, QoS and firewall, and adding one to a profile is
           not supported yet. A change or a ``move`` of a segment is fine.
           ``merge_patch`` called directly is not restricted.
        3. Merge the patch into a copy of the module's typed data, then into
           the data itself. On an edge target each merge is preceded by the
           segment rules (``_prepare_segments``). The edge layer has an entry
           for only some of the segments it inherits from its profile:

           - a change or a ``move`` of a segment the edge only inherits first
             adds its placeholder entry, which carries the profile's
             descriptor and IDs;
           - an ``add`` of a segment the edge inherits raises ``PatchError``
             (write a plain object to change it), and so does an ``add``
             under ``"+"`` whose body names one;
           - a ``delete`` of a segment the edge only inherits raises
             ``PatchError``: the layer has no entry to delete;
           - an edge entry the profile knows under another name (the same
             ``segmentId``) is that segment;
           - a segment neither layer has is left to ``merge_patch``; naming
             one segment twice raises ``PatchError``.
        4. Return the updated :class:`ConfigState`.

        All or nothing: a patch that raises (``PatchError``, or a resolver's
        ``ResolutionError`` or ``LookupError``) leaves the state, and
        ``plan()``, as they were: no placeholder entries, and a module this
        call created is removed.

        MISSING fields on the patch target are preserved as MISSING so callers
        downstream (reconciler, YAML export) can distinguish "unset" from
        "explicitly set".

        Raises ``ValueError`` for an unknown *module*.
        """
        internal_name = _resolve_module_name(module)
        existing = state.find_module(internal_name)
        mod = (
            existing
            if existing is not None
            else state.module(internal_name, create=True)
        )
        edge = state.target_type == "edge"
        try:
            data = mod.data
            if not isinstance(data, VcoModel):
                raise TypeError(
                    f"Module '{internal_name}' has unstructured data; merge_patch requires a typed module"
                )
            nested = nest_patch(type(data), patch, segment=segment, path=path)
            segments = nested.get("segments")
            if not edge and _is_object(segments) and isinstance(data, _SegmentLayer):
                _profile_segment_ops(segments, _SEGMENTS)
            trial = data.model_copy(deep=True)
            _ = trial.merge_patch(
                _prepare_segments(trial, nested, self._resolvers) if edge else nested,
                resolvers=self._resolvers,
            )
        except BaseException:
            if existing is None:
                state.drop_module(mod)
            raise
        _ = data.merge_patch(
            _prepare_segments(data, nested, self._resolvers) if edge else nested,
            resolvers=self._resolvers,
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

        1. Plan the write of every module (:meth:`ConfigState.plan`).
        2. Run write-time validators on the changed modules. Errors block save.
        3. Send each changed module, in order, as an update or an insert with
           ``returnData`` (``write_module``), and replace the state's module
           with the one the VCO stored (:meth:`ConfigState.replace_module`).
           ``SaveResult.server_changes`` holds, per module, what the VCO
           stored otherwise than sent, outside the plan's changes.

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
        """
        plan = state.plan()
        result = SaveResult(plan=plan)

        if not plan.changed:
            return result

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

        A ``ModuleWriteError`` puts the module as stored in *state* too, when
        it carries one, and gets the writes before it as ``completed``. Any
        other error gets a note that names them, if there are any.
        """
        enterprise_id = state.resolver_context.enterprise_id
        done: list[ModuleWrite[Any]] = []
        for module_plan in plan.changed:
            mod = _require_writable(state.module(module_plan.name))
            try:
                write = await self.client.write_module(
                    mod, module_plan, enterprise_id=enterprise_id
                )
            except ModuleWriteError as error:
                stored = error.module
                # Of the class of mod (write_module checks it), or None when
                # the module could not be read: then the state keeps mod.
                if isinstance(stored, type(mod)):
                    state.replace_module(mod, stored)
                error.completed = tuple(done)
                raise
            except Exception as error:
                if done:
                    error.add_note(
                        "written before this failure: "
                        + ", ".join(f"{w.name} ({w.operation})" for w in done)
                    )
                raise
            state.replace_module(mod, write.module)
            done.append(write)
            name = ConfigurationModuleName(write.name)
            result.server_changes[name] = write.server_changes
        return result

    def validate(self, state: ConfigState) -> ValidationResult:
        """Run all write-time validators without saving (dry-run validation)."""
        return self._validators.run(state, phase="write")

    async def load_resolvers(self, context: ResolverContext) -> None:
        """Ensure segment and application resolvers are loaded for the given context.

        Called automatically by load() and load_profile(). Call explicitly when
        bypassing those methods, e.g. when loading config state from a cache.
        """
        await self._ensure_segment_resolver(context)
        await self._ensure_application_resolver(context)

    async def _ensure_segment_resolver(self, context: ResolverContext) -> None:
        """Ensure segment resolver is registered and loaded from VCO API."""
        try:
            resolver = self._resolvers.get_resolver(SegmentResolver)
        except LookupError:
            resolver = SegmentResolver(self.client)
            self._resolvers.register(resolver)
        await resolver.load(context)

    async def _ensure_application_resolver(self, context: ResolverContext) -> None:
        """Ensure application resolver is loaded if registered."""
        try:
            resolver = self._resolvers.get_resolver(ApplicationResolver)
            await resolver.load(context)
        except LookupError:
            pass


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


def _prepare_segments(
    data: VcoModel, patch: Mapping[str, object], resolvers: IdentityResolver | None
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
            key, name = _prepare_segment(data, selector, op, where, resolvers)
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
) -> tuple[object, str | None]:
    """The selector to merge one segment entry under, and the segment it names (``_prepare_segments``).

    The segment is None where the entry is left to ``merge_patch`` without
    naming one, and for ``"+"``.
    """
    assert isinstance(data, _SegmentLayer)  # _prepare_segments checked
    new = is_new(selector)
    lookup: object = selector
    if new:
        given = _added_segment_name(op) if op.kind == "add" else None
        if given is None:
            return selector, None
        lookup = given
    name = _layer_segment_name(data, lookup, resolvers)
    if name is not None:
        return selector, None if new else name
    try:
        name = data.profile_segment_name(lookup)
    except UnboundError:
        return selector, None
    if name is None:
        return selector, None
    entry = _layer_segment_name(data, name, None)
    if entry is None:
        # The layer may have the profile's segment under another name.
        segment_id = data.profile_segment_id(lookup)
        if segment_id is not None:
            entry = _layer_segment_name(data, str(segment_id), None)
    if entry is not None:
        # The layer has it, under a name the selector does not give (another
        # case, or a name of its own): the patch is for that entry.
        if not new:
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
    _ = data.segment(name)
    return name, name


def _layer_segment_name(
    data: VcoModel, selector: object, resolvers: IdentityResolver | None
) -> str | None:
    """The name of the entry of the layer's ``segments`` that *selector* names, or None.

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
    return data.segments[positions[0]].segment.name


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


def _require_writable(mod: AnyConfigurationModule) -> _WritableModule:
    """*mod*, if it is a module a save writes; else ``TypeError``."""
    if not isinstance(
        mod,
        DeviceSettingsConfigurationModule
        | QosConfigurationModule
        | WanConfigurationModule
        | FirewallConfigurationModule,
    ):
        raise TypeError(f"Cannot update module type: {type(mod).__name__}")
    return mod


def _validation_error_to_warning(err: ValidatorError) -> ValidationWarning:
    """Convert a ValidationError to a ValidationWarning for load-time use."""
    return ValidationWarning(
        path=err.path,
        message=err.message,
        code=err.code,
        severity=err.severity,
    )
