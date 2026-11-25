"""Plan the write of a configuration module: the raw data as read, plus the caller's changes.

``plan_module`` is the one way a typed model becomes a write payload. The payload
starts from the JSON the module was read from (``raw_data``), so keys the model
does not know, values it coerced and defaults it added never reach the VCO. Only
what the caller changed (the difference between the module as re-parsed from the
raw JSON and the model as it is now) is applied to it.

After the plan: ``VcoClient.write_module`` sends it as an update or an insert
(the requests are built here) that asks for the module as stored
(``returnData``), and reads the module back when the VCO answers without it.
An insert is always read back, with its refs, because its row does not promise
them.
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

Refs: the refs of a write are derived from the data, never given. A ref row goes
with an identity that the data holds or that only the row says (``OwnsRef`` and
``RefOnly``); the plan compares those identities as read and now
(``pyvelo.client.derived_refs``). A row is added for one that appeared and
removed for one that went, and the plan then sends the complete refs, every other
row as read. A write of a deviceSettings or QOS module with segments always
sends its refs, as read when nothing changed in them, because the VCO runs its
processors on every such write. A module read without its refs
(``module.refs_loaded`` false) cannot be written with an edit to an owner, or
with segments (``RefsNotLoadedError``); any other plan of it sends no refs.

Rebase: ``rebase_onto`` is the plan of a module that another write changed
after it was read, applied to the module as it is now: the caller's edits (the
plan's changes) go onto the fresh data, and the write goes on with the fresh
version. It is for a module written after a profile's device settings (see
``VcoClient.write_module``).

Missing defaults: every QoS segment of a profile holds the template's four
``defaults`` rules, which the VCO does not enforce. ``plan_module`` pairs the
rules of each segment with the template's (``pair_defaults``) and warns of
those a segment lacks (``defaults_repaired``). When the module is written for
another reason it adds them, from the template, in template order; a module
with nothing else to write is not written just to repair. A list the
template does not describe (an item it has no entry for, or an entry whose
name the list holds under another ``match``) is left alone, with a warning
(``defaults_not_template``). The repaired entries are no change of the
caller's: ``changes`` leave them out and ``data`` has them.

Legacy keys: a firewall segment entry has no ``outboundOverride`` (the VCO and the
edge do not know it), but an older version of this library may have written one.
A write of a firewall module drops it from every segment entry, and the plan lists
each removal as a change. A plan with nothing else to write stays ``none``: the key
is reported (``legacy_keys``) and left, never written just to strip it.
"""

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from typing import Any, Final, Literal, cast, override

from pydantic import MISSING
from pydantic_core import to_jsonable_python

from pyvelo.client.derived_refs import (
    PlanWarning,
    RefChange,
    changed_owners,
    derive_refs,
    sends_refs_always,
)
from pyvelo.client.exceptions import (
    ConfigConflictError,
    FixedListError,
    HubListError,
    LossyFieldError,
    RefsNotLoadedError,
    SegmentSetError,
)
from pyvelo.client.hub_services import (
    HubServiceTable,
    hub_list_fields,
    missing_service_error,
)
from pyvelo.client.models.configuration.common import (
    SEGMENT_LISTS,
    placeholder_segment,
    segment_id_of,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.profile_segments import (
    AddedDefaults,
    AddedSegment,
    SegmentAddition,
    insert_defaults,
    pair_defaults,
    repaired_entry,
    with_additions,
)
from pyvelo.client.models.configuration.ref_owners import (
    fill_ref_only,
    segment_object_ids,
)
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
    IdentityLookup,
    is_dict,
    is_list,
)
from pyvelo.core.fixed_list import fixed_list_violations
from pyvelo.core.layers import Edge, Layered, Profile
from pyvelo.core.owned import UnflaggedInUse, unflagged_in_use_at
from pyvelo.core.rebase import (
    Key,
    Path,
    RawChange,
    carry_unset_defaults,
    find_lossy_assignments,
    is_override_flag,
    raw_changes,
    rebase,
)
from pyvelo.core.response_check import (
    PlannedChange,
    ResponseCheck,
    Rewrites,
    ServerChange,
    check_response,
)
from pyvelo.core.show import moves_text, shown, shown_together

type _Operation = Literal["none", "update", "insert"]

LEGACY_SEGMENT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "firewall": ("outboundOverride",),
}
"""Per module, the keys of a segment entry that the VCO and the edge do not have.

A write drops them (``drop_legacy_keys``); a plan reports them either way.
"""


def _legacy_model(
    module_name: str, level: ConfigContext | None
) -> type[VcoModel] | None:
    """The data model of a module in ``LEGACY_SEGMENT_KEYS`` at config level *level* (an edge's but for the profile's), to address a path by its keys; None for another module."""
    if module_name != "firewall":
        return None
    return (
        FirewallData[Profile] if level is ConfigContext.PROFILE else FirewallData[Edge]
    )


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
            placeholder_segment(cast("Mapping[str, object]", entry), module_name)
        )
    return missing


@dataclass(frozen=True)
class MissingDefaults:
    """The template ``defaults`` a profile QoS segment lacks."""

    segment: str
    """The segment's name."""
    segment_id: object
    names: tuple[str, ...]
    """The missing template entries, in template order."""
    repaired: bool
    """Whether this plan adds them to its payload."""


@dataclass(frozen=True)
class ModulePlan:
    """What a save of one module writes."""

    name: str
    operation: _Operation
    data: dict[str, Any]
    """The payload; the raw data as read when *operation* is ``none``."""
    refs: dict[str, Any] | None
    """The complete refs to send, or None to leave refs out.

    None whenever *operation* is ``none``, and for a module that never carried data.
    """
    changes: tuple[RawChange, ...]
    """The caller's changes, relative to the raw data with the reconciled segments.

    A profile module's are relative to the raw data plus the entries added to
    it (``module.additions``), so an added entry nobody edited is no change.
    """
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
    ref_changes: tuple[RefChange, ...] = ()
    """The ref rows the edit adds and removes, from the difference of the owners."""
    warnings: tuple[PlanWarning, ...] = ()
    """What disagrees in the module as read about its refs, and the plan leaves as it is."""
    added_segments: tuple[str, ...] = ()
    """The names of the segments added to the module that the payload holds."""
    repair_defaults: bool = True
    """The ``repair_defaults`` that ``plan_module`` was called with; a rebase of the plan keeps it."""
    missing_defaults: tuple[MissingDefaults, ...] = ()
    """The segments of a profile QoS module that lack template ``defaults``, one entry each.

    ``repaired`` says whether the payload has the missing entries.
    """
    as_read: VcoModel | None = dataclass_field(default=None, repr=False, compare=False)
    """The module as read, parsed: the baseline this plan compared the data with.

    It is the raw data with an edge's placeholders and a profile's additions
    (``read_data(...).model``), so a check of the same module as read (the
    references of a ``ConfigState`` plan) takes it and parses nothing again.
    None for a plan of a module with no raw data, and for one planned from a
    mapping. It is the one object the plan used: do not change it. A plan made
    from this one (a rebase, the hub rows) keeps it.
    """

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

        A ref row the edit adds or removes is ``+ refs <name> (<segment>):
        <value>`` or ``- refs <name> (<segment>): <value>``, without the
        segment for a row of the module; ``refs: sent as read`` says an update
        or insert sends refs with no row changed, and ``warning: <message>``
        is something about the refs as read that the plan leaves as it is.
        ``added segments: A, B`` names the segments the pipeline added to a
        profile module, which are no change from it. ``repaired defaults:
        Guest: A, B`` names, for a segment, the ``defaults`` entries the
        payload adds from the template; ``missing defaults:`` is the same
        line for entries the plan leaves out (nothing else to write, or the
        repair off).
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
        lines.extend(self.ref_lines())
        if self.reconciled_segments:
            label = "missing" if self.operation == "none" else "reconciled"
            lines.append(f"  {label} segments: {', '.join(self.reconciled_segments)}")
        if self.added_segments:
            lines.append(f"  added segments: {', '.join(self.added_segments)}")
        for missing in self.missing_defaults:
            label = "repaired" if missing.repaired else "missing"
            lines.append(
                f"  {label} defaults: {missing.segment}: {', '.join(missing.names)}"
            )
        lines.extend(
            f"  override turned on: {path} (the edge's own values apply there "
            "from now on, and the profile's no longer do)"
            for path in self.overrides_turned_on
        )
        if self.legacy_keys and self.operation == "none":
            lines.append(
                "  legacy keys (dropped when the module is written): "
                + ", ".join(self.legacy_keys)
            )
        lines.extend(f"  warning: {warning.message}" for warning in self.warnings)
        return "\n".join(lines)

    def ref_lines(self) -> list[str]:
        """The lines of ``render`` about the refs sent: a line for each row added or removed."""
        lines: list[str] = []
        for change in self.ref_changes:
            sign = "+" if change.added else "-"
            place = f" ({change.segment})" if change.segment is not None else ""
            lines.append(f"  {sign} refs {change.name}{place}: {change.value}")
        if self.refs is not None and not self.ref_changes:
            lines.append("  refs: sent as read")
        return lines


def with_hub_rows(
    plan: ModulePlan,
    *,
    added: Sequence[Mapping[str, object]],
    removed: Sequence[Mapping[str, object]],
    segments: Mapping[int, str] | None = None,
) -> ModulePlan:
    """*plan* with the hub ref rows of a save's role steps: the *removed* rows out of its refs and the *added* rows in.

    The role calls make a hub's service and its row, which the plan of the
    device settings module, made before them, cannot know. A row is told by its
    ``ref``, ``enterpriseObjectId`` and ``segmentObjectId``; each *removed*
    one is a row of the refs, and the refs hold it no more. A name with a
    change is a list, and is left out when it has no row. Each row also
    becomes a ``RefChange`` of the plan, matched by its ``enterpriseObjectId``
    and ``segmentObjectId``, so that the write holds the VCO to it
    (``check_refs``) and ``render()`` lists it. *segments* names a segment by
    its object ID, for the line of ``render()``.

    Raises ``ValueError`` for a plan that is no update with refs, and for a
    removed row the refs do not hold.
    """
    if plan.operation != "update" or plan.refs is None:
        raise ValueError(
            f"{plan.name}: hub rows go with an update that sends refs, and this "
            f"plan is {'an update with no refs' if plan.operation == 'update' else plan.operation}"
        )
    names = segments or {}
    refs = copy.deepcopy(plan.refs)
    changes: list[RefChange] = []

    def change(row: Mapping[str, object], *, adding: bool) -> RefChange:
        segment_object_id = row.get("segmentObjectId")
        return RefChange(
            added=adding,
            name=str(row["ref"]),
            segment=names.get(segment_object_id)
            if isinstance(segment_object_id, int)
            else None,
            row=copy.deepcopy(dict(row)),
            match="enterpriseObjectId",
            value=row.get("enterpriseObjectId"),
            segment_object_id=segment_object_id
            if isinstance(segment_object_id, int)
            else None,
            owner=None,
        )

    def held(name: str) -> list[dict[str, Any]]:
        value = refs.get(name)
        if is_list(value):
            return [cast("dict[str, Any]", row) for row in value]
        return [] if value is None else [cast("dict[str, Any]", value)]

    for row in removed:
        name = str(row["ref"])
        rows = held(name)
        index = next(
            (
                i
                for i, r in enumerate(rows)
                if is_dict(r)
                and r.get("enterpriseObjectId") == row.get("enterpriseObjectId")
                and r.get("segmentObjectId") == row.get("segmentObjectId")
            ),
            None,
        )
        if index is None:
            raise ValueError(
                f"{plan.name}: the refs hold no {name} row for object "
                f"{row.get('enterpriseObjectId')} in segment object "
                f"{row.get('segmentObjectId')}, so there is none to remove"
            )
        change_made = change(rows[index], adding=False)
        del rows[index]
        if rows:
            refs[name] = rows
        else:
            del refs[name]
        changes.append(change_made)
    for row in added:
        name = str(row["ref"])
        refs[name] = [*held(name), copy.deepcopy(dict(row))]
        changes.append(change(row, adding=True))
    return replace(plan, refs=refs, ref_changes=(*plan.ref_changes, *changes))


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
    one carries the plan's refs when it has any, and a firewall one never does:
    its request has no ``refs``, and no firewall field declares an owner.
    Raises ``ValueError`` if *module* has no ``configuration_id``.
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
            refs=plan.refs if plan.refs is not None else MISSING,
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


def legacy_key_paths(
    data: object, module_name: str, level: ConfigContext | None
) -> tuple[Path, ...]:
    """Where *data*, a module's data at config level *level*, has keys that ``drop_legacy_keys`` would remove."""
    model = _legacy_model(module_name, level)
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


def _module_level(module: ConfigurationModuleBase) -> ConfigContext | None:
    """The config level of *module*, None for an object that has none."""
    level = _attribute(module, "config_context")
    return level if isinstance(level, ConfigContext) else None


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


@dataclass(frozen=True)
class ReadData:
    """A module's raw data, as ``plan_module`` compares it with an edit."""

    module: ConfigurationModuleBase
    extra: list[dict[str, Any]]
    """The profile's segment entries the edge module lacks (none at the profile level)."""
    reconciled: Any
    """The raw data plus *extra*: the module as read, as the VCO would store it reconciled."""
    planned_from: Any
    """*reconciled* plus the entries added to a profile module (``module.additions``)."""

    def model[M: VcoModel](self, model_type: type[M]) -> M:
        """``planned_from`` parsed as *model_type*, the way the module was first parsed.

        The refs as read are filled in when the module has them. This is the
        baseline of the plan: the module as read, with an edge's placeholders
        and a profile's additions.
        """
        parsed = _reparse(model_type, self.planned_from, self.module.raw_mode)
        if self.module.refs_loaded:
            fill_ref_only(parsed, self.module.raw_refs)
        return parsed


def read_data(
    module: ConfigurationModuleBase,
    *,
    level: ConfigContext | None,
    profile: ConfigurationModuleBase | None = None,
) -> ReadData:
    """The data of *module* as read, ready to parse: the one baseline of a plan.

    An edge module (any *level* but the profile's) gets the placeholders of
    the segments the *profile*'s module of the same name has and it lacks; a
    profile module gets the entries the pipeline added to it. ``plan_module``
    builds its baseline from this, and so does the reference check of a
    ``ConfigState`` plan. *module* must carry raw data.
    """
    raw = module.raw_data
    extra: list[dict[str, Any]] = []
    if profile is not None and level is not ConfigContext.PROFILE:
        extra = missing_segments(raw, profile.raw_data, str(_attribute(module, "name")))
    reconciled = copy.deepcopy(raw)
    _add_segments(reconciled, extra)
    # What the pipeline added to a profile module is part of what the plan
    # compares with (R'), but not of the data as read: only that is what is
    # stored, so it decides whether there is anything to write and what the
    # refs compare with.
    additions = module.additions if level is ConfigContext.PROFILE else ()
    planned_from = with_additions(reconciled, additions) if additions else reconciled
    return ReadData(module, extra, reconciled, planned_from)


def _segment_name(placeholder: Mapping[str, Any]) -> str:
    descriptor = cast("Mapping[str, object]", placeholder["segment"])
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
    return cast("dict[str, Any]", to_jsonable_python(dict(value), by_alias=True))


def _segment_names(data: object) -> dict[object, str]:
    """The name of each segment entry of *data*, by ``segmentId``, in order; entries without an ID are skipped."""
    segments = data.get("segments") if is_dict(data) else None
    if not is_list(segments):
        return {}
    return {
        segment_id: _segment_name(cast("Mapping[str, Any]", entry))
        for entry in segments
        if (segment_id := segment_id_of(entry)) is not None
    }


def _segment_set_error(
    module_name: str, added: list[str], removed: list[str]
) -> SegmentSetError:
    """The refusal of a one-module plan that adds the segments *added* or removes *removed*."""
    did = [
        f"{verb} {', '.join(map(repr, names))}"
        for verb, names in (("adds", added), ("removes", removed))
        if names
    ]
    message = (
        f"{module_name}: the edit {' and '.join(did)}, but the segments of a "
        "profile are the same in its three modules (deviceSettings, QOS and "
        "firewall), and one module alone cannot change them."
    )
    if added:
        patch = json.dumps({"segments": {name: {"_op": "add"} for name in added}})
        message += (
            f" Assign a segment through the pipeline, which makes its entry in "
            f"all three modules: send the patch {patch} "
            f'(pipeline.assign_segment(state, "{added[0]}")).'
        )
    if removed:
        message += (
            " A segment of a profile is removed from all three modules together, "
            "which is not supported: leave it in the data."
        )
    return SegmentSetError(
        message,
        module_name=module_name,
        segments=(*added, *removed),
        modules=(module_name,),
    )


def _check_segment_set(module_name: str, as_read: object, payload: object) -> None:
    """Raise ``SegmentSetError`` when *payload*'s segments are not those of *as_read*, by ``segmentId``."""
    read, written = _segment_names(as_read), _segment_names(payload)
    added = [name for key, name in written.items() if key not in read]
    removed = [name for key, name in read.items() if key not in written]
    if added or removed:
        raise _segment_set_error(module_name, added, removed)


def _hub_list(entry: object, path: tuple[str, ...]) -> dict[tuple[str, str], str]:
    """The hubs of the list at *path* of the segment entry *entry*, by ``(type, logicalId)``, with their names, in order."""
    node = entry
    for key in path:
        node = node.get(key) if is_dict(node) else None
    hubs: dict[tuple[str, str], str] = {}
    for item in node if is_list(node) else []:
        if not is_dict(item):
            continue
        logical_id, kind, name = (
            item.get("logicalId"),
            item.get("type"),
            item.get("name"),
        )
        hubs[(str(kind), str(logical_id).lower())] = (
            name if isinstance(name, str) and name else str(logical_id)
        )
    return hubs


def _hub_patch(segment: str, path: tuple[str, ...], change: dict[str, Any]) -> str:
    """The patch text that makes *change* to the list at *path* of *segment*."""
    node: dict[str, Any] = change
    for key in reversed(path):
        node = {key: node}
    return json.dumps({"segments": {segment: node}})


def _hub_list_error(
    module_name: str, changes: list[tuple[str, tuple[str, ...], str, bool]]
) -> HubListError:
    """The refusal of a one-module plan that adds or removes the hubs of *changes*: ``(segment, list path, hub, added)``."""
    segments = list(dict.fromkeys(segment for segment, _, _, _ in changes))
    lists = list(dict.fromkeys(".".join(path) for _, path, _, _ in changes))
    hubs = list(dict.fromkeys(hub for _, _, hub, _ in changes))
    did = "; ".join(
        f"{'adds' if added else 'removes'} {hub!r} "
        f"{'to' if added else 'from'} {'.'.join(path)} of {segment!r}"
        for segment, path, hub, added in changes
    )
    segment, path, hub, added = changes[0]
    example = _hub_patch(
        segment,
        path,
        {"+": {"_op": "add", "name": hub}} if added else {hub: {"_op": "delete"}},
    )
    message = (
        f"{module_name}: the edit {did}. A hub added to or removed from these "
        "lists needs its role set or cleared on the hub's service, which one "
        "module write cannot do, so the VCO would list a hub no Edge can use "
        "(or a hub whose roles are lost). The pipeline sets the roles as steps "
        "of the save, in the order the UI does: send the change as a patch, for "
        f"example {example} "
        f'(pipeline.apply(state, module="{module_name}", patch=...)), then '
        "pipeline.save(state)."
    )
    return HubListError(
        message,
        segment=segments[0] if len(segments) == 1 else None,
        lists=lists,
        hubs=hubs,
    )


def _check_hub_lists(module_name: str, as_read: object, payload: object) -> None:
    """Raise ``HubListError`` when a segment of *payload* has other hubs in a hub list than *as_read*, by ``(type, logicalId)``.

    A reorder is no change of members. Segments are matched by ``segmentId``.
    """
    held = as_read.get("segments") if is_dict(as_read) else None
    read_by_id = (
        {segment_id_of(entry): entry for entry in held if is_dict(entry)}
        if is_list(held)
        else {}
    )
    segments = payload.get("segments") if is_dict(payload) else None
    changes: list[tuple[str, tuple[str, ...], str, bool]] = []
    for entry in segments if is_list(segments) else []:
        if not is_dict(entry):
            continue
        before = read_by_id.get(segment_id_of(entry))
        if before is None:
            continue
        name = _segment_name(cast("Mapping[str, Any]", entry))
        for field in hub_list_fields():
            path = field.path
            read, written = _hub_list(before, path), _hub_list(entry, path)
            changes.extend(
                (name, path, hub_name, True)
                for key, hub_name in written.items()
                if key not in read
            )
            changes.extend(
                (name, path, hub_name, False)
                for key, hub_name in read.items()
                if key not in written
            )
    if changes:
        raise _hub_list_error(module_name, changes)


def _defaults_path(name: str) -> str:
    return f"segments[{json.dumps(name)}].defaults"


def _flag_off_warning(found: UnflaggedInUse) -> PlanWarning:
    """The warning for an edge object the backend uses although its flag is off."""
    segments = [(key, item) for key, item in found.steps if item is not None]
    where = (
        f" of segment {segments[0][1]!r}"
        if len(segments) == 1 and segments[0][0] == "segments"
        else f" at {found.path}"
    )
    return PlanWarning(
        "flag_off_in_use",
        found.path,
        f"The edge stores {found.field}{where} with {found.flag} false or absent. "
        "The VCO uses an edge object that is present whatever its flag, so this "
        "edge runs with it and not with the profile's, though is_local reads "
        f"False. To inherit the profile's, send {json.dumps(found.inherit_patch())} "
        "(inherit()). To keep it, turn its flag on and change no value: "
        f"localize() does that, or send {json.dumps(found.keep_patch())}.",
    )


def _defaults_findings(
    payload: object,
) -> tuple[list[PlanWarning], list[tuple[str, object, tuple[str, ...]]], list[Any]]:
    """What the template's ``defaults`` say about each QoS segment of *payload*.

    Returns the ``defaults_not_template`` warnings, the segments with entries
    to repair as ``(name, segment ID, missing names)`` and those segments'
    entries, in step.
    """
    warnings: list[PlanWarning] = []
    repairs: list[tuple[str, object, tuple[str, ...]]] = []
    segments = payload.get("segments") if is_dict(payload) else None
    targets: list[Any] = []
    for entry in segments if is_list(segments) else []:
        if not is_dict(entry):
            continue
        name = _segment_name(cast("Mapping[str, Any]", entry))
        pairing = pair_defaults(entry.get("defaults", MISSING))
        if not pairing.missing:
            continue
        path = _defaults_path(name)
        if pairing.unknown or pairing.clash:
            items = cast("list[Any]", entry.get("defaults"))
            parts: list[str] = []
            if pairing.unknown:
                known = [
                    str(items[i].get("name", f"item {i}"))
                    if is_dict(items[i])
                    else f"item {i}"
                    for i in pairing.unknown
                ]
                parts.append(f"items the template does not know ({', '.join(known)})")
            if pairing.clash:
                parts.append(
                    f"missing entries whose names it already holds ({', '.join(pairing.clash)}), "
                    "which a repair would repeat"
                )
            warnings.append(
                PlanWarning(
                    "defaults_not_template",
                    path,
                    f"The defaults of segment {name!r} lack template entries "
                    f"({', '.join(pairing.missing)}), but the list has "
                    f"{' and '.join(parts)}. The list is left as it is: edit "
                    "it so that it holds the four template entries, then write "
                    "the module again.",
                )
            )
            continue
        repairs.append((name, segment_id_of(entry), pairing.missing))
        targets.append(entry)
    return warnings, repairs, targets


def plan_module(
    module: ConfigurationModuleBase,
    data: VcoModel | Mapping[str, object] | None = None,
    *,
    level: ConfigContext | None = None,
    profile: ConfigurationModuleBase | None = None,
    profile_device_settings: ConfigurationModuleBase | None = None,
    resolvers: IdentityLookup | None = None,
    via_pipeline: bool = False,
    repair_defaults: bool = True,
    conditional_backhaul: Mapping[int, bool] | None = None,
    carried_repairs: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> ModulePlan:
    """Plan the write of *module*, with *data* (default ``module.data``) as its data.

    *level* defaults to the config level of *data*, and of *module* when *data*
    has none (a mapping, or a model built by hand). Give the *profile*'s module
    of the same name to reconcile the segments an edge module lacks (never at
    the profile level).

    At the profile level the module is planned against its raw data plus the
    entries added to it (``module.additions``): an added entry nobody edited
    is written as it was made and is no change, and the plan's ``changes`` are
    relative to it. The refs are not: they are compared with the module as
    read, so an added device settings entry adds its ``deviceSettings:segment``
    row. The segments of a profile are the same in its three modules, so a
    plan of one module alone raises ``SegmentSetError`` for a payload whose
    segments differ from the module as read, and ``HubListError`` for a device
    settings payload that adds a hub to, or removes one from, the hub, bridge
    or backhaul list of a segment (a hub in a list needs its role set on its
    service, which one write cannot do); *via_pipeline* (the pipeline plans
    the three together, which runs the rule that covers all three, and derives
    the role steps) turns both off.

    The refs are derived from *data* (see the module text): *resolvers* finds
    the ``enterpriseObjectId`` of an identity the edit adds, and a plan that
    adds none needs no resolver. Raises ``ResolverNotLoadedError`` (no
    resolver for it) and ``ResolutionError`` (the VCO does not know the
    identity) for a row it cannot build, and ``RefsNotLoadedError`` for an
    edit that needs the refs of a module read without them.
    Raises ``FixedListError`` for a payload that changes a fixed part of a
    ``FixedList`` (the QoS ``defaults``: only their actions can change),
    whatever made the change, relative to the module as read.
    A QoS module at the profile level whose segments lack template ``defaults``
    (see the module text) gets them when it is written, unless *repair_defaults*
    is false; the plan warns either way, and lists them in ``missing_defaults``.
    A repaired entry's ``ruleLogicalId`` is kept on the module
    (``defaults_rule_id``), or is the one *carried_repairs* gives for
    ``(str(segment ID), name)``, which is used as it is. A repaired entry's
    ``action.allowConditionalBh`` is *conditional_backhaul*'s value for the
    segment (``vpn.conditionalBackhaul`` of its device settings), and the
    template action's, which has none, when that is None or lacks the
    segment.
    Raises ``LossyFieldError`` if the caller assigned a value that cannot reach
    the written data (the default of a key that was absent when read).

    A mapping given as *data* is converted as the request would write it (a
    ``UUID`` or a ``datetime`` becomes its text), so the plan holds plain JSON
    that shares nothing with the caller's objects. A mapping has no owners to
    compare, so it changes no ref row; a deviceSettings or QOS module with
    segments still sends its refs as read.
    """
    name = str(_attribute(module, "name"))
    current = data if data is not None else _attribute(module, "data")
    if isinstance(current, Mapping):
        current = _plain_json(cast("Mapping[str, object]", current))
    if level is None and isinstance(current, VcoModel):
        level = current.config_context
    if level is None:  # a mapping, or a model built by hand, has no level of its own
        level = _module_level(module)

    raw = module.raw_data
    if raw is MISSING:  # a module object that never carried data
        payload = (
            wire_dump(current, level)
            if isinstance(current, VcoModel)
            else cast("dict[str, Any]", current)
        )
        model_type = type(current) if isinstance(current, VcoModel) else None
        drop_legacy_keys(payload, name)
        return ModulePlan(
            name=name,
            operation="update",
            data=payload,
            refs=None,
            changes=tuple(raw_changes({}, payload, model_type)),
            reconciled_segments=(),
            model=_document_model(model_type, module),
        )

    inherited = profile if level is not ConfigContext.PROFILE else None
    read = read_data(module, level=level, profile=profile)
    extra, reconciled, planned_from = read.extra, read.reconciled, read.planned_from
    additions = module.additions if level is ConfigContext.PROFILE else ()

    baseline: VcoModel | None
    ref_baseline: VcoModel | None
    if isinstance(current, VcoModel):
        model_type = type(current)
        baseline = read.model(model_type)
        ref_baseline = baseline
        if additions:
            ref_baseline = _reparse(model_type, reconciled, module.raw_mode)
            if module.refs_loaded:
                fill_ref_only(ref_baseline, module.raw_refs)
        before = wire_dump(baseline, level)
        after = wire_dump(current, level)
        carry_unset_defaults(baseline, current, before, after)
        payload = cast(
            "dict[str, Any]",
            copy.deepcopy(
                rebase(
                    planned_from,
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
        baseline = ref_baseline = None
        model_type = None
        topped_up = dict(cast("Mapping[str, Any]", current))
        if extra:  # only for an object, so *reconciled* is one
            topped_up = _with_reconciled_segments(
                topped_up, cast("dict[str, Any]", reconciled), extra
            )
        payload = copy.deepcopy(topped_up)

    if level is ConfigContext.PROFILE and not via_pipeline:
        _check_segment_set(name, raw, payload)
        if name == "deviceSettings":
            _check_hub_lists(name, raw, payload)

    fixed_model = _document_model(model_type, module)
    if fixed_model is not None and payload != planned_from:
        broken = fixed_list_violations(fixed_model, planned_from, payload)
        if broken:
            raise FixedListError(name, broken)

    restored: list[dict[str, Any]] = []
    if inherited is not None:
        # A segment the caller deleted from the edge layer is the profile's again.
        restored = missing_segments(payload, inherited.raw_data, name)
        _add_segments(payload, restored)

    not_template: list[PlanWarning] = []
    repairs: list[tuple[str, object, tuple[str, ...]]] = []
    repair_targets: list[Any] = []
    if level is ConfigContext.PROFILE and name == "QOS":
        not_template, repairs, repair_targets = _defaults_findings(payload)

    derived_refs: dict[str, Any] | None = None
    ref_changes: tuple[RefChange, ...] = ()
    warnings: tuple[PlanWarning, ...] = ()
    hub_services: HubServiceTable | None = None
    segment_ids: dict[int, int] | None = None
    if profile_device_settings is not None:
        hub_refs: object = profile_device_settings.raw_refs
        hub_services = HubServiceTable.from_refs(hub_refs)
        segment_ids = segment_object_ids(hub_refs)
    if name == "QOS" and isinstance(current, VcoModel):
        unserved = missing_service_error(current, as_read=ref_baseline)
        if unserved is not None:
            raise unserved
    if module.refs_loaded:
        derived = derive_refs(
            module,
            ref_baseline,
            current if isinstance(current, VcoModel) else None,
            payload,
            level=level,
            resolvers=resolvers,
            hub_services=hub_services,
            segment_object_ids=segment_ids,
        )
        derived_refs, ref_changes, warnings = (
            derived.refs,
            derived.changes,
            derived.warnings,
        )
    elif ref_baseline is not None and isinstance(current, VcoModel):
        moved = changed_owners(ref_baseline, current, level)
        if moved:
            raise RefsNotLoadedError(name, tuple(dict.fromkeys(o.path for o in moved)))

    changed = payload != reconciled or bool(ref_changes)
    if changed and not module.refs_loaded and sends_refs_always(module, payload):
        raise RefsNotLoadedError(name)
    operation: _Operation = (
        "none" if not changed else "insert" if module.is_new else "update"
    )
    legacy = legacy_key_paths(reconciled, name, level)
    if changed:  # never a write just to strip them
        drop_legacy_keys(payload, name)
    changes = tuple(raw_changes(planned_from, payload, model_type))
    repaired = changed and repair_defaults
    missing_defaults: list[MissingDefaults] = []
    repair_warnings: list[PlanWarning] = []
    for entry, (segment_name, segment_id, names) in zip(
        repair_targets, repairs, strict=True
    ):
        if repaired:
            held = entry.get("defaults")
            entry["defaults"] = insert_defaults(
                held if is_list(held) else None,
                [
                    copy.deepcopy(dict(carried_repairs[(str(segment_id), n)]))
                    if carried_repairs is not None
                    and (str(segment_id), n) in carried_repairs
                    else repaired_entry(
                        n,
                        module.defaults_rule_id(segment_id, n),
                        conditional_backhaul=conditional_backhaul.get(
                            cast("int", segment_id)
                        )
                        if conditional_backhaul is not None
                        else None,
                    )
                    for n in names
                ],
            )
        missing_defaults.append(
            MissingDefaults(segment_name, segment_id, names, repaired=repaired)
        )
        repair_warnings.append(
            PlanWarning(
                "defaults_repaired",
                _defaults_path(segment_name),
                f"The defaults of segment {segment_name!r} lack {', '.join(names)}, "
                "which every QoS segment of a profile holds. "
                + (
                    "This write adds them from the template."
                    if repaired
                    else "The repair is off (repair_defaults is false), so this "
                    "write leaves them out."
                    if not repair_defaults
                    else "This module has nothing else to write, so it is not "
                    "written; a later write of it adds them."
                ),
            )
        )
    flag_off: list[PlanWarning] = []
    layered = _document_model(model_type, module)
    if (
        level is ConfigContext.EDGE
        and layered is not None
        and issubclass(layered, Layered)
    ):
        held = payload if changed else cast("Mapping[str, object]", raw)
        flag_off = [
            _flag_off_warning(found) for found in unflagged_in_use_at(layered, held)
        ]
    in_payload = _segment_names(payload).keys()
    return ModulePlan(
        name=name,
        operation=operation,
        data=payload if changed else copy.deepcopy(cast("dict[str, Any]", raw)),
        refs=derived_refs if changed else None,
        changes=changes,
        reconciled_segments=tuple(
            _segment_name(entry) for entry in [*extra, *restored]
        ),
        legacy_keys=legacy,
        overrides_turned_on=_overrides_turned_on(changes, model_type),
        model=_document_model(model_type, module),
        ref_changes=ref_changes,
        warnings=(*warnings, *not_template, *repair_warnings, *flag_off),
        added_segments=tuple(
            a.name
            for a in additions
            if isinstance(a, AddedSegment) and a.segment_id in in_payload
        ),
        repair_defaults=repair_defaults,
        missing_defaults=tuple(missing_defaults),
        as_read=baseline,
    )


_REBASE_CHANGED = "the module changed on the VCO after this save wrote device settings"
_REBASE_CLASH = f"{_REBASE_CHANGED}, at a path this save also changes; nothing was sent"
_REBASE_LIST = (
    f"{_REBASE_CHANGED}, in a list this save also changes, so its edits cannot be "
    "applied item by item; nothing was sent"
)


def _without_repairs(
    plan: ModulePlan, as_read: object
) -> tuple[dict[str, Any], dict[tuple[str, str], Mapping[str, Any]]]:
    """The payload of *plan* without its repaired ``defaults`` entries, and those entries by ``(str(segment ID), name)``.

    The repaired entries are not the caller's edits: a rebase carries the
    edits and puts the repairs back as they were planned (``carried_repairs``).
    A segment left with no entry has the ``defaults`` it had in *as_read*,
    absent or null.
    """
    data = copy.deepcopy(plan.data)
    carried: dict[tuple[str, str], Mapping[str, Any]] = {}
    segments = data.get("segments")
    read_segments: object = as_read.get("segments") if is_dict(as_read) else None
    for missing in plan.missing_defaults:
        if not missing.repaired:
            continue
        for entry in segments if is_list(segments) else []:
            if not (is_dict(entry) and segment_id_of(entry) == missing.segment_id):
                continue
            kept: list[object] = []
            for rule in cast("list[object]", entry.get("defaults", [])):
                name = rule.get("name") if is_dict(rule) else None
                if is_dict(rule) and name in missing.names:
                    carried[(str(missing.segment_id), cast("str", name))] = cast(
                        "dict[str, Any]", rule
                    )
                else:
                    kept.append(rule)
            if kept:
                entry["defaults"] = kept
                continue
            read = [
                e
                for e in (read_segments if is_list(read_segments) else [])
                if is_dict(e) and segment_id_of(e) == missing.segment_id
            ]
            if read and "defaults" in read[0]:
                entry["defaults"] = read[0]["defaults"]
            else:
                _ = entry.pop("defaults", None)
    return data, carried


def _segments_of(held: object) -> list[object]:
    """The entries of the ``segments`` list of *held*; none for data that has no such list."""
    segments = held.get("segments") if is_dict(held) else None
    return list(segments) if is_list(segments) else []


def _added_segments_held(
    additions: Sequence[SegmentAddition], held: object
) -> list[tuple[str, PlannedChange]]:
    """Each ``AddedSegment`` of *additions* whose ``segmentId`` the segments of *held* hold, with the lost edit that is its entry, as stored there."""
    found: list[tuple[str, PlannedChange]] = []
    for addition in additions:
        if not isinstance(addition, AddedSegment):
            continue
        for entry in _segments_of(held):
            if segment_id_of(entry) == addition.segment_id:
                change = PlannedChange(
                    Path(("segments", Key(addition.name))),
                    MISSING,
                    copy.deepcopy(dict(addition.entry)),
                    copy.deepcopy(entry),
                )
                found.append((addition.name, change))
    return found


def _defaults_without_segment(
    additions: Sequence[SegmentAddition], held: object
) -> list[str]:
    """The names of the segments that an ``AddedDefaults`` of *additions* goes into and the segments of *held* lack."""
    held_ids = {segment_id_of(entry) for entry in _segments_of(held)}
    added_ids = {a.segment_id for a in additions if isinstance(a, AddedSegment)}
    return [
        a.name
        for a in additions
        if isinstance(a, AddedDefaults)
        and a.segment_id not in held_ids
        and a.segment_id not in added_ids
    ]


def rebase_onto[M: ConfigurationModuleBase](
    module: M,
    plan: ModulePlan,
    fresh: ConfigurationModuleBase,
    *,
    resolvers: IdentityLookup | None = None,
    profile_device_settings: ConfigurationModuleBase | None = None,
) -> tuple[M, ModulePlan, tuple[ServerChange, ...]]:
    """*plan*, made for *module* as read, applied to *fresh*, the module as the VCO holds it now.

    Returns the rebased module, its plan and what changed under the caller.
    The module is of the class of *module*, with the raw snapshot, ``version``
    and ``refs_loaded`` of *fresh* and the data of *fresh* with the caller's
    edits (the plan's changes) applied, so that ``plan_module`` of it plans
    exactly those edits relative to *fresh*: the plan is that. At the profile
    level the edits are relative to the module plus its additions (R'), so
    they go onto *fresh* plus the same additions, the rebased module has them
    as its own, and the repaired ``defaults`` entries go back as planned
    (``carried_repairs``). Its operation is ``none`` when *fresh* holds every
    change already. The server changes are what differs between the payload
    and *fresh* outside the plan's changes, each marked ``merged``.

    A planned change whose path holds in *fresh* neither the value read nor
    the value planned is a clash: something else changed it too. So is a list
    whose items another writer added or removed, when the caller edits an item
    of it: the items cannot be matched, and the edit would replace the list
    whole. So is a segment the pipeline added to *module* (``additions``) that
    *fresh* holds already, which is checked first: its entry in *fresh* is the
    clash. Raises ``ConfigConflictError`` (not ``stated``, not ``sent``) for
    any of them, with *fresh* as its module, the planned changes *fresh* does
    not hold as the lost edits, and nothing sent.

    The ref rows are rebased with the data: the rebased module holds *fresh*
    as its raw data and refs, so its plan derives its refs again from the
    caller's edits against the refs *fresh* holds, not from the rows the
    original plan sent. A row the edits add that *fresh* holds already is not
    added twice, and a row the edits remove that *fresh* no longer holds is no
    clash. *resolvers* and *profile_device_settings* are what ``plan_module``
    takes to build the rows the edits add (a QoS rule's backhaul hub finds its
    service in the profile's device settings); the plan's ``ref_changes`` are
    those of the rebased plan.
    """
    from pyvelo.client.module_check import MODULE_REWRITES, data_model  # noqa: PLC0415 (module_check imports this module)

    if not isinstance(fresh, type(module)):
        raise TypeError(
            f"the module read is a {type(fresh).__name__}, not a {type(module).__name__}"
        )
    additions = module.additions
    as_read = {} if module.raw_data is MISSING else module.raw_data
    held = fresh.raw_data
    model = data_model(module)

    def conflict(
        detail: str,
        found: ResponseCheck | None = None,
        *,
        lost_edits: Sequence[PlannedChange] | None = None,
    ) -> ConfigConflictError:
        found = ResponseCheck() if found is None else found
        return ConfigConflictError(
            fresh.id_,
            module.version or "",
            detail,
            stated=False,
            sent=False,
            module_name=plan.name,
            plan=plan,
            module=fresh,
            lost_edits=found.not_applied if lost_edits is None else lost_edits,
            applied=found.applied,
            left_out=found.left_out,
            server_changes=found.server_changes,
            stored_parts=found.stored_parts,
        )

    # What the module holds with the entries the pipeline added to it (R'): the
    # edits of the plan are relative to it, so the rebase works from it onto the
    # fresh data plus the same entries.
    if additions:
        clashes = _added_segments_held(additions, held)
        if clashes:
            names = ", ".join(repr(name) for name, _ in clashes)
            raise conflict(
                f"{_REBASE_CHANGED}: it now holds "
                f"{'segment' if len(clashes) == 1 else 'segments'} {names}, which "
                "this save adds; nothing was sent",
                lost_edits=tuple(change for _, change in clashes),
            )
        lacking = _defaults_without_segment(additions, held)
        if lacking:
            raise conflict(
                f"{_REBASE_CHANGED}: it no longer holds "
                f"{'segment' if len(lacking) == 1 else 'segments'} "
                f"{', '.join(repr(name) for name in lacking)}, which this save "
                "adds defaults to; nothing was sent"
            )
        held = with_additions(held, additions)
        as_read = with_additions(as_read, additions)
    planned_data, carried_repairs = _without_repairs(plan, as_read)
    found = check_response(
        as_read,
        planned_data,
        held,
        model,
        rewrites=MODULE_REWRITES.get(plan.name, Rewrites()),
    )
    if any(change.clash for change in found.not_applied):
        raise conflict(_REBASE_CLASH, found)
    rebased = fresh.with_data_parsed_from(
        rebase(held, as_read, planned_data, model), additions=additions
    )
    rebased_plan = plan_module(
        rebased,
        level=module.config_context,
        via_pipeline=True,
        carried_repairs=carried_repairs,
        repair_defaults=plan.repair_defaults,
        resolvers=resolvers,
        profile_device_settings=profile_device_settings,
    )
    planned = {change.path for change in plan.changes}
    if any(change.path not in planned for change in rebased_plan.changes):
        raise conflict(_REBASE_LIST, found)
    return (
        rebased,
        rebased_plan,
        tuple(replace(change, merged=True) for change in found.server_changes),
    )
