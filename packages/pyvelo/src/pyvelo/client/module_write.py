"""Plan the write of a configuration module: the raw data as read, plus the caller's changes.

``plan_module`` is the one way a typed model becomes a write payload. The payload
starts from the JSON the module was read from (``raw_data``), so keys the model
does not know, values it coerced and defaults it added never reach the VCO. Only
what the caller changed (the difference between the module as re-parsed from the
raw JSON and the model as it is now) is applied to it.

After the plan: ``VcoClient.write_module`` sends it as an update or an insert
(the requests are built here) that asks for the module as stored
(``returnData``), and reads the module back when the VCO answers without it.
An update none of whose changes the check can find stored is sent without
``returnData`` and read back too: the VCO then answers with a row count, which
says whether it stored the write, and a row cannot.
``check_module_write`` (``pyvelo.client.module_check``) then compares what the
VCO stored with what the plan meant to change. When every planned change is
stored, the write returns the module as stored, with what else the VCO
changed (``server_changes``); otherwise it raises ``ConfigConflictError`` or
``WriteNotAppliedError``, which list the lost edits, or
``WriteOutcomeUnknownError`` when the module as stored could not be read.

Segment reconciliation: an edge module may lack a segment entry that its profile
has. The write then carries an empty entry for it (the profile's descriptor and
no rules; a deviceSettings entry also has an empty ``routes.static``, which the
Orchestrator requires), so the edge layer has a place to hold the segment.
Those entries follow the edge's own, in the profile's order, also when the caller's
data already holds one, in whatever place (a lookup added its placeholder, which
the caller may have edited): a lookup never changes what is written.
Reconciliation never writes only to reconcile: with no change from the caller the
plan is ``none``. A segment the caller deletes from the edge layer that the profile has
comes back as that empty entry: the edge cannot drop what it inherits.

Legacy keys: a firewall segment entry has no ``outboundOverride`` (the VCO and the
edge do not know it), but an older version of this library may have written one.
A write of a firewall module drops it from every segment entry, and the plan lists
each removal as a change. A plan with nothing else to write stays ``none``: the key
is reported (``legacy_keys``) and left, never written just to strip it.
"""

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast, override

from pydantic import MISSING
from pydantic_core import to_jsonable_python

from pyvelo.client.exceptions import LossyFieldError
from pyvelo.client.models.configuration.common import (
    SEGMENT_LISTS,
    placeholder_segment,
    segment_id_of,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.schemas.configuration import (
    ConfigurationModuleBase,
    ConfigurationModuleUpdate,
    FirewallConfigurationModule,
    InsertConfigurationModule,
    InsertFirewallConfigurationModule,
    InsertQosConfigurationModule,
    QosConfigurationModule,
    UpdateConfigurationModule,
)
from pyvelo.core import ConfigContext, VcoModel
from pyvelo.core.base import (
    is_dict,
    is_list,
)
from pyvelo.core.rebase import (
    Path,
    RawChange,
    carry_unset_defaults,
    find_lossy_assignments,
    is_override_flag,
    raw_changes,
    rebase,
)
from pyvelo.core.show import moves_text, shown, shown_together

type _Operation = Literal["none", "update", "insert"]

LEGACY_SEGMENT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "firewall": ("outboundOverride",),
}
"""Per module, the keys of a segment entry that the VCO and the edge do not have.

A write drops them (``drop_legacy_keys``); a plan reports them either way.
"""

_LEGACY_MODELS: Final[dict[str, type[VcoModel]]] = {"firewall": FirewallData}
"""The model of each module in ``LEGACY_SEGMENT_KEYS``, to address a path by its keys."""


def wire_dump(model: VcoModel, level: ConfigContext | None) -> dict[str, Any]:
    """The JSON a model is written as, at config level *level*."""
    return model.model_dump(
        mode="json",
        by_alias=True,
        context={"config_level": level} if level else None,
    )


def missing_segments(
    edge_data: object, profile_data: object, module_name: str
) -> list[dict[str, Any]]:
    """Empty segment entries for the profile's segments that the edge does not have.

    Segments are matched by ``segmentId``, in profile order. Nothing for a module
    that has no segment lists, for data that is not an object, or for a
    ``segments`` that is not a list (an absent or null one on the edge side is an
    empty list). An entry without a ``segmentId`` is skipped on the profile side
    and ignored on the edge side.
    """
    if module_name not in SEGMENT_LISTS:
        return []
    if not (is_dict(edge_data) and is_dict(profile_data)):
        return []
    profile_segments = profile_data.get("segments")
    edge_segments: object = edge_data.get("segments")
    if edge_segments is None:
        edge_segments = []
    if not (is_list(profile_segments) and is_list(edge_segments)):
        return []
    seen = [segment_id_of(entry) for entry in edge_segments]
    missing: list[dict[str, Any]] = []
    for entry in profile_segments:
        segment_id = segment_id_of(entry)
        if segment_id is None or segment_id in seen:
            continue
        seen.append(segment_id)  # a profile that repeats an id still gets one entry
        missing.append(
            placeholder_segment(cast(Mapping[str, object], entry), module_name)
        )
    return missing


@dataclass(frozen=True)
class ModulePlan:
    """What a save of one module writes."""

    name: str
    operation: _Operation
    data: dict[str, Any]
    """The payload; the raw data as read when *operation* is ``none``."""
    refs: dict[str, Any] | None
    """The complete refs to send, or None to leave refs out."""
    changes: tuple[RawChange, ...]
    """The caller's changes, relative to the raw data with the reconciled segments."""
    reconciled_segments: tuple[str, ...]
    """The profile's segments that the edge module, as read or as edited, does not have."""
    legacy_keys: tuple[Path, ...] = ()
    """Where the raw data has keys that the VCO does not know and a write drops.

    Always listed; a write also lists each as a ``-`` change. A plan that would
    write nothing else is ``none`` and leaves them.
    """
    overrides_turned_on: tuple[Path, ...] = ()
    """Each section or item whose override flag this write turns on.

    From this write on the edge layer's own values apply there and the
    profile's no longer do; nothing is copied from the profile. A flag inside a
    list that is written whole is part of that list's change and is not listed.
    """
    model: type[VcoModel] | None = None
    """The class of the module's data: that of the data planned when it is a
    model, else that of ``module.data`` when it is one. It tells ``render`` which
    values are secrets; a plan made from a dict still has it."""

    @override
    def __repr__(self) -> str:
        return (
            f"ModulePlan(name={self.name!r}, operation={self.operation!r}, "
            f"changes={len(self.changes)})"
        )

    def render(self) -> str:
        """The plan as text: a headline, then a line for each change.

        ``+ path: value`` is a key or an item that is added; an item added to a
        keyed list has its place after the path, ``(first)`` or ``(after
        other)``. ``- path`` is one that is removed. ``~ path: before -> after``
        is a value that changes. ``~ path: order: moves`` is a keyed list whose
        items change place: it names up to five moves, each with its new place,
        and counts the rest. A value is shown as JSON, cut to 80 characters;
        the two values of a ``~`` line are cut where they first differ. A
        secret is shown as ``"***"``; the changes keep the real values.
        """
        lines = [f"{self.name}: {self.operation}"]
        for change in self.changes:
            if change.moves is not None:
                lines.append(f"  ~ {change.path}: order: {moves_text(change.moves)}")
            elif change.before is MISSING:
                place = f" ({change.place})" if change.place is not None else ""
                after = shown(change.after, model=self.model, path=change.path)
                lines.append(f"  + {change.path}{place}: {after}")
            elif change.after is MISSING:
                lines.append(f"  - {change.path}")
            else:
                before, after = shown_together(
                    change.before, change.after, model=self.model, path=change.path
                )
                lines.append(f"  ~ {change.path}: {before} -> {after}")
        if self.refs is not None:
            lines.append("  refs: replaced")
        if self.reconciled_segments:
            label = "missing" if self.operation == "none" else "reconciled"
            lines.append(f"  {label} segments: {', '.join(self.reconciled_segments)}")
        for path in self.overrides_turned_on:
            lines.append(
                f"  override turned on: {path} (the edge's own values apply there "
                "from now on, and the profile's no longer do)"
            )
        if self.legacy_keys and self.operation == "none":
            lines.append(
                "  legacy keys (dropped when the module is written): "
                + ", ".join(self.legacy_keys)
            )
        return "\n".join(lines)


def update_request(
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    enterprise_id: int | None,
    *,
    return_data: bool = True,
) -> UpdateConfigurationModule:
    """The request that writes *plan* to *module*, at the version it was read.

    With *return_data* it asks for the module row as stored.
    """
    return UpdateConfigurationModule(
        configuration_module_id=module.id_,
        enterprise_id=enterprise_id if enterprise_id is not None else MISSING,
        version=module.version or MISSING,
        update=ConfigurationModuleUpdate(
            data=plan.data,
            refs=plan.refs if plan.refs is not None else MISSING,
        ),
        return_data=True if return_data else MISSING,
    )


def insert_request(
    module: ConfigurationModuleBase, plan: ModulePlan, enterprise_id: int | None
) -> InsertConfigurationModule:
    """The request that inserts *plan* as a new module of *module*'s configuration, asking for its row.

    Only a firewall or QoS module is inserted (``TypeError`` otherwise); a QoS
    one carries the plan's refs. Raises ``ValueError`` if *module* has no
    ``configuration_id``.
    """
    configuration_id = module.configuration_id
    if configuration_id is None:
        raise ValueError("Cannot insert module: it has no configuration ID")
    enterprise = enterprise_id if enterprise_id is not None else MISSING
    if isinstance(module, FirewallConfigurationModule):
        return InsertFirewallConfigurationModule(
            configuration_id=configuration_id,
            data=plan.data,
            enterprise_id=enterprise,
            return_data=True,
        )
    if isinstance(module, QosConfigurationModule):
        return InsertQosConfigurationModule(
            configuration_id=configuration_id,
            data=plan.data,
            refs=plan.refs,
            enterprise_id=enterprise,
            return_data=True,
        )
    raise TypeError(f"Cannot insert module type: {type(module).__name__}")


def drop_legacy_keys(data: object, module_name: str) -> None:
    """Remove from *data*'s segment entries the keys that *module_name* does not have."""
    keys = LEGACY_SEGMENT_KEYS.get(module_name)
    if not (keys and is_dict(data)):
        return
    segments = data.get("segments")
    if not is_list(segments):
        return
    for entry in segments:
        if is_dict(entry):
            for key in keys:
                _ = entry.pop(key, None)


def legacy_key_paths(data: object, module_name: str) -> tuple[Path, ...]:
    """Where *data* has keys that ``drop_legacy_keys`` would remove."""
    model = _LEGACY_MODELS.get(module_name)
    if model is None:
        return ()
    stripped = copy.deepcopy(data)
    drop_legacy_keys(stripped, module_name)
    return tuple(c.path for c in raw_changes(data, stripped, model))


def _overrides_turned_on(
    changes: tuple[RawChange, ...], model_type: type[VcoModel] | None
) -> tuple[Path, ...]:
    """The section or item of each change that turns an override flag on.

    A flag turned on is one whose value was ``false`` or absent and is now
    ``true``. Nothing without a model: a plain dict has no flags to tell.
    """
    if model_type is None:
        return ()
    return tuple(
        Path(change.path.parts[:-1])
        for change in changes
        if change.after is True
        and change.before is not True
        and is_override_flag(model_type, change.path)
    )


def _attribute(obj: object, name: str) -> object:
    """``getattr`` typed ``object``; ``MISSING`` if there is no such attribute."""
    value: object = getattr(obj, name, MISSING)
    return value


def _document_model(
    planned: type[VcoModel] | None, module: ConfigurationModuleBase
) -> type[VcoModel] | None:
    """The class of the data planned, else that of ``module.data``, else None."""
    if planned is not None:
        return planned
    data = _attribute(module, "data")
    return type(data) if isinstance(data, VcoModel) else None


def _reparse[M: VcoModel](
    model_type: type[M], raw: object, mode: Literal["python", "json"]
) -> M:
    """Parse *raw* the way the module was first parsed."""
    if mode == "json":
        return model_type.model_validate_json(json.dumps(raw))
    return model_type.model_validate(raw)


def _segment_name(placeholder: Mapping[str, Any]) -> str:
    descriptor = cast(Mapping[str, object], placeholder["segment"])
    name = descriptor.get("name")
    return name if isinstance(name, str) else str(descriptor.get("segmentId"))


def _add_segments(data: object, entries: list[dict[str, Any]]) -> None:
    """Append *entries* to the ``segments`` of *data*, an absent or null one included."""
    if not (entries and is_dict(data)):
        return
    segments = data.get("segments")
    if is_list(segments):
        segments.extend(entries)
    else:  # absent or null
        data["segments"] = list(entries)


def _with_reconciled_segments(
    current: dict[str, Any],
    baseline: dict[str, Any],
    extra: list[dict[str, Any]],
) -> dict[str, Any]:
    """*current* with the reconciled segments after its own, in the profile's order.

    The segments of *extra* are the ones reconciliation adds. *current* may hold
    an entry for one (a lookup added its placeholder, which the caller may have
    edited), in whatever place; it is kept as it is and moved to where the
    reconciled data has it. One *current* lacks is the baseline's. Without the
    baseline's entry the segment would read as the caller deleting it (typed
    data, in the rebase) or as a change to ``segments`` (a plain dict, which is
    the payload). The entries of the edge's own segments keep the caller's
    order. An absent or null ``segments`` in *current* is an empty list.
    """
    current_segments: object = current.get("segments")
    if current_segments is None:
        current_segments = []
    baseline_segments = baseline.get("segments")
    if not (extra and is_list(current_segments) and is_list(baseline_segments)):
        return current
    ids = [segment_id_of(placeholder) for placeholder in extra]
    own = [e for e in current_segments if segment_id_of(e) not in ids]
    reconciled: list[object] = []
    for segment_id in ids:
        entries = [e for e in current_segments if segment_id_of(e) == segment_id]
        if not entries:
            baseline_entry = next(
                (e for e in baseline_segments if segment_id_of(e) == segment_id), None
            )
            entries = [] if baseline_entry is None else [baseline_entry]
        reconciled.extend(entries)
    return {**current, "segments": [*own, *reconciled]}


def _plain_json(value: Mapping[str, object]) -> dict[str, Any]:
    """A new dict of *value* as the request would write it: plain JSON, sharing nothing with it."""
    return cast(dict[str, Any], to_jsonable_python(dict(value), by_alias=True))


def _plan_refs(
    module: ConfigurationModuleBase,
    refs: VcoModel | Mapping[str, object] | None,
    level: ConfigContext | None,
) -> dict[str, Any] | None:
    """The whole refs to send, or None when they are not there or did not change."""
    current = refs if refs is not None else _attribute(module, "refs")
    raw = module.raw_refs
    if isinstance(current, VcoModel):
        after: dict[str, Any] = wire_dump(current, level)
        model_type: type[VcoModel] | None = type(current)
    elif isinstance(current, Mapping):
        after = _plain_json(current)
        model_type = None
    else:  # MISSING, None: the module has no refs to send
        return None
    if raw is MISSING or raw is None:
        candidate: object = after
    else:
        before: object = raw
        if model_type is not None:
            before = wire_dump(_reparse(model_type, raw, module.raw_mode), level)
        candidate = copy.deepcopy(rebase(raw, before, after, model_type))
    return cast(dict[str, Any], candidate) if candidate != raw else None


def plan_module(
    module: ConfigurationModuleBase,
    data: VcoModel | Mapping[str, object] | None = None,
    *,
    refs: VcoModel | Mapping[str, object] | None = None,
    level: ConfigContext | None = None,
    profile: ConfigurationModuleBase | None = None,
) -> ModulePlan:
    """Plan the write of *module*, with *data* (default ``module.data``) as its data.

    *level* defaults to the config level of *data*. *refs* defaults to
    ``module.refs``. Give the *profile*'s module of the same name to reconcile
    the segments an edge module lacks (never at the profile level).

    Raises ``LossyFieldError`` if the caller assigned a value that cannot reach
    the written data (the default of a key that was absent when read).

    A mapping given as *data* or *refs* is converted as the request would write
    it (a ``UUID`` or a ``datetime`` becomes its text), so the plan holds plain
    JSON that shares nothing with the caller's objects.
    """
    name = str(_attribute(module, "name"))
    current = data if data is not None else _attribute(module, "data")
    if isinstance(current, Mapping):
        current = _plain_json(cast(Mapping[str, object], current))
    if level is None and isinstance(current, VcoModel):
        level = current.config_context
    refs_out = _plan_refs(module, refs, level)

    raw = module.raw_data
    if raw is MISSING:  # a module object that never carried data
        payload = (
            wire_dump(current, level)
            if isinstance(current, VcoModel)
            else cast(dict[str, Any], current)
        )
        model_type = type(current) if isinstance(current, VcoModel) else None
        drop_legacy_keys(payload, name)
        return ModulePlan(
            name=name,
            operation="update",
            data=payload,
            refs=refs_out,
            changes=tuple(raw_changes({}, payload, model_type)),
            reconciled_segments=(),
            model=_document_model(model_type, module),
        )

    inherited = profile if level is not ConfigContext.PROFILE else None
    extra: list[dict[str, Any]] = []
    if inherited is not None:
        extra = missing_segments(raw, inherited.raw_data, name)
    reconciled = copy.deepcopy(raw)
    _add_segments(reconciled, extra)

    if isinstance(current, VcoModel):
        model_type = type(current)
        baseline = _reparse(model_type, reconciled, module.raw_mode)
        before = wire_dump(baseline, level)
        after = wire_dump(current, level)
        carry_unset_defaults(baseline, current, before, after)
        payload = cast(
            dict[str, Any],
            copy.deepcopy(
                rebase(
                    reconciled,
                    before,
                    _with_reconciled_segments(after, before, extra),
                    model_type,
                )
            ),
        )
        lossy = find_lossy_assignments(baseline, current, before, after, level)
        if lossy:
            flags = [path for path in lossy if is_override_flag(model_type, path)]
            raise LossyFieldError(name, lossy, flags)
    else:
        model_type = None
        topped_up = dict(cast(Mapping[str, Any], current))
        if extra:  # only for an object, so *reconciled* is one
            topped_up = _with_reconciled_segments(
                topped_up, cast(dict[str, Any], reconciled), extra
            )
        payload = copy.deepcopy(topped_up)

    restored: list[dict[str, Any]] = []
    if inherited is not None:
        # A segment the caller deleted from the edge layer is the profile's again.
        restored = missing_segments(payload, inherited.raw_data, name)
        _add_segments(payload, restored)

    changed = payload != reconciled or refs_out is not None
    operation: _Operation = (
        "none" if not changed else "insert" if module.is_new else "update"
    )
    legacy = legacy_key_paths(reconciled, name)
    if changed:  # never a write just to strip them
        drop_legacy_keys(payload, name)
    changes = tuple(raw_changes(reconciled, payload, model_type))
    return ModulePlan(
        name=name,
        operation=operation,
        data=payload if changed else copy.deepcopy(cast(dict[str, Any], raw)),
        refs=refs_out,
        changes=changes,
        reconciled_segments=tuple(
            _segment_name(entry) for entry in [*extra, *restored]
        ),
        legacy_keys=legacy,
        overrides_turned_on=_overrides_turned_on(changes, model_type),
        model=_document_model(model_type, module),
    )
