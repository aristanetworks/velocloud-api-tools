"""ConfigState: holds edge/profile configuration with snapshot for diff-on-save."""

import asyncio
import copy
import json
import weakref
from dataclasses import dataclass, field, fields
from typing import Any, Literal, Protocol, cast, override, runtime_checkable

from pydantic import MISSING, BaseModel
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.models.configuration.common import segment_id_of
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.module_check import ModuleWrite
from pyvelo.client.module_write import ModulePlan, missing_segments, plan_module
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    ConfigurationBase,
    DeviceSettingsConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    FirewallConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
    new_edge_module,
)
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.validation import ValidationError
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.rebase import ItemMove, ItemPlace, Path, python_path, raw_changes
from pyvelo.core.response_check import PlannedChange, ServerChange

from pyvelo_intent.config.module_names import resolve_module_name
from pyvelo_intent.errors import LayerNotSavedError


@dataclass
class ValidationWarning:
    path: str
    message: str
    code: str
    severity: str = "warning"


@dataclass(frozen=True)
class ConfigFieldChange:
    """One change of :meth:`ConfigState.plan`: its module, and its path in python names.

    Attributes:
        module: The module's VCO name (e.g. ``"firewall"``, ``"WAN"``).
        path: Where the change is, in the names a patch uses
            (``segments["Global Segment"].outbound[block-ssh].name``).
        before: The value as read, a JSON value, or ``MISSING`` where there
            was none. For a keyed list whose items change place, the item
            names in the order read.
        after: The value as it is now, a JSON value, or ``MISSING`` where
            there is none; the item names in the new order for a reorder.
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
    - "refs: replaced" says the module's refs are sent.
    - "reconciled segments: A, B" are the profile's segments the write adds to
      the edge module as empty entries; with "none" the line reads "missing
      segments".
    - "override turned on: path (...)" is a section or an item of an edge whose
      override flag the changes turn on: from this save on the edge's own
      values apply there, and the profile's no longer do.
    - "legacy keys (dropped when the module is written): ..." are keys the VCO
      does not know, on a module with nothing else to write.

    Paths are in the names the VCO uses. A value is JSON, cut to 80 characters;
    the two values of a "~" line are cut where they first differ.

    Attributes:
        modules: The plan of each module of the target.
        target: What the plan is for (the first line of ``render()``), or "".
    """

    modules: tuple[ModulePlan, ...]
    target: str = ""

    @property
    def changed(self) -> tuple[ModulePlan, ...]:
        """The plans that write something."""
        return tuple(m for m in self.modules if m.operation != "none")

    def render(self) -> str:
        lines = [m.render() for m in self.modules]
        return "\n".join([self.target, *lines] if self.target else lines)


class LayerView:
    """Read-only access to the data of one config layer's modules.

    Each property is the module's ``data``, ready to read or edit in place. An
    edit is saved only through the state's target layer (``state.edge`` of an
    edge state, ``state.profile`` of a profile state); an edit of the other
    layer is reported by the next ``ConfigState.plan``. *profile* is the
    profile layer an edge layer inherits from; a module this view creates is
    bound to it.
    """

    def __init__(
        self, config: ConfigurationBase, profile: ConfigurationBase | None = None
    ) -> None:
        self._config: ConfigurationBase = config
        self._profile: ConfigurationBase | None = profile

    @property
    def firewall(self) -> FirewallData:
        """The firewall data; an edge layer that has none starts it empty."""
        module = _module_of(
            self._config,
            ConfigurationModuleName.FIREWALL,
            create=True,
            profile=self._profile,
        )
        assert isinstance(module, FirewallConfigurationModule)
        return module.data

    @property
    def qos(self) -> QosData:
        """The QoS data; an edge layer that has none starts it empty."""
        module = _module_of(
            self._config,
            ConfigurationModuleName.QOS,
            create=True,
            profile=self._profile,
        )
        assert isinstance(module, QosConfigurationModule)
        return module.data

    @property
    def device_settings(self) -> DeviceSettingsData:
        module = _module_of(
            self._config, ConfigurationModuleName.DEVICE_SETTINGS, create=False
        )
        assert isinstance(module, DeviceSettingsConfigurationModule)
        return module.data

    @property
    def wan(self) -> WanData:
        module = _module_of(self._config, ConfigurationModuleName.WAN, create=False)
        assert isinstance(module, WanConfigurationModule)
        return module.data


type _Place = tuple[str | int, ...]
"""Where a model is in its module's dump: dump keys and list positions (``mark_detached``)."""


@dataclass
class _Replaced:
    """A module ``replace_module`` took out, kept to find in-place edits of its objects.

    ``dump`` is the module's dump when it was replaced, or when an in-place
    edit was last found; ``nodes`` are the models inside it, held weakly, each
    with its place in ``dump``, which ``dump`` always holds. Nothing here keeps
    the module or its objects alive.
    """

    name: str
    model: type[VcoModel]
    dump: object
    nodes: list[tuple[_Place, weakref.ref[BaseModel]]]

    def changed_paths(self) -> list[Path]:
        """Where the objects still alive were changed in place since ``dump``, each once.

        Drops the nodes no longer alive. A node inside another live node is in
        that one's dump, so only the outermost live nodes are dumped: put at
        their places in ``dump``, they make the module as it is now, and
        ``raw_changes`` against ``dump`` gives the paths, from the module, with
        keyed items named by key. When there are some, that is the new
        ``dump``, so each change is found once; a node below that the edit
        moved from its place there is no longer compared.
        """
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


def _at(doc: object, place: _Place) -> object:
    """The value at *place* of the JSON *doc*, or ``MISSING`` where there is none."""
    for part in place:
        if isinstance(part, int):
            if not isinstance(doc, list):
                return MISSING
            items = cast(list[object], doc)
            if part >= len(items):
                return MISSING
            doc = items[part]
        else:
            if not isinstance(doc, dict):
                return MISSING
            entries = cast(dict[str, object], doc)
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
        items = list(cast(list[object], doc))
        items[part] = _put(items[part], rest, value)
        return items
    entries = dict(cast(dict[str, object], doc))
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
                + "the lock themselves: call them outside the `async with state.lock:` block"
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
    _made_into_unset: list[tuple[AnyConfigurationModule, bool]] = field(
        default_factory=list, repr=False
    )
    """Each module ``module(create=True)`` made where ``modules`` was unset, and
    whether ``modules`` was in the config's ``model_fields_set`` then."""
    _replaced: list[_Replaced] = field(default_factory=list, repr=False)
    """Each module ``replace_module`` took out whose objects may still be held."""

    def __post_init__(self) -> None:
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
    ) -> AnyConfigurationModule:
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

    def inherited_segment_ids(self, module: AnyConfigurationModule) -> frozenset[str]:
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
    ) -> AnyConfigurationModule | None:
        """The target config's module called *name* (an intent-level name works), or None."""
        return _find_module(self.target_config, resolve_module_name(name))

    def drop_module(self, module: AnyConfigurationModule) -> None:
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
        config = self.target_config
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

    def replace_module(
        self, old: AnyConfigurationModule, new: AnyConfigurationModule
    ) -> None:
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
        does not keep it compared. Nor is a model the module's dump leaves out
        (a field absent at its level). A pydantic model inside that is not a
        ``VcoModel`` (a segment's ``vrrp``) is compared like the others, but an
        assignment to it does not raise. The state keeps no strong reference to
        *old*. Raises ``ValueError`` if *old* is not in the target config.
        """
        config = self.target_config
        modules = [] if config.modules is MISSING else config.modules
        index = next((i for i, m in enumerate(modules) if m is old), None)
        if index is None:
            raise ValueError(f"module {old.name!r} is not in the target config")
        modules[index] = new
        self.unconfirmed_inserts.discard(old.name)
        if config is self.edge_config:
            _bind_to_profile(new, self.profile_config)
        snapshot = self.snapshot_for_target()
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
        nodes = old.mark_detached()
        dump = _dump(old)
        self._replaced.append(
            _Replaced(
                name=old.name,
                model=type(old),
                dump=dump,
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
                + f"in place at: {', '.join(paths)}. The state holds the module as "
                + "stored; take the objects from the state again and redo the change"
                for name, paths in found
            ),
            [path for _, paths in found for path in paths],
        )

    def _check_unsaved_layer(self) -> None:
        """Raise ``LayerNotSavedError`` if the layer a save does not write was edited.

        The layer is compared with the copy ``take_snapshot`` took. For each
        module that differs, the snapshot's copy becomes the module as it is
        now, so the edits are reported once. A state with no snapshot is not
        checked.
        """
        if self.target_type == "edge":
            layer, current = "profile", self.profile_config
        elif self.edge_config is not None:
            layer, current = "edge", self.edge_config
        else:
            return
        snapshot = self._snapshot.get(layer)
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
            if not changes:
                continue
            fields[module.name] = list(dict.fromkeys(str(c.path) for c in changes))
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
    def edge(self) -> LayerView:
        """The edge layer's module data. Raises ``ValueError`` without an edge layer."""
        if self.target_type != "edge" or self.edge_config is None:
            raise ValueError("this state has no edge layer")
        return LayerView(self.edge_config, self.profile_config)

    @property
    def profile(self) -> LayerView:
        """The profile layer's module data."""
        return LayerView(self.profile_config)

    def plan(self) -> ConfigPlan:
        """Plan the write of every module of the target config, in order.

        Each module is planned relative to the raw data it was read from. An
        edge module also reconciles its segments with the profile's module of
        the same name. Raises ``LossyFieldError`` if an edit cannot be written.

        First, raises ``DetachedObjectError`` naming the paths if objects of a
        module :meth:`replace_module` replaced were changed in place; once for
        those changes, so the next call plans.

        Then raises ``LayerNotSavedError`` if the layer this state does not
        save (the profile layer of an edge state) was edited, naming the
        fields; once for those edits, so the next call plans.
        """
        self._check_detached()
        self._check_unsaved_layer()
        config = self.target_config
        target = self._target_text()
        if config.modules is MISSING:
            return ConfigPlan(modules=(), target=target)
        return ConfigPlan(
            modules=tuple(self.plan_module(mod) for mod in config.modules),
            target=target,
        )

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
        self, module: AnyConfigurationModule, data: VcoModel | None = None
    ) -> ModulePlan:
        """Plan the write of one *module* of the target config, as :meth:`plan` plans it.

        With *data*, that is planned in place of the module's own data: the
        way to ask whether a change to *module* could be written before it is
        made. Raises ``LossyFieldError`` if the module, or *data*, holds an
        edit that cannot be written. Does not check for detached objects.
        """
        level = (
            ConfigContext.PROFILE
            if self.target_type == "profile"
            else ConfigContext.EDGE
        )
        profile_mod = (
            _find_module(self.profile_config, module.name)
            if level is ConfigContext.EDGE
            else None
        )
        return plan_module(module, data, level=level, profile=profile_mod)

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
        entry whose ``moves`` say which items change place.
        """
        wanted = None if module_name is None else resolve_module_name(module_name)
        changes: list[ConfigFieldChange] = []
        for module in self.plan().modules:
            if wanted is not None and module.name != wanted:
                continue
            held = _find_module(self.target_config, module.name)
            data = None if held is None else held.data
            model = type(data) if isinstance(data, VcoModel) else None
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
        return changes


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
        try:
            setattr(error, _PROGRESS_ATTRIBUTE, self)
        except (AttributeError, TypeError):
            pass


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

    A change to a field it owns, the removal of a default it adds back, and
    the removal of a row of an append-only ref: neither applied nor lost, each
    with the value read, the value sent and the value stored. Not in a list
    compared whole (a keyed list read with an item that has no key): there an
    owned field is part of the list's change, and a value stored there
    otherwise than sent is a server change with ``as_read`` ``MISSING``."""
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
                counts = cast(dict[object, tuple[object, ...]], value)
                value = {str(name): len(entries) for name, entries in counts.items()}
            parts.append(f"{item.name}={value!r}")
        return f"{type(self).__name__}({', '.join(parts)})"

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0

    def raise_for_errors(self) -> None:
        """Raise :class:`SaveValidationError` if any write-time validator failed."""
        if self.validation_errors:
            from pyvelo_intent.errors import SaveValidationError

            raise SaveValidationError(self.validation_errors)


# ---------------------------------------------------------------------------
# Module lookup
# ---------------------------------------------------------------------------


def _find_module(config: ConfigurationBase, name: str) -> AnyConfigurationModule | None:
    if config.modules is MISSING:
        return None
    return next((m for m in config.modules if m.name == name), None)


@runtime_checkable
class _ProfileBindable(Protocol):
    def bind_profile(self, profile_data: object) -> None: ...


def _bind_to_profile(
    module: AnyConfigurationModule, profile_config: ConfigurationBase
) -> None:
    """Bind *module*'s data to the raw data of the profile module of the same name."""
    data: object = module.data
    if not isinstance(data, _ProfileBindable):
        return
    profile_module = _find_module(profile_config, module.name)
    data.bind_profile(profile_module.raw_data if profile_module else MISSING)


def _module_of(
    config: ConfigurationBase,
    name: ConfigurationModuleName,
    *,
    create: bool,
    profile: ConfigurationBase | None = None,
) -> AnyConfigurationModule:
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
