"""ConfigState: holds edge/profile configuration with snapshot for diff-on-save."""

import copy
from dataclasses import dataclass, field
from typing import Literal, cast

from pydantic import MISSING, BaseModel
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.schemas.configuration import (
    AnyConfigurationModule,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.validation import ValidationError
from pyvelo.core.fields import get_key_field


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


@dataclass
class ConfigState:
    """Holds the loaded configuration state with snapshot for diff detection.

    After load, a snapshot is taken. On save, the current state is compared
    against the snapshot to determine which modules changed.
    """

    edge_config: EdgeConfiguration | None
    profile_config: EnterpriseConfigurationProfile
    target_type: Literal["edge", "profile"]
    resolver_context: ResolverContext
    _snapshot: dict[str, EdgeConfiguration | EnterpriseConfigurationProfile] = field(
        default_factory=dict, repr=False
    )
    load_warnings: list[ValidationWarning] = field(default_factory=list)

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

    def get_changed_modules(self) -> list[ConfigurationModuleName]:
        """Compare current state against snapshot, return names of changed modules.

        Compares module data by serializing to dicts and checking equality.
        """
        changed: list[ConfigurationModuleName] = []

        # Determine which config to diff (edge for edge targets, profile for profile targets)
        if self.target_type == "edge" and self.edge_config is not None:
            current_config = self.edge_config
        else:
            current_config = self.profile_config
        snapshot_config = self.snapshot_for_target()

        if snapshot_config is None or current_config.modules is MISSING:
            return changed

        # Build a map of snapshot modules by name
        snapshot_modules: dict[str, AnyConfigurationModule] = {}
        if snapshot_config.modules is not MISSING:
            for mod in snapshot_config.modules:
                snapshot_modules[mod.name] = mod

        for mod in current_config.modules:
            snap_mod = snapshot_modules.get(mod.name)
            if snap_mod is None:
                # New module not in snapshot
                changed.append(ConfigurationModuleName(mod.name))
                continue

            # Compare data dicts
            current_data = mod.data
            snapshot_data = snap_mod.data

            if isinstance(current_data, dict) and isinstance(snapshot_data, dict):
                if current_data != snapshot_data:
                    changed.append(ConfigurationModuleName(mod.name))
            elif isinstance(current_data, BaseModel) and isinstance(
                snapshot_data, BaseModel
            ):
                if current_data.model_dump() != snapshot_data.model_dump():
                    changed.append(ConfigurationModuleName(mod.name))
            elif current_data != snapshot_data:
                changed.append(ConfigurationModuleName(mod.name))

        return changed

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
