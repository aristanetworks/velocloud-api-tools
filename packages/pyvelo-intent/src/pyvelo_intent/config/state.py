"""ConfigState: holds edge/profile configuration with snapshot for diff-on-save."""

import copy
from dataclasses import dataclass, field
from typing import Literal, cast

from pydantic import MISSING, BaseModel
from pyvelo.client.enums.configuration import ConfigurationModuleName
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
from pyvelo.core import ConfigContext
from pyvelo.core.fields import get_key_field

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
    """

    def __init__(self, config: ConfigurationBase) -> None:
        self._config: ConfigurationBase = config

    @property
    def firewall(self) -> FirewallData:
        """The firewall data; an edge layer that has none starts it empty."""
        module = _module_of(self._config, ConfigurationModuleName.FIREWALL, create=True)
        assert isinstance(module, FirewallConfigurationModule)
        return module.data

    @property
    def qos(self) -> QosData:
        """The QoS data; an edge layer that has none starts it empty."""
        module = _module_of(self._config, ConfigurationModuleName.QOS, create=True)
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


@dataclass
class ConfigState:
    """Holds the loaded configuration state with snapshot for diff detection.

    After load, a snapshot is taken for the per-field diff. A save is planned
    from each module's raw data as read (see :meth:`plan`), which is what
    decides which modules changed.

    A state that has written to the VCO is *spent*: its raw data no longer
    matches the VCO, so it must not be applied to or saved again.
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
        return _module_of(self.target_config, resolve_module_name(name), create=create)

    @property
    def edge(self) -> LayerView:
        """The edge layer's module data. Raises ``ValueError`` without an edge layer."""
        if self.target_type != "edge" or self.edge_config is None:
            raise ValueError("this state has no edge layer")
        return LayerView(self.edge_config)

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
        """
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
        """Names of the modules that a save would write."""
        return [ConfigurationModuleName(m.name) for m in self.plan().changed]

    def get_field_changes(
        self,
        module_name: str | None = None,
    ) -> list[ConfigFieldChange]:
        """Per-field diff between snapshot and current state.

        If ``module_name`` is None, returns changes across all changed
        modules. KeyField-annotated lists are diffed by key (additions,
        removals, per-key recursive diff), so reordering does not appear as
        a change. The path format describes the field a user would patch via
        a ``merge_patch`` dict.

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


def _module_of(
    config: ConfigurationBase, name: ConfigurationModuleName, *, create: bool
) -> AnyConfigurationModule:
    """*config*'s module *name*; with *create*, an empty edge one if it has none."""
    existing = _find_module(config, name)
    if existing is not None:
        return existing
    if create and isinstance(config, EdgeConfiguration):
        if config.id_ is MISSING or config.id_ is None:
            raise ValueError(f"Cannot create module '{name}': configuration has no ID")
        new = new_edge_module(name, config.id_).for_edge()
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
            key_attr = get_key_field(field_info)
            if (
                key_attr is not None
                and isinstance(before_val, list)
                and isinstance(after_val, list)
            ):
                _diff_keyed_list(
                    module=module,
                    path=child_path,
                    key_attr=key_attr,
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
    key_attr: str,
    before: list[object],
    after: list[object],
    out: list[ConfigFieldChange],
) -> None:
    """Diff two lists of BaseModel items by their KeyField key value."""

    def _resolve_key(item: object) -> object | None:
        val: object | None = item
        for part in key_attr.split("."):
            val = cast(object | None, getattr(val, part, None))
            if val is None:
                return None
        return val

    by_key_before: dict[object | None, object] = {
        _resolve_key(item): item for item in before
    }
    by_key_after: dict[object | None, object] = {
        _resolve_key(item): item for item in after
    }
    all_keys: list[object | None] = list(by_key_before.keys()) + [
        k for k in by_key_after if k not in by_key_before
    ]
    for key in all_keys:
        b = by_key_before.get(key, MISSING)
        a = by_key_after.get(key, MISSING)
        _diff_value(module=module, path=f"{path}[{key}]", before=b, after=a, out=out)
