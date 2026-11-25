"""ConfigState: holds edge/profile configuration with snapshot for diff-on-save."""

import asyncio
import contextlib
import copy
import json
import weakref
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import (
    Any,
    Literal,
    Protocol,
    cast,
    override,
    runtime_checkable,
)

from pydantic import MISSING, BaseModel

from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    DanglingReferenceError,
    DetachedObjectError,
    SegmentSetError,
)
from pyvelo.client.hub_services import HubServiceTable, fill_services
from pyvelo.client.models.configuration.common import segment_id_of
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.profile_segments import AddedSegment
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.ref_owners import (
    ref_only_values,
    segment_object_ids,
)
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.module_check import ModuleWrite
from pyvelo.client.module_write import (
    ModulePlan,
    missing_segments,
    plan_module,
    read_data,
)
from pyvelo.client.schemas.configuration import (
    AnyStoredModule,
    ConfigurationBase,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
    new_edge_module,
)
from pyvelo.config.errors import LayerNotSavedError, SaveValidationError
from pyvelo.config.hub_steps import (
    HubStep,
    changed_segment_ids,
    check_hub_lists,
    derive_steps,
    role_disagreements,
)
from pyvelo.config.module_names import resolve_module_name
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.validation import ValidationError
from pyvelo.config.validation_warning import ValidationWarning
from pyvelo.core import ConfigContext, Layered, VcoModel
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.effective import effective_view
from pyvelo.core.identity import (
    HubIdentity,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.core.layers import Edge, Effective, Profile
from pyvelo.core.rebase import (
    ItemMove,
    ItemPlace,
    Key,
    Path,
    python_path,
    raw_changes,
)
from pyvelo.core.references import (
    can_provide,
    check_references,
    provided,
    reference_text,
    union,
)
from pyvelo.core.response_check import PlannedChange, ServerChange

_DEVICE_SETTINGS = ConfigurationModuleName.DEVICE_SETTINGS.value
_SEGMENT_MODULES = (
    _DEVICE_SETTINGS,
    ConfigurationModuleName.QOS.value,
    ConfigurationModuleName.FIREWALL.value,
)
"""The three modules of a profile that hold its segments, in write order."""


@dataclass(frozen=True)
class ConfigFieldChange:
    """One change of :meth:`ConfigState.plan`: its module, and its path in python names.

    Attributes:
        module: The module's VCO name (e.g. ``"firewall"``, ``"WAN"``).
        path: Where the change is, in the names a patch uses
            (``segments["Global Segment"].outbound[block-ssh].name``). A
            provider change is at the provider's field
            (``segments["Global Segment"].dns.primary_provider.service``).
        before: The value as read, a JSON value, or ``MISSING`` where there
            was none. For a keyed list whose items change place, the item
            names in the order read. For a provider (a field whose value is
            a ref row), the service's logical ID as lower-case text, or None
            for no provider.
        after: The value as it is now, a JSON value, or ``MISSING`` where
            there is none; the item names in the new order for a reorder.
            For a provider, as ``before``.
        place: Where an item added to a keyed list goes, else None.
        moves: For the order of a keyed list, each item that changes place
            with its new place, else None.
    """

    module: str
    path: Path
    before: object
    after: object
    place: ItemPlace | None = None
    moves: tuple[ItemMove, ...] | None = None


@dataclass(frozen=True)
class ConfigPlan:
    """What a save of a whole ConfigState writes: one plan per module.

    render() is the text to show before a save. Its first line names what is
    written: "edge 101, enterprise 1, configuration 10" or
    'profile "Branch", enterprise 1, configuration 20' (an ID that is not known
    is left out). Then, for each module of the target, its name and what the
    save does with it (none, update or insert), followed by a line for each
    change:

    - "+ path: value" is a key or an item that is added. An item added to a
      keyed list has its place after the path: "(first)" or "(after other)".
    - "- path" is a key or an item that is removed.
    - "~ path: old -> new" is a value that changes.
    - "~ path: order: item first; item after other" is a keyed list whose items
      change order: the items that move, each with its new place.
    - "+ refs name (segment): value" and "- refs name (segment): value" are
      a ref row the edit adds or removes, with no segment for a row of the
      module; "refs: sent as read" says the module's refs are sent with no
      row changed.
    - "warning: text" is something about the module's refs as read that the
      plan leaves as it is. ``warnings`` holds them all as data; ``render()``
      shows each once, in its module's block.
    - "reconciled segments: A, B" are the profile's segments the write adds to
      the edge module as empty entries; with "none" the line reads "missing
      segments".
    - "added segments: A, B" are the segments added to a profile module
      (``add_segment_entry``), which are no change from it; "repaired
      defaults: Guest: A, B" are the QoS ``defaults`` entries of a profile
      segment that the write adds from the template, and "missing defaults"
      the same entries when the plan leaves them out.
    - "override turned on: path (...)" is a section or an item of an edge whose
      override flag the changes turn on: from this save on the edge's own
      values apply there, and the profile's no longer do.
    - "legacy keys (dropped when the module is written): ..." are keys the VCO
      does not know, on a module with nothing else to write.
    - "warning: text", last and with no indent, is a segment of a profile that
      its three modules disagree about as read (``segment_warnings``), or a
      reference that names nothing as it was read and the save leaves, or a
      hub whose roles as read do not match the lists it is in
      (``check_warnings``).

    A profile whose edit adds a hub to a segment's hub, bridge or backhaul
    list, or removes one, has the role calls the save makes before it writes
    the device settings (``steps``). ``render()`` shows them first, after the target
    line, as a ``steps:`` line and a line for each, in the order they are
    made:

        steps:
          disable backhaul for Hub-B on segment Global Segment
          enable hub for Hub-A on segment Global Segment

    A profile's device settings module is written first, then the others in
    the order the configuration lists them, and ``modules`` is in that order.

    Paths are in the names the VCO uses. A value is JSON, cut to 80 characters;
    the two values of a "~" line are cut where they first differ. A secret
    shows as ``"***"``.

    Attributes:
        modules: The plan of each module of the target.
        target: What the plan is for (the first line of ``render()``), or "".
        warnings: What each module's plan warns of, one entry per warning,
            with the module's name before the warning's path
            (``"deviceSettings: refs deviceSettings:dns:primaryProvider"``),
            then ``segment_warnings``, then ``check_warnings``.
        segment_warnings: The segments of a profile that its three modules
            disagree about as read, which the edit does not settle: code
            ``segments_disagree``, at ``segments["<name>"]``. Empty for an edge.
        check_warnings: What the cross-module checks warn of and the save
            leaves as it is, at ``"<module>: <path>"``: the references that
            name nothing in the module as read and still name nothing (code
            ``reference_kept``), and the hubs whose roles as read do not match
            the lists they are in (``hub_roles_disagree``).
        steps: The role calls a hub added to or removed from a segment's hub
            lists needs, in the order they are made (``derive_steps``); empty
            for an edge, and for an edit that leaves the lists' members. A list
            change is also a change of the device settings module, so
            ``changed`` is not made longer by them.
    """

    modules: tuple[ModulePlan, ...]
    target: str = ""
    segment_warnings: tuple[ValidationWarning, ...] = ()
    check_warnings: tuple[ValidationWarning, ...] = ()
    steps: tuple[HubStep, ...] = ()

    @property
    def changed(self) -> tuple[ModulePlan, ...]:
        """The plans that write something."""
        return tuple(m for m in self.modules if m.operation != "none")

    @property
    def warnings(self) -> tuple[ValidationWarning, ...]:
        """The warnings of every module's plan, then the segment and check warnings, as data; ``render()`` shows them in their blocks."""
        return (
            *(
                ValidationWarning(
                    path=f"{module.name}: {warning.path}",
                    message=warning.message,
                    code=warning.code,
                )
                for module in self.modules
                for warning in module.warnings
            ),
            *self.segment_warnings,
            *self.check_warnings,
        )

    def render(self) -> str:
        """The plan as text to show before a save, in the format above."""
        lines = [m.render() for m in self.modules]
        if self.steps:
            lines = ["steps:", *(f"  {step.render()}" for step in self.steps), *lines]
        lines.extend(
            f"warning: {w.message}"
            for w in (*self.segment_warnings, *self.check_warnings)
        )
        return "\n".join([self.target, *lines] if self.target else lines)


type _AnyConfiguration = ConfigurationBase[Any]
"""A configuration of either stored layer, for the helpers that read both."""


class LayerView[X: (Edge, Profile)]:
    """Read-only access to the data of one config layer's modules.

    Each property is the module's ``data``, ready to read or edit in place. An
    edit is saved only through the state's target layer (``state.edge`` of an
    edge state, ``state.profile`` of a profile state); an edit of the other
    layer is reported by the next ``ConfigState.plan``. *profile* is the
    profile layer an edge layer inherits from; a module this view creates is
    bound to it.
    """

    def __init__(
        self,
        config: ConfigurationBase[X],
        profile: ConfigurationBase[Profile] | None = None,
    ) -> None:
        """A view of *config*'s modules, binding a module it creates to *profile*."""
        self._config: ConfigurationBase[X] = config
        self._profile: ConfigurationBase[Profile] | None = profile

    @property
    def firewall(self) -> FirewallData[X]:
        """The firewall data; an edge layer that has none starts it empty."""
        module = _module_of(
            self._config,
            ConfigurationModuleName.FIREWALL,
            create=True,
            profile=self._profile,
        )
        assert isinstance(module, FirewallConfigurationModule)
        return cast("FirewallData[X]", module.data)  # the module is of this layer

    @property
    def qos(self) -> QosData[X]:
        """The QoS data; an edge layer that has none starts it empty."""
        module = _module_of(
            self._config,
            ConfigurationModuleName.QOS,
            create=True,
            profile=self._profile,
        )
        assert isinstance(module, QosConfigurationModule)
        return cast("QosData[X]", module.data)  # the module is of this layer

    @property
    def device_settings(self) -> DeviceSettingsData:
        """The device settings data; a layer that has none raises ``ValueError``."""
        module = _module_of(
            self._config, ConfigurationModuleName.DEVICE_SETTINGS, create=False
        )
        assert isinstance(module, DeviceSettingsConfigurationModule)
        return module.data

    @property
    def wan(self) -> WanData:
        """The WAN data; a layer that has none raises ``ValueError``."""
        module = _module_of(self._config, ConfigurationModuleName.WAN, create=False)
        assert isinstance(module, WanConfigurationModule)
        return module.data


class EffectiveView:
    """Each module's effective data: what an edge uses, with where each value comes from.

    Computed from the edge layer and the profile layer each time a property is
    read, so it follows every edit. A value is a ``Resolved`` (its origin, the
    value, and where in that layer it sits); see ``pyvelo.core.effective``.
    Nothing is created: an edge that has no row for a module has the view of
    the profile's.
    """

    def __init__(
        self, edge: ConfigurationBase[Edge], profile: ConfigurationBase[Profile]
    ) -> None:
        """A view of the *edge* layer over the *profile* layer it inherits from."""
        self._edge: ConfigurationBase[Edge] = edge
        self._profile: ConfigurationBase[Profile] = profile

    @property
    def firewall(self) -> FirewallData[Effective]:
        """The firewall's effective data: the edge's local values over the profile's, then the display defaults."""
        return self._view(
            ConfigurationModuleName.FIREWALL,
            FirewallData[Profile],
            FirewallData[Effective],
        )

    @property
    def qos(self) -> QosData[Effective]:
        """The QoS effective data: the edge's local values over the profile's, then the display defaults."""
        return self._view(
            ConfigurationModuleName.QOS, QosData[Profile], QosData[Effective]
        )

    def _view[M: Layered[Effective]](
        self,
        name: ConfigurationModuleName,
        profile_class: type[Layered[Profile]],
        effective_class: type[M],
    ) -> M:
        """The module *name* at the effective layer; neither layer's module is created.

        A profile that has no such module is *profile_class* with no segments.
        """
        edge_module = _find_module(self._edge, name)
        profile_module = _find_module(self._profile, name)
        edge_data = None
        if edge_module is not None:
            edge_data = cast("Layered[Edge]", cast("object", edge_module.data))
        if profile_module is None:
            profile_data = profile_class.model_validate({"segments": []})
        else:
            profile_data = cast("Layered[Profile]", cast("object", profile_module.data))
        return effective_view(edge_data, profile_data, effective_class)


type _Place = tuple[str | int, ...]
"""Where a model is in its module's dump: dump keys and list positions (``mark_detached``)."""


@dataclass
class _Replaced:
    """A module ``replace_module`` took out, kept to find in-place edits of its objects.

    ``dump`` is the module's dump when it was replaced, or when an in-place
    edit was last found; ``nodes`` are the models inside it, held weakly, each
    with its place in ``dump``, which ``dump`` always holds. Nothing here keeps
    the module or its objects alive.

    ``refs`` are the module's ``RefOnly`` values then (``ref_only_values``),
    which a dump leaves out, and ``root`` is its data, held weakly, to read
    them again.
    """

    name: str
    model: type[VcoModel]
    dump: object
    nodes: list[tuple[_Place, weakref.ref[BaseModel]]]
    refs: dict[Path, object]
    root: weakref.ref[VcoModel] | None

    def changed_paths(self) -> list[Path]:
        """Where the objects still alive were changed in place since ``dump``, each once.

        A ``RefOnly`` value of the data, if it is alive, that is not the one
        recorded is a change too, at its path under ``data``. Drops the nodes
        no longer alive. A node inside another live node is in
        that one's dump, so only the outermost live nodes are dumped: put at
        their places in ``dump``, they make the module as it is now, and
        ``raw_changes`` against ``dump`` gives the paths, from the module, with
        keyed items named by key. When there are some, that is the new
        ``dump``, so each change is found once; a node below that the edit
        moved from its place there is no longer compared.
        """
        paths = self._changed_dump_paths()
        root = None if self.root is None else self.root()
        if root is not None:
            now = ref_only_values(root)
            moved = ref_only_changes(self.refs, now)
            if moved:
                self.refs = now
                paths.extend(Path(("data", *path.parts)) for path in moved)
        return paths

    def _changed_dump_paths(self) -> list[Path]:
        alive = [
            (place, ref, node)
            for place, ref in self.nodes
            if (node := ref()) is not None
        ]
        self.nodes = [(place, ref) for place, ref, _ in alive]
        if not alive:
            return []
        places = {place for place, _, _ in alive}
        outermost = {
            place: _dump(node)
            for place, _, node in alive
            if not any(place[:i] in places for i in range(len(place)))
        }
        now = self.dump
        for place, value in outermost.items():
            now = _put(now, place, value)
        changes = raw_changes(self.dump, now, self.model)
        if not changes:
            return []
        self.dump = now
        self.nodes = [
            (place, ref)
            for place, ref, node in alive
            if place in outermost or _at(now, place) == _dump(node)
        ]
        return list(dict.fromkeys(change.path for change in changes))


def _dump(model: BaseModel) -> object:
    return model.model_dump(mode="json", by_alias=True)


def ref_only_changes(
    before: Mapping[Path, object], after: Mapping[Path, object]
) -> list[Path]:
    """The paths whose ``RefOnly`` value differs between *before* and *after*, in python names.

    A dump leaves these values out, so a comparison of dumps does not see
    them. A path only one side has (a segment added or removed) counts as
    ``None`` on the other: a provider is then a change, no provider is not.
    """
    return [
        path
        for path in dict.fromkeys([*before, *after])
        if before.get(path) != after.get(path)
    ]


def _at(doc: object, place: _Place) -> object:
    """The value at *place* of the JSON *doc*, or ``MISSING`` where there is none."""
    for part in place:
        if isinstance(part, int):
            if not isinstance(doc, list):
                return MISSING
            items = cast("list[object]", doc)
            if part >= len(items):
                return MISSING
            doc = items[part]
        else:
            if not isinstance(doc, dict):
                return MISSING
            entries = cast("dict[str, object]", doc)
            if part not in entries:
                return MISSING
            doc = entries[part]
    return doc


def _put(doc: object, place: _Place, value: object) -> object:
    """The JSON *doc* with *value* at *place*: the containers on the way are copied."""
    if not place:
        return value
    part, rest = place[0], place[1:]
    if isinstance(part, int):
        items = list(cast("list[object]", doc))
        items[part] = _put(items[part], rest, value)
        return items
    entries = dict(cast("dict[str, object]", doc))
    entries[part] = _put(entries[part], rest, value)
    return entries


class StateLock(asyncio.Lock):
    """An ``asyncio.Lock`` that knows the task that holds it.

    A task that holds the lock and asks for it again would wait for itself
    without end. Here ``acquire`` raises ``RuntimeError`` at once instead, so
    ``ConfigPipeline.apply`` and ``save``, which take the lock themselves, fail
    loudly when called inside an ``async with state.lock:`` block.

    The check sees only the calling task. A task that holds the lock and waits,
    inside the block, for another task that calls ``apply`` or ``save`` still
    deadlocks: that task waits for the lock, and nothing here can tell.
    """

    _holder: asyncio.Task[object] | None = None

    @override
    async def acquire(self) -> Literal[True]:
        task = asyncio.current_task()
        if task is not None and task is self._holder:
            raise RuntimeError(
                "this task already holds the lock of this state. apply and save take "
                "the lock themselves: call them outside the `async with state.lock:` block"
            )
        _ = await super().acquire()
        self._holder = task
        return True

    @override
    def release(self) -> None:
        super().release()
        self._holder = None


@dataclass
class ConfigState:
    """Holds the loaded configuration state.

    ``take_snapshot`` keeps a copy of the configuration as loaded
    (``snapshot_for_target``). :meth:`plan` compares the layer this state does
    not save with it; the changes of a plan come from the data as read, not
    from the snapshot. A save is planned from each module's raw data as read
    (see :meth:`plan`), which is what decides which modules changed.

    A state continues after a save: each module written is replaced by the
    module the VCO stored (:meth:`replace_module`), so the next plan starts
    from what the VCO holds and the state takes more edits and saves. So
    does a save that raised ``ConfigConflictError`` or
    ``WriteNotAppliedError`` with the module as stored (``error.module``):
    the edits to that module are gone, and ``error.lost_edits`` lists them;
    ``error.redo_patch()`` is the patch that redoes them (``error.not_redone()``
    lists those it leaves out). Objects taken from the state before a save
    belong to the modules it replaced, and are detached: an edit through one
    raises ``DetachedObjectError``, at once or at the next :meth:`plan`.

    After a save that raised ``WriteOutcomeUnknownError``, or failed with any
    other error once an insert was sent, the state keeps the new module as it
    is and remembers the insert in ``unconfirmed_inserts``: the next save
    reads the configuration for a module of that name before it inserts.

    The edge layer's module data is bound to the profile layer (see
    ``bind_profile`` on the module data), so a segment the edge layer lacks can
    be added to it as an empty entry that inherits from the profile's.
    """

    edge_config: EdgeConfiguration | None
    profile_config: EnterpriseConfigurationProfile
    target_type: Literal["edge", "profile"]
    resolver_context: ResolverContext
    _snapshot: dict[str, EdgeConfiguration | EnterpriseConfigurationProfile] = field(
        default_factory=dict, repr=False
    )
    load_warnings: list[ValidationWarning] = field(default_factory=list)
    resolvers: ResolverRegistry = field(
        default_factory=ResolverRegistry, repr=False, compare=False
    )
    """The resolvers that turn the names in a patch for this state into IDs."""
    lock: StateLock = field(
        default_factory=StateLock, init=False, repr=False, compare=False
    )
    """Held by ``ConfigPipeline.apply`` and ``save`` for the whole call, so that
    they run one after the other. Code that edits the state by assignment from
    another task may hold it around the edit (``async with state.lock:``).
    ``apply`` and ``save`` called by the task that holds it raise
    ``RuntimeError``. Do not wait, inside the block, for another task that calls
    them: that task waits for the lock."""
    unconfirmed_inserts: set[str] = field(
        default_factory=set, init=False, repr=False, compare=False
    )
    """The new modules an insert was sent for whose answer was not read: the VCO
    may hold them. The next save reads the configuration before it inserts one
    of them."""
    _made_into_unset: list[tuple[AnyStoredModule, bool]] = field(
        default_factory=list, repr=False
    )
    """Each module ``module(create=True)`` made where ``modules`` was unset, and
    whether ``modules`` was in the config's ``model_fields_set`` then."""
    _replaced: list[_Replaced] = field(default_factory=list, repr=False)
    """Each module ``replace_module`` took out whose objects may still be held."""

    def __post_init__(self) -> None:
        """Bind each edge module's data to the profile module of the same name."""
        if self.edge_config is None or self.edge_config.modules is MISSING:
            return
        for mod in self.edge_config.modules:
            _bind_to_profile(mod, self.profile_config)

    def take_snapshot(self) -> None:
        """Deep-copy current configs for later diff detection."""
        snapshot: dict[str, EdgeConfiguration | EnterpriseConfigurationProfile] = {}
        if self.edge_config is not None:
            snapshot["edge"] = copy.deepcopy(self.edge_config)
        snapshot["profile"] = copy.deepcopy(self.profile_config)
        self._snapshot = snapshot

    def snapshot_for_target(
        self,
    ) -> EdgeConfiguration | EnterpriseConfigurationProfile | None:
        """Return the snapshot config matching ``target_type``.

        Returns ``None`` if no snapshot has been taken yet (or, for an edge
        target, if no edge config was loaded).
        """
        if self.target_type == "edge":
            return self._snapshot.get("edge")
        return self._snapshot.get("profile")

    @property
    def target_config(self) -> EdgeConfiguration | EnterpriseConfigurationProfile:
        """The config that edits apply to: the edge's, else the profile's."""
        if self.target_type == "edge" and self.edge_config is not None:
            return self.edge_config
        return self.profile_config

    def module(
        self, name: ConfigurationModuleName | str, *, create: bool = False
    ) -> AnyStoredModule:
        """The target config's module called *name* (an intent-level name works).

        With ``create=True``, an edge target that has no ``firewall`` or ``QOS``
        module gets an empty one, which saves as an insert. Nothing is copied
        from the profile. Raises ``ValueError`` if there is no such module and
        none can be created.
        """
        config = self.target_config
        unset = config.modules is MISSING
        was_set = "modules" in config.model_fields_set
        module = _module_of(
            config,
            resolve_module_name(name),
            create=create,
            profile=self.profile_config,
        )
        if unset and module.is_new:
            self._made_into_unset.append((module, was_set))
        return module

    def inherited_segment_ids(self, module: AnyStoredModule) -> frozenset[str]:
        """The ``segmentId``, in string form, of each segment *module* only inherits.

        *module* is an edge module of this state. These are the segments of
        the profile's module of the same name that *module* had no entry for
        when it was read: the ones a save writes an entry for after the edge's
        own (:func:`~pyvelo.client.module_write.missing_segments`), so a patch
        cannot give them a place. Empty for a profile target, for a profile
        with no module of that name, and for a module that has no
        ``segments``. A module the state created has no segments as read, so
        every segment of the profile is in it. After a save the module is the
        one the VCO stored, which has the entries the save wrote.
        """
        if self.target_type != "edge":
            return frozenset()
        profile_module = _find_module(self.profile_config, module.name)
        if profile_module is None:
            return frozenset()
        missing = missing_segments(
            module.raw_data, profile_module.raw_data, module.name
        )
        return frozenset(
            str(segment_id)
            for entry in missing
            if (segment_id := segment_id_of(entry)) is not None
        )

    def find_module(
        self, name: ConfigurationModuleName | str
    ) -> AnyStoredModule | None:
        """The target config's module called *name* (an intent-level name works), or None."""
        return _find_module(self.target_config, resolve_module_name(name))

    def drop_module(self, module: AnyStoredModule) -> None:
        """Remove *module*, one that ``module(create=True)`` made, from the target config.

        A ``modules`` list that the creation set, where it was unset, is unset
        again once it is empty. Raises ``ValueError`` for a module that is not
        new (``is_new``): one read from the VCO is never dropped. The module's
        name leaves ``unconfirmed_inserts``.
        """
        if not module.is_new:
            raise ValueError(
                f"module {module.name!r} was read from the VCO; only a new one is dropped"
            )
        self.unconfirmed_inserts.discard(module.name)
        config = cast("_AnyConfiguration", self.target_config)
        if config.modules is MISSING:
            return
        config.modules[:] = [m for m in config.modules if m is not module]
        made = next(
            (entry for entry in self._made_into_unset if entry[0] is module), None
        )
        if made is None:
            return
        self._made_into_unset.remove(made)
        if not config.modules:
            config.modules = MISSING
            if not made[1]:
                config.model_fields_set.discard("modules")

    def replace_module(self, old: AnyStoredModule, new: AnyStoredModule) -> None:
        """Put *new*, the module as the VCO stored it, where *old* is in the target config.

        An edge module is bound to the profile layer. The snapshot takes a
        copy of *new*, and a later :meth:`plan` starts from *new*, so it does
        not show what was written; the other modules keep their edits. The
        module's name leaves ``unconfirmed_inserts``.
        *old* and every model inside it are detached: an assignment raises
        ``DetachedObjectError``, and so does the next
        :meth:`plan` after an in-place change to a list or dict they hold. So
        does a lookup through a detached layer that adds a placeholder
        (``segment("Guest")``), on purpose: it usually comes before an edit
        that would be lost. A plain list or dict is compared only while the
        model it is a field of, or one that contains that model, is alive,
        because a list cannot be weakly referenced; holding an item of the list
        does not keep it compared. The providers (``RefOnly`` fields, which a
        dump leaves out) are compared while the module's ``data`` object is
        alive; an assignment to one raises at once, so this finds only a
        change that no guard sees. Nor is a model the module's dump leaves out
        (a field absent at its level). A pydantic model inside that is not a
        ``VcoModel`` (a segment's ``vrrp``) is compared like the others, but an
        assignment to it does not raise. The state keeps no strong reference to
        *old*. Raises ``ValueError`` if *old* is not in the target config.
        """
        config = cast("_AnyConfiguration", self.target_config)
        modules = [] if config.modules is MISSING else config.modules
        index = next((i for i, m in enumerate(modules) if m is old), None)
        if index is None:
            raise ValueError(f"module {old.name!r} is not in the target config")
        modules[index] = new
        self.unconfirmed_inserts.discard(old.name)
        if config is self.edge_config:
            _bind_to_profile(new, self.profile_config)
        snapshot = cast("_AnyConfiguration | None", self.snapshot_for_target())
        if snapshot is not None:
            stored = copy.deepcopy(new)
            if snapshot.modules is MISSING:
                snapshot.modules = [stored]
            else:
                at = next(
                    (i for i, m in enumerate(snapshot.modules) if m.name == new.name),
                    None,
                )
                if at is None:
                    snapshot.modules.append(stored)
                else:
                    snapshot.modules[at] = stored
        self._made_into_unset = [e for e in self._made_into_unset if e[0] is not old]
        # Last: the steps above work on *old* as a live module.
        data = old.data
        refs = ref_only_values(data) if isinstance(data, VcoModel) else {}
        nodes = old.mark_detached()
        dump = _dump(old)
        self._replaced.append(
            _Replaced(
                name=old.name,
                model=type(old),
                dump=dump,
                refs=refs,
                root=weakref.ref(data) if isinstance(data, VcoModel) else None,
                # A model the dump leaves out can be neither compared nor put back.
                nodes=[
                    (place, weakref.ref(node))
                    for place, node in nodes
                    if _at(dump, place) is not MISSING
                ],
            )
        )

    def _check_detached(self) -> None:
        """Raise ``DetachedObjectError`` if objects of a replaced module were changed in place.

        Each change is reported once: the comparison is then taken again from
        the objects as they are. A replaced module none of whose objects is
        alive is forgotten.
        """
        found: list[tuple[str, list[Path]]] = []
        for record in self._replaced:
            paths = record.changed_paths()
            if paths:
                found.append((record.name, paths))
        self._replaced = [record for record in self._replaced if record.nodes]
        if not found:
            return
        raise DetachedObjectError(
            "\n".join(
                f"{name}: objects taken from the state before a save were changed "
                f"in place at: {', '.join(paths)}. The state holds the module as "
                "stored; take the objects from the state again and redo the change"
                for name, paths in found
            ),
            [path for _, paths in found for path in paths],
        )

    def _check_unsaved_layer(self) -> None:
        """Raise ``LayerNotSavedError`` if the layer a save does not write was edited.

        The layer is compared with the copy ``take_snapshot`` took, its data
        and its providers (the ``RefOnly`` fields, which a dump leaves out).
        For each module that differs, the snapshot's copy becomes the module as
        it is now, so the edits are reported once. A state with no snapshot is
        not checked.
        """
        if self.target_type == "edge":
            layer, current = "profile", self.profile_config
        elif self.edge_config is not None:
            layer, current = "edge", self.edge_config
        else:
            return
        snapshot = cast("_AnyConfiguration | None", self._snapshot.get(layer))
        if snapshot is None or snapshot.modules is MISSING:
            return
        if current.modules is MISSING:
            return
        held = {m.name: i for i, m in enumerate(snapshot.modules)}
        fields: dict[str, list[str]] = {}
        for module in current.modules:
            index = held.get(module.name)
            data = module.data
            if index is None or not isinstance(data, VcoModel):
                continue
            before = snapshot.modules[index].data
            if not isinstance(before, VcoModel):
                continue
            changes = raw_changes(_dump(before), _dump(data), type(data))
            providers = ref_only_changes(ref_only_values(before), ref_only_values(data))
            if not changes and not providers:
                continue
            fields[module.name] = list(
                dict.fromkeys([*(str(c.path) for c in changes), *providers])
            )
            snapshot.modules[index] = copy.deepcopy(module)
        if not fields:
            return
        profile_id = self.profile_config.id_
        raise LayerNotSavedError(
            layer,
            self.target_type,
            fields,
            profile_id if isinstance(profile_id, int) else None,
        )

    @property
    def edge(self) -> LayerView[Edge]:
        """The edge layer's module data. Raises ``ValueError`` without an edge layer."""
        if self.target_type != "edge" or self.edge_config is None:
            raise ValueError("this state has no edge layer")
        return LayerView(self.edge_config, self.profile_config)

    @property
    def profile(self) -> LayerView[Profile]:
        """The profile layer's module data."""
        return LayerView(self.profile_config)

    @property
    def effective(self) -> EffectiveView:
        """What the edge uses, with where each value comes from (read-only).

        Computed from the edge and profile layers on each access, and never
        creates a module: an edge with no row for one has the profile's values
        and the display defaults. Raises ``ValueError`` without an edge layer.
        """
        if self.target_type != "edge" or self.edge_config is None:
            raise ValueError("this state has no edge layer, so no effective view")
        return EffectiveView(self.edge_config, self.profile_config)

    def plan(self, *, repair_defaults: bool = True) -> ConfigPlan:
        """Plan the write of every module of the target config, in order.

        The order is the write order: ``deviceSettings`` first, for every
        target, then the other modules in the order the configuration lists
        them. Each module is planned relative to the raw data it was read
        from. An edge module also reconciles its segments with the profile's
        module of the same name. Raises ``LossyFieldError`` if an edit cannot
        be written.

        First, raises ``DetachedObjectError`` naming the paths if objects of a
        module :meth:`replace_module` replaced were changed in place; once for
        those changes, so the next call plans.

        Then raises ``LayerNotSavedError`` if the layer this state does not
        save (the profile layer of an edge state) was edited, naming the
        fields; once for those edits, so the next call plans.

        A profile whose three modules (device settings, QoS and firewall) are
        all there has the same segments in each, so the plans of the three are
        then compared by ``segmentId``. Raises ``SegmentSetError``, naming the
        segments and the modules, for a segment the edit removes from a
        module (a QoS or firewall entry for a segment the device settings
        lacked as read may be removed), and for a segment the edit adds to
        some of the three and not all; the message gives the patch and the
        call that complete it. A module that disagrees as read, with nothing
        added, is left as it is and ``ConfigPlan.segment_warnings`` says so.

        *repair_defaults* is false to leave out the template ``defaults`` that
        a profile QoS segment lacks (:func:`~pyvelo.client.module_write.plan_module`);
        the plan warns either way.

        A profile whose edit adds a hub to a segment's hub, bridge or backhaul
        list, or removes one, has the role calls that go with it in
        ``ConfigPlan.steps`` (:func:`~pyvelo.config.hub_steps.derive_steps`),
        and ``check_warnings`` has a ``hub_roles_disagree`` warning for each
        hub whose roles as read do not match its lists. An added hub is looked
        up in the hub resolver; a removal needs none.

        Before the steps are derived, a profile's edit of the hub lists or the
        ``vpn`` flags is checked against the UI's rules for them
        (:func:`~pyvelo.config.hub_steps.check_hub_lists`): ``HubListError``
        for a hub list that would break one, with no request sent, and a
        ``hub_list_as_read`` warning for one that was broken as read. The
        checks of the hub lists come after the segment-set rule and before
        the reference check.

        Raises ``ResolverNotLoadedError`` when a provider was set and the
        state's network services are not loaded (:meth:`plan_module`), or a
        hub was added and the state's hubs are not. A save, a dry run and
        ``apply`` load them first; this does not.
        """
        self._check_detached()
        self._check_unsaved_layer()
        config = self.target_config
        target = self._target_text()
        if config.modules is MISSING:
            return ConfigPlan(modules=(), target=target)
        ordered = sorted(
            config.modules,
            key=lambda m: m.name != ConfigurationModuleName.DEVICE_SETTINGS,
        )
        plans = tuple(
            self.plan_module(mod, repair_defaults=repair_defaults) for mod in ordered
        )
        warnings = (
            _check_segment_set(ordered, plans) if self.target_type == "profile" else ()
        )
        as_read = {p.name: p.as_read for p in plans}
        steps, hub_warnings = self._hub_steps(ordered, as_read)
        check_warnings = self._check_references(ordered, as_read)
        return ConfigPlan(
            modules=plans,
            target=target,
            segment_warnings=warnings,
            check_warnings=(*hub_warnings, *check_warnings),
            steps=steps,
        )

    def _hub_steps(
        self,
        modules: Sequence[AnyStoredModule],
        as_read: Mapping[str, VcoModel | None],
    ) -> tuple[tuple[HubStep, ...], tuple[ValidationWarning, ...]]:
        """The role steps of the hub list changes of a profile's device settings, and the warnings of its lists.

        *as_read* is each module's data as the plan parsed it
        (``ModulePlan.as_read``, by module name); the device settings are not
        parsed again.

        The lists are checked first
        (:func:`~pyvelo.config.hub_steps.check_hub_lists`, which raises
        ``HubListError``), and the warnings are theirs, then those of the
        roles as read. An edge has neither: its profile's lists are not its
        to change. A segment's object ID is its ``deviceSettings:segment``
        row's, and for a segment the profile has no row for (one just
        assigned), the segment resolver's. An added hub is looked up in the
        hub resolver, which raises ``ResolverNotLoadedError`` when it is not
        loaded; a removal needs none.
        """
        if self.target_type != "profile":
            return (), ()
        module = next(
            (
                m
                for m in modules
                if m.name == ConfigurationModuleName.DEVICE_SETTINGS.value
            ),
            None,
        )
        edited: object = None if module is None else module.data
        if (
            not isinstance(module, DeviceSettingsConfigurationModule)
            or not isinstance(edited, DeviceSettingsData)
            or module.raw_data is MISSING
        ):
            return (), ()
        parsed = as_read.get(str(module.name))
        if not isinstance(parsed, DeviceSettingsData):
            return (), ()
        refs: object = module.raw_refs
        object_ids = segment_object_ids(refs)
        for segment_id in changed_segment_ids(parsed, edited):
            if segment_id not in object_ids:
                object_id = self._segment_object_id(segment_id)
                if object_id is not None:
                    object_ids[segment_id] = object_id
        resolver = self.resolvers.resolver_for(HubIdentity)
        hubs = resolver if isinstance(resolver, HubResolver) else None
        list_warnings = check_hub_lists(parsed, edited, hubs=hubs)
        steps = derive_steps(
            parsed,
            edited,
            segment_object_ids=object_ids,
            hubs=hubs,
        )
        configuration_id = self.target_config.id_
        disagreements = (
            role_disagreements(parsed, refs, configuration_id)
            if isinstance(configuration_id, int)
            else ()
        )
        return steps, (*list_warnings, *disagreements)

    def _segment_object_id(self, segment_id: int) -> int | None:
        """The object ID of segment *segment_id* in the segment resolver, or None when it does not know it.

        Raises ``ResolverNotLoadedError`` when the resolver is not loaded.
        """
        try:
            identity = self.resolvers.lookup_by(
                SegmentIdentity, lambda s: s.segment_id, segment_id
            )
        except ResolverNotLoadedError:
            raise
        except ResolutionError:
            return None
        return identity.object_id

    def _check_references(
        self,
        modules: Sequence[AnyStoredModule],
        as_read: Mapping[str, VcoModel | None],
    ) -> tuple[ValidationWarning, ...]:
        """Refuse a value of the target that names nothing the configuration provides; warn of one that did as read.

        Each module of the target is a target of the check, as edited
        (``module.data``) and as read (*as_read*, by module name: the data as
        the plan parsed it, with an edge's placeholders and a profile's
        additions; ``ModulePlan.as_read``). The sets the values are looked up
        in are those of the target's modules as edited and, for an edge, the
        profile's modules as read that can provide a set (parsed here); the
        sets as read are those of the modules as read. Raises ``DanglingReferenceError`` for a value that is
        new, or was a member and no longer is; returns a ``reference_kept``
        warning for one that named nothing as read and does still.
        """
        level = self._level()
        profile_models = self._profile_as_read() if level is ConfigContext.EDGE else []
        targets: list[tuple[str, VcoModel, VcoModel | None]] = []
        for module in modules:
            edited: object = module.data
            if not isinstance(edited, VcoModel):
                continue
            targets.append((str(module.name), edited, as_read.get(str(module.name))))
        sets_now = union(
            *(provided(edited) for _, edited, _ in targets),
            *(provided(model) for model in profile_models),
        )
        sets_as_read = union(
            *(provided(read) for _, _, read in targets if read is not None),
            *(provided(model) for model in profile_models),
        )
        findings = check_references(targets, sets_now, sets_as_read)
        if findings.dangling:
            raise DanglingReferenceError(
                findings.dangling, on_edge=level is ConfigContext.EDGE
            )
        return tuple(
            ValidationWarning(
                path=f"{reference.module}: {reference.path}",
                message=(
                    f"{reference_text(reference)} (it was so as read; the save "
                    "leaves it)"
                ),
                code="reference_kept",
            )
            for reference in findings.kept
        )

    def _level(self) -> ConfigContext:
        return (
            ConfigContext.PROFILE
            if self.target_type == "profile"
            else ConfigContext.EDGE
        )

    def _profile_as_read(self) -> list[VcoModel]:
        """The data of each module of the profile layer as read, with the profile's additions, for the modules that can provide a set.

        A module that provides none (QoS, firewall) is not parsed: its rules
        are the bulk of a profile, and the sets of its modules as read are
        empty.
        """
        config = self.profile_config
        if config.modules is MISSING:
            return []
        models: list[VcoModel] = []
        for module in config.modules:
            held: object = module.data
            if (
                isinstance(held, VcoModel)
                and can_provide(type(held))
                and module.raw_data is not MISSING
            ):
                models.append(
                    read_data(module, level=ConfigContext.PROFILE).model(type(held))
                )
        return models

    def _target_text(self) -> str:
        """What a plan is for: ``edge 101, enterprise 1, configuration 10``."""
        config = self.target_config
        if self.target_type == "edge":
            edge_id = self.resolver_context.edge_id
            text = "edge" if edge_id is None else f"edge {edge_id}"
        else:
            text = f"profile {json.dumps(config.name)}"
        if self.resolver_context.enterprise_id is not None:
            text += f", enterprise {self.resolver_context.enterprise_id}"
        if isinstance(config.id_, int):
            text += f", configuration {config.id_}"
        return text

    def plan_module(
        self,
        module: AnyStoredModule,
        data: VcoModel | None = None,
        *,
        repair_defaults: bool = True,
    ) -> ModulePlan:
        """Plan the write of one *module* of the target config, as :meth:`plan` plans it.

        With *data*, that is planned in place of the module's own data: the
        way to ask whether a change to *module* could be written before it is
        made. Raises ``LossyFieldError`` if the module, or *data*, holds an
        edit that cannot be written. Does not check for detached objects.
        The segments of a profile's modules are not checked here: ``plan``
        runs the rule that covers all three.

        A profile QoS module is planned with the ``vpn.conditionalBackhaul``
        of each segment of the profile's device settings, as it is now: a
        ``defaults`` entry it repairs takes it as ``allowConditionalBh``.
        *repair_defaults* is as for :meth:`plan`.

        A QoS rule's new backhaul hub takes the hub's service for its segment
        from the profile's device settings module as read (the target's own on
        a profile, the profile layer's on an edge): ``HubServiceNotFoundError``
        says a hub has none, and that the hub is saved first and the rule
        after.

        A provider the edit sets needs the network services loaded
        (``state.resolvers``): ``ResolverNotLoadedError`` says they are not.
        ``ConfigPipeline.save``, ``dry_run`` and ``apply`` load them and plan
        again; ``await pipeline.load_resolvers(state, NetworkServiceIdentity)``
        loads them now.
        """
        level = self._level()
        profile_mod = (
            _find_module(self.profile_config, module.name)
            if level is ConfigContext.EDGE
            else None
        )
        return plan_module(
            module,
            data,
            level=level,
            profile=profile_mod,
            profile_device_settings=_find_module(
                self.profile_config, ConfigurationModuleName.DEVICE_SETTINGS
            ),
            resolvers=self.resolvers,
            via_pipeline=True,
            repair_defaults=repair_defaults,
            conditional_backhaul=self.conditional_backhaul(module, level),
        )

    def fill_hub_services(self, data: VcoModel, *, skip: Collection[Path] = ()) -> None:
        """Give each backhaul hub of the QoS *data* that names a hub and no service its service for the segment.

        The service is the hub's in the profile's device settings module as
        read (the target's own on a profile, the profile layer's on an edge),
        as :meth:`plan_module` finds it (``pyvelo.client.hub_services.fill_services``).
        ``HubServiceNotFoundError`` says a hub has none, and that the hub is
        saved first and the rule after. An entry at a path of *skip* is left
        as it is. ``ResolverNotLoadedError`` says the segment resolver is not
        loaded.
        """
        held = _find_module(
            self.profile_config, ConfigurationModuleName.DEVICE_SETTINGS
        )
        refs: object = MISSING if held is None else held.raw_refs
        fill_services(
            data,
            HubServiceTable.from_refs(refs),
            segment_object_ids(refs),
            self.resolvers,
            skip=skip,
        )

    def conditional_backhaul(
        self, module: AnyStoredModule, level: ConfigContext
    ) -> dict[int, bool] | None:
        """For a profile QoS *module*, each segment's ``vpn.conditionalBackhaul`` in the profile's device settings now; else None."""
        if (
            level is not ConfigContext.PROFILE
            or module.name != ConfigurationModuleName.QOS.value
        ):
            return None
        held = _find_module(
            self.profile_config, ConfigurationModuleName.DEVICE_SETTINGS
        )
        data = None if held is None else held.data
        if not isinstance(data, DeviceSettingsData):
            return None
        return {
            item.segment.segment_id: item.vpn.conditional_backhaul
            for item in data.segments
        }

    def get_changed_modules(self) -> list[ConfigurationModuleName]:
        """Names of the modules that a save would write.

        Raises ``LossyFieldError`` as :meth:`plan` does.
        """
        return [ConfigurationModuleName(m.name) for m in self.plan().changed]

    def get_field_changes(
        self,
        module_name: str | None = None,
    ) -> list[ConfigFieldChange]:
        """The changes of :meth:`plan`, one entry each, in python names.

        With *module_name*, only that module's: the python or the VCO name
        (``"wan"`` or ``"WAN"``). A module the target does not have has none.
        Raises ``ValueError`` for a name that is no module. Raises what
        :meth:`plan` raises: ``LossyFieldError``, ``DetachedObjectError`` and
        ``LayerNotSavedError``, the last once for a set of edits, as it does
        for :meth:`get_changed_modules`.

        These are the changes a save writes, so a read changes nothing and a
        rename is one change. Each path is in the names a patch uses, and the
        values are JSON, as in the plan; the order of a keyed list is one
        entry whose ``moves`` say which items change place. A provider set,
        replaced or cleared is one entry at the provider's field, with the
        logical ID of the service before and after, or None; a provider
        replaced is one entry, not a removal and an addition. When the
        provider's stub had no ``ref``, setting a provider also writes the
        stub's ``ref``, and that is an entry of its own, before the
        provider's. A segment added to a profile module is one entry, at
        ``segments["<name>"]``, with ``before`` ``MISSING`` and ``after`` the
        entry as written; it comes before the changes to the module's other
        values.
        """
        wanted = None if module_name is None else resolve_module_name(module_name)
        changes: list[ConfigFieldChange] = []
        for module in self.plan().modules:
            if wanted is not None and module.name != wanted:
                continue
            held = _find_module(self.target_config, module.name)
            data = None if held is None else held.data
            model = type(data) if isinstance(data, VcoModel) else None
            changes.extend(_added_segment_changes(module, held))
            changes.extend(
                ConfigFieldChange(
                    module=module.name,
                    path=python_path(change.path, model),
                    before=change.before,
                    after=change.after,
                    place=change.place,
                    moves=change.moves,
                )
                for change in module.changes
            )
            changes.extend(_provider_changes(module))
        return changes


def _added_segment_changes(
    module: ModulePlan, held: AnyStoredModule | None
) -> list[ConfigFieldChange]:
    """One change for each segment added to *held*, a profile module, with the entry as the plan writes it."""
    if held is None:
        return []
    written = _segment_entries(module.data)
    return [
        ConfigFieldChange(
            module=module.name,
            path=Path(("segments", Key(addition.name))),
            before=MISSING,
            after=written[addition.segment_id],
        )
        for addition in held.additions
        if isinstance(addition, AddedSegment) and addition.segment_id in written
    ]


def _segment_entries(data: object) -> dict[Any, Any]:
    """The entries of the ``segments`` list of the module *data*, by ``segmentId``; one without an ID is left out."""
    segments = data.get("segments") if is_dict(data) else None
    if not is_list(segments):
        return {}
    return {
        segment_id: entry
        for entry in segments
        if (segment_id := segment_id_of(entry)) is not None
    }


def _segment_name(entry: object, segment_id: object) -> str:
    descriptor = entry.get("segment") if is_dict(entry) else None
    name = descriptor.get("name") if is_dict(descriptor) else None
    return name if isinstance(name, str) else str(segment_id)


def _names_text(names: Sequence[str]) -> str:
    return (
        " and ".join(names)
        if len(names) < 3  # noqa: PLR2004  # two names are joined by "and" alone
        else f"{', '.join(names[:-1])} and {names[-1]}"
    )


def _add_hint(name: str, modules: Sequence[str]) -> str:
    patch = json.dumps({"segments": {name: {"_op": "add"}}})
    where = " and ".join(modules)
    return (
        f"Send the patch {patch} to {where}, or assign the segment to all three "
        f'with pipeline.assign_segment(state, "{name}")'
    )


def _check_segment_set(
    modules: Sequence[AnyStoredModule], plans: Sequence[ModulePlan]
) -> tuple[ValidationWarning, ...]:
    """Run the segment-set rule of a profile on the payloads of *plans* and the modules as read.

    Does nothing, and returns no warning, unless the three modules of a profile
    are all there. For each ``segmentId`` in the payloads or the modules as
    read:

    - removed from a module (it was in it as read, and is not in its payload):
      ``SegmentSetError``, unless the module is QoS or firewall and device
      settings lacked the segment as read (an orphan);
    - added to a module (in its payload, not in it as read): it must be in
      the payloads of all three, else ``SegmentSetError`` names the modules
      that lack it;
    - otherwise, in some payloads and not in others: a ``segments_disagree``
      warning, which says how to complete it.
    """
    held = {
        module.name: (module, plan)
        for module, plan in zip(modules, plans, strict=True)
        if module.name in _SEGMENT_MODULES
    }
    if len(held) != len(_SEGMENT_MODULES):
        return ()
    read = {name: _segment_entries(held[name][0].raw_data) for name in _SEGMENT_MODULES}
    written = {name: _segment_entries(held[name][1].data) for name in _SEGMENT_MODULES}
    ids = dict.fromkeys(
        segment_id
        for entries in (*read.values(), *written.values())
        for segment_id in entries
    )
    removals: list[tuple[str, list[str]]] = []
    incomplete: list[tuple[str, list[str], list[str]]] = []
    warnings: list[ValidationWarning] = []
    for segment_id in ids:
        entry = next(
            e[segment_id]
            for e in (*written.values(), *read.values())
            if segment_id in e
        )
        name = _segment_name(entry, segment_id)
        orphan = segment_id not in read[_DEVICE_SETTINGS]
        removed = [
            m
            for m in _SEGMENT_MODULES
            if segment_id in read[m]
            and segment_id not in written[m]
            and not (orphan and m != _DEVICE_SETTINGS)
        ]
        lacking = [m for m in _SEGMENT_MODULES if segment_id not in written[m]]
        added = [
            m
            for m in _SEGMENT_MODULES
            if segment_id in written[m] and segment_id not in read[m]
        ]
        if removed:
            removals.append((name, removed))
        if added and lacking:
            incomplete.append((name, added, lacking))
        if not (removed or (added and lacking)) and 0 < len(lacking) < len(
            _SEGMENT_MODULES
        ):
            has = [m for m in _SEGMENT_MODULES if m not in lacking]
            warnings.append(
                ValidationWarning(
                    path=f"segments[{json.dumps(name)}]",
                    message=(
                        f"Segment {name!r} is in {_names_text(has)} but not in "
                        f"{_names_text(lacking)}, as read, and the segments of a "
                        "profile are the same in its three modules. This save "
                        f"leaves it as it is. {_add_hint(name, lacking)}."
                    ),
                    code="segments_disagree",
                )
            )
    if removals or incomplete:
        parts: list[str] = []
        for name, removed in removals:
            parts.append(
                f"removes the segment {name!r} from {_names_text(removed)}: a "
                "segment of a profile is removed from all three modules "
                "together, which is not supported, so leave it in the data "
                "(only a QoS or firewall entry for a segment the device "
                "settings lack can be removed)"
            )
        for name, added, lacking in incomplete:
            parts.append(
                f"adds the segment {name!r} to {_names_text(added)} but not to "
                f"{_names_text(lacking)}, and the segments of a profile are the "
                f"same in its three modules. {_add_hint(name, lacking)}"
            )
        raise SegmentSetError(
            "The edit " + "; and it ".join(parts) + ".",
            segments=[
                *dict.fromkeys(
                    [*(n for n, _ in removals), *(n for n, _, _ in incomplete)]
                )
            ],
            modules=[
                *dict.fromkeys(
                    [
                        *(m for _, r in removals for m in r),
                        *(m for _, _, c in incomplete for m in c),
                    ]
                )
            ],
        )
    return tuple(warnings)


def _provider_changes(module: ModulePlan) -> list[ConfigFieldChange]:
    """The provider changes of *module*'s plan: the ref rows of ``RefOnly`` owners, one change per owner.

    The row a change removes is the owner's ``before`` and the row it adds
    its ``after``, each as the identity's lower-case text.
    """
    before: dict[Path, str] = {}
    after: dict[Path, str] = {}
    for change in module.ref_changes:
        if change.owner is not None:
            (after if change.added else before)[change.owner] = str(change.value)
    return [
        ConfigFieldChange(
            module=module.name,
            path=owner,
            before=before.get(owner),
            after=after.get(owner),
        )
        for owner in dict.fromkeys([*before, *after])
    ]


_PROGRESS_ATTRIBUTE = "_save_progress"


@dataclass(frozen=True)
class SaveProgress:
    """How far a save got when it raised.

    Attributes:
        completed: The writes stored before the error, in order.
        failed: The name of the module whose write raised, or None when the
            error came after a write was stored (an edit made while a module
            was written).
        pending: The names of the planned modules that were not sent.
    """

    completed: tuple[ModuleWrite[Any], ...]
    failed: str | None
    pending: tuple[str, ...]

    def attach(self, error: BaseException) -> None:
        """Set this on *error*, for :func:`save_progress`; an error whose class takes no attributes is left as it is."""
        with contextlib.suppress(AttributeError, TypeError):
            setattr(error, _PROGRESS_ATTRIBUTE, self)


def save_progress(error: BaseException) -> SaveProgress | None:
    """The progress a save attached to *error*, or None.

    Looks at *error*, then at the error behind it (``__cause__``, else
    ``__context__``), so it answers for the ``TimeoutError`` that
    ``asyncio.timeout`` raises from a cancelled save. None for an error that
    did not come from a save that had started to write.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        progress = getattr(current, _PROGRESS_ATTRIBUTE, None)
        if isinstance(progress, SaveProgress):
            return progress
        current = current.__cause__ or current.__context__
    return None


@dataclass(frozen=True)
class StepOutcome:
    """What a save did with one role step (``ConfigPlan.steps``).

    *status* is ``"sent"`` for a call the save made, ``"in_place"`` for a step
    whose roles the VCO already had, which the save did not send, and
    ``"would_send"`` for a step a dry run would send.
    """

    step: HubStep
    status: Literal["sent", "in_place", "would_send"]


@dataclass
class SaveResult:
    """Outcome of ``ConfigPipeline.save``.

    A save that is not a dry run raises :class:`SaveValidationError` when a
    write-time validator reports an error, and sends nothing. A dry run
    collects the errors in ``validation_errors`` and returns;
    :meth:`raise_for_errors` raises them. ``modules_updated`` and
    ``modules_inserted`` are what the plan updates and inserts, written when
    ``written`` names them.
    """

    dry_run: bool = False
    """Whether the save that returned this sent nothing on purpose."""
    written: tuple[ModuleWrite[Any], ...] = ()
    """The writes this save stored, in order; empty for a dry run and for a save with nothing to change."""
    modules_updated: list[ConfigurationModuleName] = field(default_factory=list)
    modules_inserted: list[ConfigurationModuleName] = field(default_factory=list)
    plan: ConfigPlan | None = None
    server_changes: dict[ConfigurationModuleName, tuple[ServerChange, ...]] = field(
        default_factory=dict
    )
    """For each module written, what the VCO stored otherwise than sent, at paths that are not lost edits.

    A value stored as it was read is not listed: a planned change kept so is
    in ``left_out``."""
    left_out: dict[ConfigurationModuleName, tuple[PlannedChange, ...]] = field(
        default_factory=dict
    )
    """For each module written, the planned changes the VCO stores its own way.

    A change to a field it owns, and the removal of a default it adds back:
    neither applied nor lost, each with the value read, the value sent and the
    value stored. A ref row is never left out: one the plan adds or removes is
    applied or lost, and a row of a ref the VCO's processors manage or only
    adds to is a server change when it differs. Not in a list
    compared whole (a keyed list read with an item that has no key): there an
    owned field is part of the list's change, and a value stored there
    otherwise than sent is a server change with ``as_read`` ``MISSING``."""
    steps: tuple[StepOutcome, ...] = ()
    """What the save did with each role step of the plan, in order; empty for a plan without steps.

    A dry run says ``"would_send"`` or ``"in_place"``, and sends none."""
    warnings: list[ValidationWarning] = field(default_factory=list)
    validation_errors: list[ValidationError] = field(default_factory=list)
    # Optional count for downstream integrations managing enterprise objects.
    # ConfigPipeline leaves this at zero.
    objects_applied: int = 0

    @override
    def __repr__(self) -> str:
        """The dataclass ``repr``, with the number of entries per module for ``server_changes`` and ``left_out``.

        The entries hold configuration values, so they are not printed.
        """
        counted = ("server_changes", "left_out")
        parts: list[str] = []
        for item in fields(self):
            value: object = getattr(self, item.name)
            if item.name in counted:
                counts = cast("dict[object, tuple[object, ...]]", value)
                value = {str(name): len(entries) for name, entries in counts.items()}
            parts.append(f"{item.name}={value!r}")
        return f"{type(self).__name__}({', '.join(parts)})"

    @property
    def is_valid(self) -> bool:
        """Whether no write-time validator reported an error."""
        return len(self.validation_errors) == 0

    def raise_for_errors(self) -> None:
        """Raise :class:`SaveValidationError` if any write-time validator failed."""
        if self.validation_errors:
            raise SaveValidationError(self.validation_errors)


# ---------------------------------------------------------------------------
# Module lookup
# ---------------------------------------------------------------------------


def _find_module(config: _AnyConfiguration, name: str) -> AnyStoredModule | None:
    if config.modules is MISSING:
        return None
    return next((m for m in config.modules if m.name == name), None)


@runtime_checkable
class _ProfileBindable(Protocol):
    def bind_profile(self, profile_data: object) -> None: ...


def _bind_to_profile(
    module: AnyStoredModule, profile_config: _AnyConfiguration
) -> None:
    """Bind *module*'s data to the raw data of the profile module of the same name."""
    data: object = module.data
    if not isinstance(data, _ProfileBindable):
        return
    profile_module = _find_module(profile_config, module.name)
    data.bind_profile(profile_module.raw_data if profile_module else MISSING)


def _module_of(
    config: _AnyConfiguration,
    name: ConfigurationModuleName,
    *,
    create: bool,
    profile: _AnyConfiguration | None = None,
) -> AnyStoredModule:
    """*config*'s module *name*; with *create*, an empty edge one if it has none.

    A module that is created is bound to *profile* (see :func:`_bind_to_profile`).
    """
    existing = _find_module(config, name)
    if existing is not None:
        return existing
    if create and isinstance(config, EdgeConfiguration):
        if config.id_ is MISSING or config.id_ is None:
            raise ValueError(f"Cannot create module '{name}': configuration has no ID")
        new = new_edge_module(name, config.id_).for_edge()
        if profile is not None:
            _bind_to_profile(new, profile)
        if config.modules is MISSING:
            config.modules = []
        config.modules.append(new)
        return new
    # Raises ValueError, the same as for any module the config does not have.
    return config.get_module(name)
