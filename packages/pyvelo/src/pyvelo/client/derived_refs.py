# The cycle is through ``pyvelo.client.exceptions``, whose annotation-only imports name
# ``module_check``, which imports this module for ``APPEND_ONLY_REF_NAMES``.
# pyright: reportImportCycles=false
"""The ref rows an edit adds and removes, and the complete refs to send, by difference of the declared owners.

A ref row goes with an identity that the data holds (``OwnsRef``) or that only
the row says (``RefOnly``). ``derive_refs`` compares the owners of a
module as read with the owners now: an owner that appeared adds a row, one that
went removes the row it showed, and every other row is sent as read. What
already disagrees as read (a row with no owner, an owner with no row, several
rows for one provider) is reported as a ``PlanWarning`` and left as it is, so a
module nobody edited never has its refs rewritten.

The module class is imported for type checking only: ``pyvelo.client.schemas``
imports ``ref_owners``, and this module is imported by ``module_check``.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from pyvelo.client.enums.enterprise import EnterpriseServiceType
from pyvelo.client.hub_services import HubServiceTable, hub_name
from pyvelo.client.models.configuration.ref_owners import (
    RefOwner,
    SegmentRef,
    data_segments,
    declared_ref_names,
    ref_owners,
    row_is_of_segment,
    rows_named,
    segment_object_ids,
)
from pyvelo.core import ConfigContext, OwnsRef, RefOnly, VcoModel
from pyvelo.core.base import IdentityLookup, is_dict, is_list
from pyvelo.core.identity import (
    HubService,
    HubServiceNotFoundError,
    NetworkServiceIdentity,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
    entity_label,
)
from pyvelo.core.rebase import Path
from pyvelo.core.show import patch_text

if TYPE_CHECKING:
    from pyvelo.client.schemas.configuration import ConfigurationModuleBase

APPEND_ONLY_REF_NAMES: Final = frozenset(
    {"deviceSettings:vnfs:edge", "objectGroup:addressGroup", "objectGroup:portGroup"}
)
"""Ref names whose rows the VCO keeps itself: a write sends none of their rows.

A row of one of these names is never planned as removed and never ``left_out``;
it is left out of the refs sent, and the VCO stores the ones it holds again.
"""

SERVICE_TYPES: Final[Mapping[str, str]] = {
    "deviceSettings:dns:primaryProvider": EnterpriseServiceType.DNS.value,
    "deviceSettings:dns:backupProvider": EnterpriseServiceType.DNS.value,
    "deviceSettings:authentication": EnterpriseServiceType.AUTHENTICATION.value,
    "deviceSettings:css:provider": EnterpriseServiceType.CLOUD_SECURITY_SERVICE.value,
}
"""The type of network service each provider ref names, a tag of ``EnterpriseServiceType``.

A new row of one of these names whose service is of another type is refused:
the VCO would store a row that names a service the field cannot use.
"""

PROCESSOR_REF_NAMES: Final = frozenset(
    {
        "deviceSettings:handOffGateways:gateways",
        "deviceSettings:handOffControllers:gateways",
        "deviceSettings:css:site",
        "deviceSettings:vnfs:edge",
        "deviceSettings:securityVnf:service",
        "deviceSettings:securityVnf:license",
        "deviceSettings:vnfs:vnfImage",
        "deviceSettings:edgeDirectNvs:site",
        "deviceSettings:zscaler:iaasSubscription",
        "deviceSettings:zscaler:mtgreSite",
        "deviceSettings:zscaler:location",
        "deviceSettings:zscaler:subLocation",
        "deviceSettings:wssIntegration",
    }
)
"""Ref names the VCO's inline processors manage.

Their rows are passed through as read and are not compared after the write; a
difference is a server change. ``deviceSettings:vnfs:edge`` is also
append-only, and that wins when the refs to send are built.
"""

ALWAYS_SEND_REFS: Final = frozenset({"deviceSettings", "QOS"})
"""Modules whose write always carries their complete refs when they have segments.

The VCO runs its processors on every write of such a module, whatever changed;
a write without refs could make them replace the module's whole association set.
"""

_ONLY_THE_EDIT = "the plan changes only what the edit adds or removes"


@dataclass(frozen=True)
class PlanWarning:
    """Something about the module as read that disagrees, and that the plan leaves as it is."""

    code: str
    """``"ref_without_owner"``, ``"owner_without_ref"`` or ``"several_refs"``."""
    path: str
    """``refs <name> (<segment name>)``, or ``refs <name>`` without a segment."""
    message: str


@dataclass(frozen=True)
class RefChange:
    """A ref row an edit adds or removes."""

    added: bool
    name: str
    segment: str | None
    """The segment's name, for a per-segment ref."""
    row: dict[str, Any]
    """The new row, or the row as read that goes."""
    match: str
    """The row field the identity is at: ``"logicalId"`` or ``"data.segmentId"``."""
    value: object
    """The identity as JSON: a UUID as lower-case text."""
    segment_object_id: int | None
    owner: Path | None
    """The owner's path in python names for a ``RefOnly`` owner, else ``None``."""


@dataclass(frozen=True)
class DerivedRefs:
    refs: dict[str, Any] | None
    """The complete refs to send, or ``None`` when none go."""
    changes: tuple[RefChange, ...]
    warnings: tuple[PlanWarning, ...]


type _Key = tuple[str, object, int | None]
"""An owner's identity for the difference: ref name, identity, ``segmentId``."""


@dataclass(frozen=True)
class _Context:
    """What matching a row to an owner needs."""

    object_ids: dict[int, int]
    logical_ids: dict[int, uuid.UUID | None]

    def segment(self, owner: RefOwner) -> SegmentRef | None:
        """The segment an owner's row is of; ``None`` for an owner with no segment."""
        if not _is_per_segment(owner):
            return None
        segment_id = owner.segment_id
        return SegmentRef(
            segment_id,
            owner.segment_name,
            None if segment_id is None else self.logical_ids.get(segment_id),
        )


def _object_ids(raw_refs: object, given: Mapping[int, int] | None) -> dict[int, int]:
    """``segmentId`` to the segment's object ID: the module's own segment rows, over those *given*."""
    return {**(given or {}), **segment_object_ids(raw_refs)}


def _is_per_segment(owner: RefOwner) -> bool:
    marker = owner.marker
    return isinstance(marker, RefOnly) or marker.per_segment


def _match_field(owner: RefOwner) -> str:
    """The row field the owner's identity is at."""
    marker = owner.marker
    return marker.row if isinstance(marker, OwnsRef) else "logicalId"


def _by(owner: RefOwner) -> str:
    """The attribute of the owner's identity type that its value is."""
    marker = owner.marker
    return marker.by if isinstance(marker, OwnsRef) else "logical_id"


def _json_value(value: object) -> object:
    """An identity as JSON: a UUID as lower-case text."""
    return str(value).lower() if isinstance(value, uuid.UUID) else value


def row_field(row: dict[object, object], field: str) -> object:
    """The value at the dotted *field* of *row*, or ``None``."""
    value: object = row
    for step in field.split("."):
        if not is_dict(value):
            return None
        value = value.get(step)
    return value


def same_identity(identity: object, held: object) -> bool:
    """Whether *held* (a row's value) is *identity*: UUIDs as lower-case text, ints as ints.

    *identity* may be a ``UUID`` or its text (as ``RefChange.value`` holds it).
    """
    if isinstance(identity, uuid.UUID | str):
        return isinstance(held, str) and held.lower() == str(identity).lower()
    return (
        isinstance(identity, int)
        and isinstance(held, int)
        and not isinstance(held, bool)
        and held == identity
    )


def _row_matches(owner: RefOwner, row: dict[object, object], context: _Context) -> bool:
    return same_identity(owner.value, row_field(row, _match_field(owner))) and (
        row_is_of_segment(row, context.segment(owner), context.object_ids)
    )


def _key(owner: RefOwner) -> _Key:
    return (owner.name, owner.value, owner.segment_id)


def _by_key(owners: tuple[RefOwner, ...]) -> dict[_Key, RefOwner]:
    found: dict[_Key, RefOwner] = {}
    for owner in owners:
        _ = found.setdefault(_key(owner), owner)
    return found


def _warning_path(name: str, segment_name: str | None) -> str:
    return f"refs {name}" if segment_name is None else f"refs {name} ({segment_name})"


def _unknown_identity(owner: RefOwner, caught: ResolutionError) -> ResolutionError:
    """The error for an owner whose identity the resolver does not know."""
    label = entity_label(owner.identity_type)
    marker = owner.marker
    if isinstance(marker, OwnsRef) and marker.value is not None:
        advice = (
            f"Use a {label} that exists: add the {label} to the profile first, "
            + "or give the segment entry the segment ID of one that does."
        )
    else:
        advice = (
            f"Give the name or the logical ID of a {label} that exists: "
            + f"{patch_text(owner.path)} sets it."
        )
    return ResolutionError(
        name=caught.name,
        identity_type=owner.identity_type,
        candidates=[],
        message=(
            f"{owner.path} names the {label} {owner.value}, which the VCO does "
            + f"not have, so the row it goes with cannot be added. {advice}"
        ),
    )


def _identity(
    identity_type: type, by: str, value: object, resolvers: IdentityLookup | None
) -> object:
    """The identity of *identity_type* whose *by* is *value*."""
    if resolvers is None:
        raise ResolverNotLoadedError(identity_type)
    return resolvers.identity_sync(cast(type[object], identity_type), by, value)


def _object_id(identity: object) -> int:
    """The ``object_id`` of *identity*."""
    found: object = getattr(identity, "object_id", None)
    if not isinstance(found, int):  # pragma: no cover - an identity holds an int
        raise TypeError(f"{type(identity).__name__}.object_id is not an int: {found!r}")
    return found


def _wrong_service_type(
    owner: RefOwner, service: NetworkServiceIdentity
) -> ResolutionError:
    """The error for an owner of a provider ref whose service is of another type."""
    wanted = SERVICE_TYPES[owner.name]
    return ResolutionError(
        name=service.name,
        identity_type=NetworkServiceIdentity,
        candidates=[],
        message=(
            f"{owner.path} names the service {service.name!r}, which is of type "
            + f"{service.type_!r}, and the field wants a service of type "
            + f"{wanted!r}, so the row it goes with would name a service it "
            + "cannot use. Give the name or the logical ID of a service of type "
            + f"{wanted!r}: {patch_text(owner.path)} sets it."
        ),
    )


def _segment_object_id(
    owner: RefOwner, context: _Context, resolvers: IdentityLookup | None
) -> int:
    """The ``segmentObjectId`` of the row for *owner*: from the segment rows the module has or was given, else the segment resolver."""
    found = (
        None if owner.segment_id is None else context.object_ids.get(owner.segment_id)
    )
    if found is not None:
        return found
    try:
        return _object_id(
            _identity(SegmentIdentity, "segment_id", owner.segment_id, resolvers)
        )
    except ResolverNotLoadedError:
        raise
    except ResolutionError as caught:
        raise ResolutionError(
            name=caught.name,
            identity_type=SegmentIdentity,
            candidates=[],
            message=(
                f"{owner.path} sits in the segment with segment ID "
                + f"{owner.segment_id}, which the VCO does not have, so "
                + "the row cannot be added. Add the segment to the profile "
                + "first, or use one that exists."
            ),
        ) from caught


def _no_service(
    owner: RefOwner, hub_services: HubServiceTable | None
) -> HubServiceNotFoundError:
    """The error for an owner whose hub has no service in its segment of the profile."""
    error = HubServiceNotFoundError(
        hub=hub_name(owner.holder),
        segment=owner.segment_name or str(owner.segment_id),
        path=str(owner.path),
    )
    if hub_services is not None:
        return error
    return HubServiceNotFoundError(
        hub=error.hub,
        segment=error.segment,
        path=error.path,
        message=(
            f"{error} No device settings of the profile were given to look "
            + "in: pass the profile's device settings module as "
            + "profile_device_settings= (a ConfigState does)."
        ),
    )


def _new_row(
    module: ConfigurationModuleBase,
    owner: RefOwner,
    context: _Context,
    resolvers: IdentityLookup | None,
    hub_services: HubServiceTable | None,
) -> tuple[dict[str, Any], int | None]:
    """The row to add for *owner* and its ``segmentObjectId`` (``None`` for a ref with no segment)."""
    segment_object_id: int | None = None
    if owner.identity_type is HubService:
        if hub_services is None:
            raise _no_service(owner, hub_services)
        segment_object_id = _segment_object_id(owner, context, resolvers)
        service = (
            hub_services.find(segment_object_id, owner.value)
            if isinstance(owner.value, uuid.UUID)
            else None
        )
        if service is None:
            raise _no_service(owner, hub_services)
        object_id = service.object_id
    else:
        try:
            identity = _identity(
                owner.identity_type, _by(owner), owner.value, resolvers
            )
        except ResolverNotLoadedError:
            raise
        except ResolutionError as caught:
            raise _unknown_identity(owner, caught) from caught
        wanted = SERVICE_TYPES.get(owner.name)
        if (
            wanted is not None
            and isinstance(identity, NetworkServiceIdentity)
            and identity.type_ != wanted
        ):
            raise _wrong_service_type(owner, identity)
        object_id = _object_id(identity)
        if _is_per_segment(owner):
            segment_object_id = _segment_object_id(owner, context, resolvers)
    row: dict[str, Any] = {"configurationId": module.configuration_id}
    if not module.is_new:
        row["moduleId"] = module.id_
    row["enterpriseObjectId"] = object_id
    row["ref"] = owner.name
    if segment_object_id is not None:
        row["segmentObjectId"] = segment_object_id
    return row, segment_object_id


def _warnings(
    baseline: VcoModel,
    owners: tuple[RefOwner, ...],
    read: dict[str, Any],
    level: ConfigContext | None,
    context: _Context,
) -> list[PlanWarning]:
    """What disagrees in the module as read: the rows and the owners of *baseline*."""
    found: list[PlanWarning] = []
    for owner in owners:
        rows = rows_named(read, owner.name)
        path = _warning_path(owner.name, owner.segment_name)
        if isinstance(owner.marker, OwnsRef):
            if not any(_row_matches(owner, row, context) for row in rows):
                found.append(
                    PlanWarning(
                        "owner_without_ref",
                        path,
                        f"The data holds {owner.value} at {owner.path}, but the module has "
                        + f"no {owner.name} row for it. The row is left missing: {_ONLY_THE_EDIT}.",
                    )
                )
        else:
            segment = context.segment(owner)
            of_segment = [
                row
                for row in rows
                if row_is_of_segment(row, segment, context.object_ids)
            ]
            if len(of_segment) > 1:
                found.append(
                    PlanWarning(
                        "several_refs",
                        path,
                        f"The module has {len(of_segment)} {owner.name} rows for one "
                        + f"segment, and {owner.path} shows the first. Every row is left "
                        + f"as it is: {_ONLY_THE_EDIT}, and a change removes only the row shown.",
                    )
                )
    for name in sorted(declared_ref_names(type(baseline), level)):
        for row in rows_named(read, name):
            owned = [o for o in owners if o.name == name]
            if any(_row_matches(o, row, context) for o in owned):
                continue
            if any(
                isinstance(o.marker, RefOnly)
                and row_is_of_segment(row, context.segment(o), context.object_ids)
                for o in owned
            ):
                continue  # one of several rows of a provider: ``several_refs``
            segment_name: str | None = None
            if row.get("segmentObjectId") is not None or row.get("segmentLogicalId"):
                segment_name = next(
                    (
                        s.name
                        for s in data_segments(baseline)
                        if row_is_of_segment(row, s, context.object_ids)
                    ),
                    None,
                )
            found.append(
                PlanWarning(
                    "ref_without_owner",
                    _warning_path(name, segment_name),
                    f"The module has a {name} row that nothing in the data names. "
                    + f"The row is left as it is: {_ONLY_THE_EDIT}.",
                )
            )
    return found


def _non_empty_list(value: object) -> bool:
    return is_list(value) and len(value) > 0


def sends_refs_always(
    module: ConfigurationModuleBase, payload: Mapping[str, Any]
) -> bool:
    """Whether a write of *module* with *payload* carries its refs whatever changed.

    True for a module that is not new, of a name in ``ALWAYS_SEND_REFS``,
    whose payload ``segments`` is a non-empty list.
    """
    return (
        not module.is_new
        and str(getattr(module, "name", "")) in ALWAYS_SEND_REFS
        and _non_empty_list(payload.get("segments"))
    )


def changed_owners(
    baseline: VcoModel, current: VcoModel, level: ConfigContext | None
) -> tuple[RefOwner, ...]:
    """The owners that *baseline* has and *current* has not, then those *current* has and *baseline* has not.

    Owners are told apart as ``derive_refs`` tells them (ref name, identity
    and segment), so an owner that moved to another place with the same
    identity is no change. It needs no resolver, no refs and no rows: for a
    module whose refs were not read, it says which edit would need them.
    """
    before = _by_key(ref_owners(baseline, level))
    now = _by_key(ref_owners(current, level))
    return (
        *(owner for key, owner in before.items() if key not in now),
        *(owner for key, owner in now.items() if key not in before),
    )


def derive_refs(
    module: ConfigurationModuleBase,
    baseline: VcoModel | None,
    current: VcoModel | None,
    payload: Mapping[str, Any],
    *,
    level: ConfigContext | None,
    resolvers: IdentityLookup | None,
    hub_services: HubServiceTable | None = None,
    segment_object_ids: Mapping[int, int] | None = None,
) -> DerivedRefs:
    """The rows the edit from *baseline* to *current* adds and removes, and the refs to send.

    *baseline* is the module's data as read, with its ``RefOnly`` fields
    filled from its refs, and *current* the data now; ``None`` for either (a
    plan of plain data) means no owners, so no change. *payload* is the data
    written. ``level`` is the config level the owners are read at.

    Each added owner with no row as read gets a new row; the resolver gives the
    ``enterpriseObjectId``, and for an owner of a hub's service (a QoS rule's
    backhaul hub) *hub_services* does. *segment_object_ids* (``segmentId`` to
    the segment's object ID) is where a module with no ``deviceSettings:segment``
    rows of its own, such as a QoS module, finds the ``segmentObjectId`` of a
    per-segment row, before the segment resolver.

    Raises ``ResolverNotLoadedError`` for a resolver that is not loaded
    (``resolvers`` ``None`` included), ``ResolutionError`` for an identity it
    does not know, naming the owner, and ``HubServiceNotFoundError`` for a hub
    that has no service in its segment of *hub_services* (``None`` has none).
    """
    raw_refs: object = module.raw_refs
    read: dict[str, Any] = {}
    if is_dict(raw_refs):
        read = {str(name): copy.deepcopy(held) for name, held in raw_refs.items()}
    before = () if baseline is None else ref_owners(baseline, level)
    now = () if current is None else ref_owners(current, level)
    comparing = baseline is not None and current is not None
    logical_ids: dict[int, uuid.UUID | None] = {}
    for model in (current, baseline):
        for segment in () if model is None else data_segments(model):
            if segment.segment_id is not None:
                logical_ids[segment.segment_id] = segment.logical_id
    context = _Context(_object_ids(raw_refs, segment_object_ids), logical_ids)

    before_by_key = _by_key(before) if comparing else {}
    now_by_key = _by_key(now) if comparing else {}
    changes: list[RefChange] = []
    gone: dict[str, list[dict[object, object]]] = {}
    for key, owner in before_by_key.items():
        if key in now_by_key:
            continue
        taken = gone.get(owner.name, [])
        row = next(
            (
                r
                for r in rows_named(read, owner.name)
                if _row_matches(owner, r, context) and not any(r is t for t in taken)
            ),
            None,
        )
        if row is None:
            continue
        gone[owner.name] = [*taken, row]
        changes.append(
            RefChange(
                added=False,
                name=owner.name,
                segment=owner.segment_name,
                row=cast(dict[str, Any], row),
                match=_match_field(owner),
                value=_json_value(owner.value),
                segment_object_id=(
                    cast(int | None, row.get("segmentObjectId"))
                    if _is_per_segment(owner)
                    else None
                ),
                owner=owner.path if isinstance(owner.marker, RefOnly) else None,
            )
        )
    new_rows: dict[str, list[dict[str, Any]]] = {}
    for key, owner in now_by_key.items():
        if key in before_by_key:
            continue
        if any(_row_matches(owner, r, context) for r in rows_named(read, owner.name)):
            continue
        row, segment_object_id = _new_row(
            module, owner, context, resolvers, hub_services
        )
        new_rows.setdefault(owner.name, []).append(row)
        changes.append(
            RefChange(
                added=True,
                name=owner.name,
                segment=owner.segment_name,
                row=row,
                match=_match_field(owner),
                value=_json_value(owner.value),
                segment_object_id=segment_object_id,
                owner=owner.path if isinstance(owner.marker, RefOnly) else None,
            )
        )

    warnings: list[PlanWarning] = []
    if baseline is not None and module.refs_loaded:
        warnings = _warnings(baseline, before, read, level, context)

    send = bool(changes) or sends_refs_always(module, payload)
    refs: dict[str, Any] | None = None
    if send:
        refs = {}
        for name in [*read, *(n for n in new_rows if n not in read)]:
            if name in APPEND_ONLY_REF_NAMES:
                continue
            if name not in gone and name not in new_rows:
                refs[name] = read[name]
                continue
            held = read.get(name)
            rows = held if is_list(held) else [] if held is None else [held]
            kept = [
                row
                for row in rows
                if not any(row is taken for taken in gone.get(name, []))
            ]
            kept.extend(new_rows.get(name, []))
            if kept:
                refs[name] = kept
    return DerivedRefs(refs, tuple(changes), tuple(warnings))
