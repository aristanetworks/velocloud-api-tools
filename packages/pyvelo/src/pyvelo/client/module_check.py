"""The outcome of one module write: what the VCO stored, against what the plan meant to change.

``check_module_write`` runs the response check (``pyvelo.core.response_check``)
on the data and ``check_refs`` on the refs, and turns what they find into a
``ModuleWrite`` or an error, by what the VCO said of the write
(``WriteAnswer``):

- ``"refused"``: a ``ModuleWrite`` only when a change is found stored and none
  is lost or left out (a write sent twice whose first copy was stored);
  otherwise ``ConfigConflictError``, which lists no lost edit when the check
  cannot tell what is stored;
- ``"stale"``: nothing was sent, because the module's version on the VCO is not
  the one read; the same rule as ``"refused"``, and the
  ``ConfigConflictError`` says that nothing was sent (``sent`` is False);
- ``"row"`` or ``"counted"``: a ``ModuleWrite`` when no change is lost;
  otherwise, the first that fits: ``WriteNotAppliedError`` for an insert;
  ``ConfigConflictError`` for a row with none of the changes stored and each
  value as read; ``WriteNotAppliedError`` for anything else and for every
  counted write. When every change not stored is a ref row, that is
  ``RefsDroppedError``, a ``WriteNotAppliedError``.

A planned change the VCO stores otherwise than as sent is left out of the
count (``ResponseCheck.left_out``): one to a value it owns, and the removal of
a default it adds back. A ref row is never left out: one the plan adds or
removes is applied or lost, and the rows of the names the VCO's processors
manage (``PROCESSOR_REF_NAMES``) or only adds to (``APPEND_ONLY_REF_NAMES``)
are not compared, and a difference there is a server change.
After a refusal, and when a row holds none of the changes, a left-out change
that is still as read is a lost edit instead: the write did not reach it.
``MODULE_REWRITES`` says where, per module, beside the fields marked
``ServerOwned``.
"""

import copy
from dataclasses import dataclass
from typing import Any, Final, Literal, cast, override

from pydantic import MISSING

from pyvelo.client.derived_refs import (
    APPEND_ONLY_REF_NAMES,
    PROCESSOR_REF_NAMES,
    RefChange,
    row_field,
    same_identity,
)
from pyvelo.client.exceptions import (
    ConfigConflictError,
    RefsDroppedError,
    WriteAnswer,
    WriteNotAppliedError,
)
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import ConfigurationModuleBase
from pyvelo.core import VcoModel
from pyvelo.core.base import (
    is_dict,
    is_list,
)
from pyvelo.core.rebase import Key, Path, key_part
from pyvelo.core.response_check import (
    PlannedChange,
    ResponseCheck,
    Rewrites,
    ServerChange,
    check_response,
    matches,
)


@dataclass(frozen=True)
class ModuleWrite[M: ConfigurationModuleBase]:
    """A module write that is stored as planned."""

    name: str
    operation: Literal["none", "update", "insert"]
    module: M
    """The module as stored; the module given when nothing was sent."""
    plan: ModulePlan
    applied: tuple[PlannedChange, ...] = ()
    left_out: tuple[PlannedChange, ...] = ()
    """Planned changes the VCO stores otherwise than as sent (``ResponseCheck.left_out``)."""
    server_changes: tuple[ServerChange, ...] = ()

    @override
    def __repr__(self) -> str:
        return (
            f"ModuleWrite(name={self.name!r}, operation={self.operation!r}, "
            f"applied={len(self.applied)}, left_out={len(self.left_out)}, "
            f"server_changes={len(self.server_changes)})"
        )


MODULE_REWRITES: Final[dict[str, Rewrites]] = {
    "deviceSettings": Rewrites(
        # The edge client connector's name is derived; the models do not have it.
        server_owned=(("**", "clientConnector", "name"),),
        readded=(
            ("segments", "*", "ecmp"),
            ("segments", "*", "ospf", "v6Detail"),
            ("segments", "*", "bgp", "v6Detail", "ospf"),
            # No path is known for fips, so it is matched anywhere.
            ("**", "fips"),
        ),
        ipv6=True,
    ),
}
"""Per module name, the rewrites of the VCO that no model field is marked for.

Seeded from what is known of the VCO's rewrites; a rewrite a live test finds
is added here. A firewall rule's ``ruleLogicalId`` and a WAN link's
``internalId`` are ``ServerOwned`` fields instead.
"""

type RowId = tuple[str, object, object]
"""A ref row's identity: its name, ``logicalId`` and ``segmentLogicalId``; a field a row lacks is None."""

_NOT_COMPARED = PROCESSOR_REF_NAMES | APPEND_ONLY_REF_NAMES
"""Ref names whose rows are never applied or lost: their difference is a server change."""


def _entries(refs: object) -> list[tuple[str, dict[str, Any]]]:
    """Each row of raw refs with its name, in order.

    Raw refs are an object from ref name to one row or a list of rows.
    ``MISSING``, None and a value that is not an object have no rows, and an
    entry that is not an object is no row.
    """
    entries: list[tuple[str, dict[str, Any]]] = []
    if not is_dict(refs):
        return entries
    for name, value in refs.items():
        entries.extend(
            (str(name), cast("dict[str, Any]", entry))
            for entry in (value if is_list(value) else [value])
            if is_dict(entry)
        )
    return entries


def _identity(name: str, row: dict[str, Any]) -> RowId:
    return (name, row.get("logicalId"), row.get("segmentLogicalId"))


def _by_identity(
    entries: list[tuple[str, dict[str, Any]]],
) -> dict[RowId, dict[str, Any]]:
    return {_identity(name, row): row for name, row in entries}


def _key(identity: object, segment: object) -> Key:
    """The key of a refs entry: the row's identity, then its segment when it has one."""
    first = "null" if identity is None else key_part(identity)
    if segment is None:
        return Key(first)
    return Key((first, key_part(segment)))


def _row_path(identity: RowId) -> Path:
    """``name[logicalId]``, or ``name[logicalId,segmentLogicalId]`` for a row with a segment."""
    name, logical_id, segment_logical_id = identity
    return Path((name, _key(logical_id, segment_logical_id)))


def _change_path(change: RefChange) -> Path:
    """``name[value]``, or ``name[value,segmentObjectId]`` for a row of a segment, of a row the plan adds or removes."""
    return Path((change.name, _key(change.value, change.segment_object_id)))


def _stored_row(
    change: RefChange, entries: list[tuple[str, dict[str, Any]]]
) -> dict[str, Any] | None:
    """The stored row an added row is: one under its name whose ``match`` field holds its ``value`` and, per segment, its ``segmentObjectId``."""
    return next(
        (
            row
            for name, row in entries
            if name == change.name
            and same_identity(
                change.value, row_field(cast("dict[object, object]", row), change.match)
            )
            and (
                change.segment_object_id is None
                or row.get("segmentObjectId") == change.segment_object_id
            )
        ),
        None,
    )


def check_refs(
    module: ConfigurationModuleBase,
    plan: ModulePlan,
    stored: ConfigurationModuleBase,
) -> ResponseCheck:
    """Compare the refs the VCO stored with what the write of *plan* meant to change in them.

    The refs as read are those of *module*. Rows are compared by identity,
    ``(name, logicalId, segmentLogicalId)``, and never by content: the other
    fields, a row's own ID among them, change on every write. Each entry has
    ``part="refs"``, a path keyed by the row's identity, and rows for values.

    A row the plan added (``plan.ref_changes``) is applied when the stored refs
    hold, under its name, a row whose ``match`` field holds its ``value`` and,
    for a row of a segment, its ``segmentObjectId``; otherwise it is lost. A
    row the plan removed is applied when no stored row has its identity, and
    lost when one has. A row of ``PROCESSOR_REF_NAMES`` or
    ``APPEND_ONLY_REF_NAMES`` is neither: each one that differs between as
    read and stored is a server change.

    A row sent as read (or, when no refs were sent, any row as read) that the
    stored refs lack is a server change: the VCO dropped it on its own. When
    the stored refs hold none of the rows sent outside the two sets of names,
    and at least one was sent, each row sent as read is a lost edit instead:
    every row was lost. A stored row that is neither as read nor added is a
    server change. Nothing is checked when no refs were sent and none were
    read, or when *stored* was read without its refs.
    """
    as_read = module.raw_refs
    if (plan.refs is None and as_read is MISSING) or not stored.refs_loaded:
        return ResponseCheck()
    read_entries = _entries(as_read)
    read = _by_identity(read_entries)
    kept_entries = _entries(stored.raw_refs)
    kept = _by_identity(kept_entries)

    applied: list[PlannedChange] = []
    not_applied: list[PlannedChange] = []
    server_changes: list[ServerChange] = []
    matched: list[dict[str, Any]] = []
    for change in plan.ref_changes:
        if change.name in _NOT_COMPARED:
            continue
        if change.added:
            found = _stored_row(change, kept_entries)
            if found is not None:
                matched.append(found)
            made = PlannedChange(
                _change_path(change),
                MISSING,
                change.row,
                MISSING if found is None else found,
                part="refs",
                owner=change.owner,
            )
            (not_applied if found is None else applied).append(made)
        else:
            held = kept.get(_identity(change.name, change.row))
            gone = PlannedChange(
                _change_path(change),
                change.row,
                MISSING,
                MISSING if held is None else held,
                part="refs",
                owner=change.owner,
            )
            (applied if held is None else not_applied).append(gone)

    expected = [(n, r) for n, r in read_entries if n not in _NOT_COMPARED]
    every_row_lost = False
    if plan.refs is not None:
        added = [change.row for change in plan.ref_changes if change.added]
        sent = [(n, r) for n, r in _entries(plan.refs) if n not in _NOT_COMPARED]
        expected = [(n, r) for n, r in sent if r not in added]
        every_row_lost = (
            bool(sent)
            and not matched
            and not any(_identity(n, r) in kept for n, r in expected)
        )
    for name, row in expected:
        identity = _identity(name, row)
        if identity in kept:
            continue
        if every_row_lost:
            not_applied.append(
                PlannedChange(_row_path(identity), row, row, MISSING, part="refs")
            )
        else:
            server_changes.append(
                ServerChange(_row_path(identity), row, MISSING, part="refs")
            )
    for name, row in read_entries:
        identity = _identity(name, row)
        if name in _NOT_COMPARED and identity not in kept:
            server_changes.append(
                ServerChange(_row_path(identity), row, MISSING, part="refs")
            )
    for name, row in kept_entries:
        identity = _identity(name, row)
        if identity in read or any(row is found for found in matched):
            continue
        server_changes.append(
            ServerChange(_row_path(identity), MISSING, row, part="refs")
        )
    return ResponseCheck(
        applied=tuple(applied),
        not_applied=tuple(not_applied),
        server_changes=tuple(server_changes),
    )


def data_model(module: ConfigurationModuleBase) -> type[VcoModel] | None:
    """The class of the module's data, when it is a model."""
    data: object = getattr(module, "data", None)
    return type(data) if isinstance(data, VcoModel) else None


def has_checkable_change(module: ConfigurationModuleBase, plan: ModulePlan) -> bool:
    """Whether *plan* has a change ``check_module_write`` can find stored.

    True when the data has a planned change that is not the removal of a
    default the VCO adds back (``MODULE_REWRITES``), taking the payload as
    stored exactly as sent (a change to a value the VCO owns is then left out
    in the same way as in the check itself), or when the plan adds or removes
    a ref row (``ref_changes``). A write with none of these cannot tell a
    stored write from a stale one by the module it gets back.
    """
    as_read = module.raw_data
    rewrites = MODULE_REWRITES.get(plan.name, Rewrites())
    data = check_response(
        {} if as_read is MISSING else as_read,
        plan.data,
        plan.data,
        data_model(module),
        rewrites=rewrites,
    )
    if any(
        not (
            change.intended is MISSING
            and any(matches(change.path, pattern) for pattern in rewrites.readded)
        )
        for change in data.applied
    ):
        return True
    return bool(plan.ref_changes)


def _refs_to_put_back(raw_refs: object) -> object:
    """The module's refs as read, less the rows of ``APPEND_ONLY_REF_NAMES``.

    Those rows are the VCO's own: sending them back would store them twice. A
    value that is not a dict of refs (``MISSING``) is returned as it is.
    """
    if not is_dict(raw_refs):
        return raw_refs
    return {
        name: copy.deepcopy(rows)
        for name, rows in raw_refs.items()
        if name not in APPEND_ONLY_REF_NAMES
    }


def check_module_write[M: ConfigurationModuleBase](
    module: M,
    plan: ModulePlan,
    stored: ConfigurationModuleBase,
    *,
    answer: WriteAnswer = "row",
) -> ModuleWrite[M]:
    """The outcome of writing *plan* to *module*, which the VCO now holds as *stored*.

    *stored* is the module the VCO returned for the write, or read after it;
    it is of the class of *module*. *answer* says what the VCO said of the
    write (``WriteAnswer``); after ``"refused"``, *stored* is from the read
    after the refusal, and for ``"stale"``, which sends nothing, from the read
    that found the version changed.

    The data is checked against the raw data as read (without reconciled
    segments, so those are planned changes too) and the refs of *module*
    against the refs *stored* holds (``check_refs``), for an insert as for an
    update.

    After ``"refused"`` or ``"stale"``, or ``"row"`` with no change found
    stored, a left-out change whose stored value is the one read is a lost
    edit: the write did not reach it. A refused or stale write is returned only
    when a change is found stored and none is lost or left out. A ``"row"`` or ``"counted"`` write
    is returned when no change is lost. Otherwise this raises
    ``ConfigConflictError`` after a refusal (``stated``) or for a stale write
    (not ``stated``, and not ``sent``), for an insert
    ``WriteNotAppliedError``, ``ConfigConflictError`` (not ``stated``) for a
    row with none of the changes stored and each value as read, and
    ``WriteNotAppliedError`` for anything else and for every ``"counted"``
    write. ``RefsDroppedError`` takes the place of ``WriteNotAppliedError``
    when every lost edit is a ref row. After a ``"row"`` answer to an update
    it needs a change applied (a data edit beside the ref change): with none,
    the write is a conflict. An insert and a ``"counted"`` write are never
    inferred to be conflicts, so they raise it with none applied too.
    """
    if not isinstance(stored, type(module)):
        raise TypeError(
            f"the stored module is a {type(stored).__name__}, "
            f"not a {type(module).__name__}"
        )
    as_read = module.raw_data
    data = check_response(
        {} if as_read is MISSING else as_read,
        plan.data,
        stored.raw_data,
        data_model(module),
        rewrites=MODULE_REWRITES.get(plan.name, Rewrites()),
    )
    refs = check_refs(module, plan, stored)
    applied = (*data.applied, *refs.applied)
    lost_edits = (*data.not_applied, *refs.not_applied)
    left_out = data.left_out
    server_changes = (*data.server_changes, *refs.server_changes)
    not_reached = answer in ("refused", "stale")
    if not_reached or (answer == "row" and not applied):
        # The write did not reach a left-out change that is still as read.
        lost_edits = (*lost_edits, *(c for c in left_out if not c.clash))
        left_out = tuple(c for c in left_out if c.clash)
    stored_whole = (
        bool(applied) and not lost_edits and not left_out
        if not_reached
        else not lost_edits
    )
    if stored_whole:
        return ModuleWrite(
            name=plan.name,
            operation=plan.operation,
            module=stored,
            plan=plan,
            applied=applied,
            left_out=left_out,
            server_changes=server_changes,
        )

    version = module.version or ""
    refs_only = bool(plan.ref_changes) and not (
        data.applied or data.not_applied or data.left_out
    )
    detail = _conflict(answer, plan, applied, lost_edits, refs_only=refs_only)
    if detail is not None:
        raise ConfigConflictError(
            stored.id_,
            version,
            detail,
            stated=answer == "refused",
            sent=answer != "stale",
            module_name=plan.name,
            plan=plan,
            module=stored,
            lost_edits=lost_edits,
            applied=applied,
            left_out=left_out,
            server_changes=server_changes,
            stored_parts=data.stored_parts,
        )
    if lost_edits and all(change.part == "refs" for change in lost_edits):
        raise RefsDroppedError(
            stored.id_,
            version,
            module_name=plan.name,
            plan=plan,
            module=stored,
            lost_edits=lost_edits,
            applied=applied,
            left_out=left_out,
            server_changes=server_changes,
            stored_parts=data.stored_parts,
            refs_as_read=_refs_to_put_back(module.raw_refs),
        )
    raise WriteNotAppliedError(
        stored.id_,
        version,
        module_name=plan.name,
        plan=plan,
        module=stored,
        lost_edits=lost_edits,
        applied=applied,
        left_out=left_out,
        server_changes=server_changes,
        stored_parts=data.stored_parts,
    )


def _conflict(
    answer: WriteAnswer,
    plan: ModulePlan,
    applied: tuple[PlannedChange, ...],
    lost_edits: tuple[PlannedChange, ...],
    *,
    refs_only: bool,
) -> str | None:
    """Why a write that is not stored whole is a conflict, the ``detail``; None when it is not one.

    *refs_only* is a write whose planned changes are all ref rows: no change
    is stored then, and the refs are as read, either because the VCO refused
    the version or because it would not take a new row.
    """
    if answer == "refused":
        return "the VCO refused the version"
    if answer == "stale":
        return (
            "the module's version on the VCO is not the one read, and the "
            "module there does not hold this write's changes; nothing was "
            "sent: redo the lost edits on error.module and save again"
        )
    if plan.operation == "insert" or answer == "counted":
        return None
    if not applied and not any(change.clash for change in lost_edits):
        if refs_only:
            return (
                "none of the planned ref rows is stored, and the refs are as "
                "read: either the module changed since it was read, or the VCO "
                "would not take a new row because it cannot resolve its object "
                "(read the module again, check that the object exists, and make "
                "the change again)"
            )
        return "none of the planned changes is stored, and the values there are as read"
    return None
