"""The outcome of one module write: what the VCO stored, against what the plan meant to change.

``check_module_write`` runs the response check (``pyvelo.core.response_check``)
on the data and, for an update, ``check_refs`` on the refs, and turns what they
find into a ``ModuleWrite`` or an error. The first of these that fits:

- every planned change is stored: a ``ModuleWrite``;
- the VCO refused the version: ``ConfigConflictError``;
- the write is an insert: ``WriteNotAppliedError``;
- none is stored, and each is as read there: ``ConfigConflictError``;
- anything else: ``WriteNotAppliedError``.

A planned change the VCO stores otherwise than as sent is left out of the
count (``ResponseCheck.left_out``): one to a value it owns, the removal of a
default it adds back, and the removal of a ref row of an append-only name.
``MODULE_REWRITES`` says where, per module, beside the fields marked
``ServerOwned``.
"""

from dataclasses import dataclass
from typing import Any, Final, Literal, cast

from pydantic import MISSING

from pyvelo.client.exceptions import ConfigConflictError, WriteNotAppliedError
from pyvelo.client.module_write import ModulePlan
from pyvelo.client.schemas.configuration import ConfigurationModuleBase
from pyvelo.core import VcoModel
from pyvelo.core.base import (
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
)
from pyvelo.core.rebase import Key, KeyScalar, Path, key_part
from pyvelo.core.response_check import (
    PlannedChange,
    ResponseCheck,
    Rewrites,
    ServerChange,
    check_response,
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

APPEND_ONLY_REF_NAMES: Final = frozenset(
    {"deviceSettings:vnfs:edge", "objectGroup:addressGroup", "objectGroup:portGroup"}
)
"""Ref names whose rows a write does not remove: the removal of one is left out."""

type RowId = tuple[str, object, object]
"""A ref row's identity: its name, ``enterpriseObjectId`` and ``segmentObjectId``."""


def ref_rows(refs: object) -> dict[RowId, dict[str, Any]]:
    """Each row of raw refs, by ``(name, enterpriseObjectId, segmentObjectId)``.

    Raw refs are an object from ref name to one row or a list of rows.
    ``MISSING``, None and a value that is not an object have no rows, and an
    entry that is not an object is no row. An ID a row lacks is None.
    """
    rows: dict[RowId, dict[str, Any]] = {}
    if not _is_dict(refs):
        return rows
    for name, value in refs.items():
        entries = value if _is_list(value) else [value]
        for entry in entries:
            if _is_dict(entry):
                row = cast(dict[str, Any], entry)
                identity = (
                    str(name),
                    row.get("enterpriseObjectId"),
                    row.get("segmentObjectId"),
                )
                rows[identity] = row
    return rows


def _id_part(value: object) -> KeyScalar:
    """One ID of a row, as a part of its path; ``null`` for none."""
    return "null" if value is None else key_part(value)


def _row_path(identity: RowId) -> Path:
    """``name[enterpriseObjectId]``, or ``name[enterpriseObjectId,segmentObjectId]`` for a row with a segment."""
    name, object_id, segment_id = identity
    if segment_id is None:
        return Path((name, Key(_id_part(object_id))))
    return Path((name, Key((_id_part(object_id), _id_part(segment_id)))))


def check_refs(as_read: object, sent: object | None, stored: object) -> ResponseCheck:
    """Compare the refs the VCO stored with what a write meant to change in them.

    Rows are compared by identity (``ref_rows``) only: their other fields,
    their own IDs among them, change on every write. Each entry has
    ``part="refs"``, the path ``_row_path`` gives, and rows for values.

    *sent* None means refs were not sent, and nothing is planned. Otherwise
    a row of *sent* that is not as read was added, and is applied when it is
    stored; a row as read that is not in *sent* was removed, and is applied
    when it is not stored, or left out under an ``APPEND_ONLY_REF_NAMES``
    name. Either way a row as read and planned to stay that is not stored,
    and a stored row that is neither, are server changes. When *as_read* is
    ``MISSING`` and *sent* None the refs were never read, so a stored row is
    no change: the result is empty.
    """
    if as_read is MISSING and sent is None:
        return ResponseCheck()
    read, kept = ref_rows(as_read), ref_rows(stored)
    planned = read if sent is None else ref_rows(sent)
    applied: list[PlannedChange] = []
    not_applied: list[PlannedChange] = []
    left_out: list[PlannedChange] = []
    for identity, row in planned.items():
        if identity not in read:
            added = PlannedChange(
                _row_path(identity),
                MISSING,
                row,
                kept.get(identity, MISSING),
                part="refs",
            )
            (applied if identity in kept else not_applied).append(added)
    for identity, row in read.items():
        if identity not in planned:
            removed = PlannedChange(
                _row_path(identity),
                row,
                MISSING,
                kept.get(identity, MISSING),
                part="refs",
            )
            if identity[0] in APPEND_ONLY_REF_NAMES:
                left_out.append(removed)
            else:
                (not_applied if identity in kept else applied).append(removed)
    server_changes = [
        *(
            ServerChange(_row_path(identity), row, MISSING, part="refs")
            for identity, row in read.items()
            if identity in planned and identity not in kept
        ),
        *(
            ServerChange(_row_path(identity), MISSING, row, part="refs")
            for identity, row in kept.items()
            if identity not in read and identity not in planned
        ),
    ]
    return ResponseCheck(
        applied=tuple(applied),
        not_applied=tuple(not_applied),
        left_out=tuple(left_out),
        server_changes=tuple(server_changes),
    )


def data_model(module: ConfigurationModuleBase) -> type[VcoModel] | None:
    """The class of the module's data, when it is a model."""
    data: object = getattr(module, "data", None)
    return type(data) if isinstance(data, VcoModel) else None


def check_module_write[M: ConfigurationModuleBase](
    module: M,
    plan: ModulePlan,
    stored: ConfigurationModuleBase,
    *,
    refused: bool = False,
) -> ModuleWrite[M]:
    """The outcome of writing *plan* to *module*, which the VCO now holds as *stored*.

    *stored* is the module the VCO returned for the write, or read after it;
    it is of the class of *module*. *refused* says the VCO refused the
    write's ``version``, so *stored* is from the read after the refusal.

    The data is checked against the raw data as read (without reconciled
    segments, so those are planned changes too) and, for an update, the refs
    against the refs as read. Returns a ``ModuleWrite`` with *stored* when
    every planned change is stored. Otherwise raises ``ConfigConflictError``
    when the VCO refused the version, or when nothing is stored and the
    values there are as read (a refused stale version, from a VCO that does
    not say so), and ``WriteNotAppliedError`` for anything else and for an
    insert, which carries no version.
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
    refs = (
        check_refs(module.raw_refs, plan.refs, stored.raw_refs)
        if plan.operation == "update"
        else ResponseCheck()
    )
    applied = (*data.applied, *refs.applied)
    lost_edits = (*data.not_applied, *refs.not_applied)
    left_out = (*data.left_out, *refs.left_out)
    server_changes = (*data.server_changes, *refs.server_changes)
    if not lost_edits:
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
    detail = _conflict(refused, plan, applied, lost_edits)
    if detail is not None:
        raise ConfigConflictError(
            stored.id_,
            version,
            detail,
            module_name=plan.name,
            plan=plan,
            module=stored,
            lost_edits=lost_edits,
            applied=applied,
            left_out=left_out,
            server_changes=server_changes,
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
    )


def _conflict(
    refused: bool,
    plan: ModulePlan,
    applied: tuple[PlannedChange, ...],
    lost_edits: tuple[PlannedChange, ...],
) -> str | None:
    """Why a write with *lost_edits* is a conflict, the ``detail``; None when it is not one."""
    if refused:
        return "the VCO refused the version"
    if plan.operation == "insert":
        return None
    if not applied and not any(change.clash for change in lost_edits):
        return "none of the planned changes is stored, and the values there are as read"
    return None
