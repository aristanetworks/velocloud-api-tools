"""ConfigState: holds edge/profile configuration with snapshot for diff-on-save."""

import copy
import weakref
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast, runtime_checkable

from pydantic import MISSING, BaseModel
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import DetachedObjectError
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.wan.root import WanData
from pyvelo.client.module_write import ModulePlan, plan_module
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
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.validation import ValidationError
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.fields import get_key_spec
from pyvelo.core.rebase import (
    KeyForm,
    Path,
    item_key,
    item_key_text,
    key_form,
    raw_changes,
)
from pyvelo.core.response_check import ServerChange

from pyvelo_intent.config.module_names import resolve_module_name


@dataclass
class ValidationWarning:
    path: str
    message: str
    code: str
    severity: str = "warning"


@dataclass(frozen=True)
class ConfigFieldChange:
    """A single per-field difference between snapshot and current state.

    Attributes:
        module: User-facing module name (e.g. ``"firewall"``).
        path: Dotted path to the changed field, with bracketed segments using
            the KeyField key value (e.g. ``"lan.networks[100].name"``). The
            path round-trips conceptually into a ``merge_patch`` patch.
        before: Snapshot value, or ``MISSING`` if the field was unset.
        after: Current value, or ``MISSING`` if the field is now unset.
    """

    module: str
    path: str
    before: object
    after: object


@dataclass(frozen=True)
class ConfigPlan:
    """What a save of a whole :class:`ConfigState` writes: one plan per module."""

    modules: tuple[ModulePlan, ...]

    @property
    def changed(self) -> tuple[ModulePlan, ...]:
        """The plans that write something."""
        return tuple(m for m in self.modules if m.operation != "none")

    def render(self) -> str:
        return "\n".join(m.render() for m in self.modules)


class LayerView:
    """Read-only access to the data of one config layer's modules.

    Each property is the module's ``data``, ready to read or edit in place.
    *profile* is the profile layer an edge layer inherits from; a module this
    view creates is bound to it.
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
    nodes: list[tuple[_Place, weakref.ref[VcoModel]]]

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


def _dump(model: VcoModel) -> object:
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


@dataclass
class ConfigState:
    """Holds the loaded configuration state with snapshot for diff detection.

    After load, a snapshot is taken for the per-field diff. A save is planned
    from each module's raw data as read (see :meth:`plan`), which is what
    decides which modules changed.

    A state that has written to the VCO is *spent*: its raw data no longer
    matches the VCO, so it must not be applied to or saved again.

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
    _spent: bool = field(default=False, repr=False)
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

    def find_module(
        self, name: ConfigurationModuleName | str
    ) -> AnyConfigurationModule | None:
        """The target config's module called *name* (an intent-level name works), or None."""
        return _find_module(self.target_config, resolve_module_name(name))

    def drop_module(self, module: AnyConfigurationModule) -> None:
        """Remove *module*, one that ``module(create=True)`` made, from the target config.

        A ``modules`` list that the creation set, where it was unset, is unset
        again once it is empty. Raises ``ValueError`` for a module that is not
        new (``is_new``): one read from the VCO is never dropped.
        """
        if not module.is_new:
            raise ValueError(
                f"module {module.name!r} was read from the VCO; only a new one is dropped"
            )
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

        An edge module is bound to the profile layer. The per-field snapshot
        takes a copy of *new*, so neither :meth:`plan` nor
        :meth:`get_field_changes` shows what was written; the other modules
        keep their edits. *old* and every model inside it are detached: an
        assignment raises ``DetachedObjectError``, and so does the next
        :meth:`plan` after an in-place change to a list or dict they hold. So
        does a lookup through a detached layer that adds a placeholder
        (``segment("Guest")``), on purpose: it usually comes before an edit
        that would be lost. A plain list or dict still held after every model
        of its module is dropped is not compared, because a list cannot be
        weakly referenced; nor is a model the module's dump leaves out (a
        field absent at its level). The state keeps no strong reference to
        *old*. Raises ``ValueError`` if *old* is not in the target config.
        """
        config = self.target_config
        modules = [] if config.modules is MISSING else config.modules
        index = next((i for i, m in enumerate(modules) if m is old), None)
        if index is None:
            raise ValueError(f"module {old.name!r} is not in the target config")
        modules[index] = new
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
        # Last: a copy of a detached object is detached too.
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

    @property
    def spent(self) -> bool:
        """True once this state has written to the VCO."""
        return self._spent

    def mark_spent(self) -> None:
        """Record that this state has written to the VCO."""
        self._spent = True

    def plan(self) -> ConfigPlan:
        """Plan the write of every module of the target config, in order.

        Each module is planned relative to the raw data it was read from. An
        edge module also reconciles its segments with the profile's module of
        the same name. Raises ``LossyFieldError`` if an edit cannot be written.

        First, raises ``DetachedObjectError`` naming the paths if objects of a
        module :meth:`replace_module` replaced were changed in place; once for
        those changes, so the next call plans.
        """
        self._check_detached()
        config = self.target_config
        if config.modules is MISSING:
            return ConfigPlan(modules=())
        level = (
            ConfigContext.PROFILE
            if self.target_type == "profile"
            else ConfigContext.EDGE
        )
        plans: list[ModulePlan] = []
        for mod in config.modules:
            profile_mod = (
                _find_module(self.profile_config, mod.name)
                if level is ConfigContext.EDGE
                else None
            )
            plans.append(plan_module(mod, level=level, profile=profile_mod))
        return ConfigPlan(modules=tuple(plans))

    def get_changed_modules(self) -> list[ConfigurationModuleName]:
        """Names of the modules that a save would write.

        Raises ``LossyFieldError`` as :meth:`plan` does.
        """
        return [ConfigurationModuleName(m.name) for m in self.plan().changed]

    def get_field_changes(
        self,
        module_name: str | None = None,
    ) -> list[ConfigFieldChange]:
        """Per-field diff between snapshot and current state.

        If ``module_name`` is None, returns changes across all changed
        modules. KeyField-annotated lists are diffed by key (additions,
        removals, per-key recursive diff), so reordering does not appear as
        a change; one with an item without a key, or a key repeated, is
        diffed by position. The path format describes the field a user would
        patch via a ``merge_patch`` dict.

        MISSING is preserved as the sentinel rather than rendered as None.
        """
        if self.target_type == "edge" and self.edge_config is not None:
            current_config = self.edge_config
        else:
            current_config = self.profile_config
        snapshot_config = self.snapshot_for_target()

        if snapshot_config is None or current_config.modules is MISSING:
            return []

        snapshot_modules: dict[str, AnyConfigurationModule] = {}
        if snapshot_config.modules is not MISSING:
            for mod in snapshot_config.modules:
                snapshot_modules[mod.name] = mod

        changes: list[ConfigFieldChange] = []
        for mod in current_config.modules:
            if module_name is not None and mod.name != module_name:
                continue
            snap_mod = snapshot_modules.get(mod.name)
            current_data = mod.data
            snapshot_data = snap_mod.data if snap_mod is not None else MISSING
            _diff_value(
                module=mod.name,
                path="",
                before=snapshot_data,
                after=current_data,
                out=changes,
            )
        return changes


@dataclass
class SaveResult:
    """Outcome of ``ConfigPipeline.save``.

    Validator errors are collected into ``validation_errors`` rather than
    raised, so callers can inspect them. Most CLI / scripting callers should
    follow up with :meth:`raise_for_errors` to surface failures as exceptions.
    """

    modules_updated: list[ConfigurationModuleName] = field(default_factory=list)
    modules_inserted: list[ConfigurationModuleName] = field(default_factory=list)
    plan: ConfigPlan | None = None
    server_changes: dict[ConfigurationModuleName, tuple[ServerChange, ...]] = field(
        default_factory=dict
    )
    """For each module written, what the VCO stored otherwise than sent, outside the plan's changes."""
    warnings: list[ValidationWarning] = field(default_factory=list)
    validation_errors: list[ValidationError] = field(default_factory=list)
    # Optional count for downstream integrations managing enterprise objects.
    # ConfigPipeline leaves this at zero.
    objects_applied: int = 0

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


# ---------------------------------------------------------------------------
# Per-field diff helpers
# ---------------------------------------------------------------------------


def _diff_value(
    *,
    module: str,
    path: str,
    before: object,
    after: object,
    out: list[ConfigFieldChange],
) -> None:
    """Walk two parallel values and append a ConfigFieldChange per leaf difference.

    KeyField-annotated lists are diffed by key. Sub-models recurse field by
    field. Scalars compare via equality.
    """
    if before is MISSING and after is MISSING:
        return
    if before is MISSING or after is MISSING:
        out.append(
            ConfigFieldChange(module=module, path=path, before=before, after=after)
        )
        return
    if isinstance(before, BaseModel) and isinstance(after, BaseModel):
        if type(before) is not type(after):
            out.append(
                ConfigFieldChange(module=module, path=path, before=before, after=after)
            )
            return
        for field_name, field_info in type(after).model_fields.items():
            child_path = f"{path}.{field_name}" if path else field_name
            before_val = cast(object, getattr(before, field_name, MISSING))
            after_val = cast(object, getattr(after, field_name, MISSING))
            spec = get_key_spec(field_info)
            if (
                spec is not None
                and isinstance(before_val, list)
                and isinstance(after_val, list)
            ):
                _diff_keyed_list(
                    module=module,
                    path=child_path,
                    key_paths=spec.paths,
                    before=cast(list[object], before_val),
                    after=cast(list[object], after_val),
                    out=out,
                )
            else:
                _diff_value(
                    module=module,
                    path=child_path,
                    before=before_val,
                    after=after_val,
                    out=out,
                )
        return
    if isinstance(before, list) and isinstance(after, list):
        before_items = cast(list[object], before)
        after_items = cast(list[object], after)
        if len(before_items) != len(after_items):
            out.append(
                ConfigFieldChange(module=module, path=path, before=before, after=after)
            )
            return
        for i, (b, a) in enumerate(zip(before_items, after_items, strict=True)):
            _diff_value(module=module, path=f"{path}[{i}]", before=b, after=a, out=out)
        return
    if before != after:
        out.append(
            ConfigFieldChange(module=module, path=path, before=before, after=after)
        )


def _diff_keyed_list(
    *,
    module: str,
    path: str,
    key_paths: tuple[str, ...],
    before: list[object],
    after: list[object],
    out: list[ConfigFieldChange],
) -> None:
    """Diff two lists of BaseModel items by their KeyField key value.

    Items are matched by key in string form, part by part (``key_form``). The
    path names an item by ``item_key_text``: the value of a one-part key, a
    composite key as ``key_text`` writes it
    (``static["10.0.0.0",24,"192.168.1.1",GE3]``), as the item before had it.

    When an item on either side has no key, or a key is repeated on either
    side, items cannot be matched by key: the lists are diffed by position,
    as a list without a ``KeyField`` is (``_diff_value``).
    """
    keys_before = [key_form(item_key(item, key_paths)) for item in before]
    keys_after = [key_form(item_key(item, key_paths)) for item in after]
    if any(
        None in keys or len(set(keys)) != len(keys)
        for keys in (keys_before, keys_after)
    ):
        _diff_value(module=module, path=path, before=before, after=after, out=out)
        return
    by_key_before: dict[KeyForm | None, object] = dict(
        zip(keys_before, before, strict=True)
    )
    by_key_after: dict[KeyForm | None, object] = dict(
        zip(keys_after, after, strict=True)
    )
    all_keys: list[KeyForm | None] = list(by_key_before.keys()) + [
        k for k in by_key_after if k not in by_key_before
    ]
    for key in all_keys:
        b = by_key_before.get(key, MISSING)
        a = by_key_after.get(key, MISSING)
        text = item_key_text(a if b is MISSING else b, key_paths)
        _diff_value(module=module, path=f"{path}[{text}]", before=b, after=a, out=out)
