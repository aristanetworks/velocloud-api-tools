"""The data fields that own ref rows: finding them, and filling the ``RefOnly`` ones from the refs read.

A ref row goes with an identity the data holds (``OwnsRef``) or stands for a
value only the row says (``RefOnly``). This module walks a module's
data for those fields. It does not know modules: ``pyvelo.client.schemas``
imports it to fill a module's ``RefOnly`` fields as the module is validated,
and the derivation of the rows to send imports it to compare owners.

The walk is ``pyvelo.core.walk.walk_fields``: it takes the fields of models,
and the items of lists and maps of models, as the references check does. A
model whose class cannot hold an owner (``can_hold_owner``) is not walked, so
the rules of a firewall module, which hold none, cost nothing to look through,
and a QoS module is walked down to the backhaul hub entries of its rules.
"""

import functools
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Final, Literal, TypeAliasType, get_args, get_origin

from pydantic import MISSING
from pydantic.fields import FieldInfo

from pyvelo.core import ConfigContext, OwnsRef, RefOnly, VcoModel
from pyvelo.core.base import is_dict, is_list
from pyvelo.core.fields import get_owns_ref, get_ref_only, get_resolvable
from pyvelo.core.rebase import Path
from pyvelo.core.walk import walk_fields

SEGMENT_REF: Final = "deviceSettings:segment"
"""The ref name of a profile device settings segment's row."""


@dataclass(frozen=True)
class RefOwner:
    """A field that holds an identity a ref row goes with."""

    name: str
    """The ref name: the stub's ``ref``, else the marker's default, else the ``OwnsRef`` name."""
    marker: OwnsRef | RefOnly
    identity_type: type
    value: object
    """The identity: a ``uuid.UUID`` (text that parses as one is made one) or an ``int``."""
    segment_id: int | None
    """The ``segmentId`` of the segment the owner sits in; for a ``RefOnly`` owner and an ``OwnsRef`` with ``per_segment``, else ``None``."""
    segment_name: str | None
    """That segment's name, under the same rule."""
    path: Path
    """The field's path in python names: ``segments["Global Segment"].dns.primary_provider.service``."""
    holder: VcoModel | None = field(default=None, compare=False, repr=False)
    """The model that holds the field, for a message that names more than the identity (the hub of a backhaul entry)."""


@dataclass(frozen=True)
class SegmentRef:
    """A segment entry's descriptor: the three things a ref row is matched to it by."""

    segment_id: int | None
    name: str | None
    logical_id: uuid.UUID | None


@dataclass(frozen=True)
class _Site:
    """A field marked ``OwnsRef`` or ``RefOnly``, where the walk found it."""

    model: VcoModel
    field: str
    field_info: FieldInfo
    path: Path
    segment: SegmentRef | None


def _as_uuid(value: object) -> uuid.UUID | None:
    """*value* as a ``UUID`` when it is one or is text that parses as one."""
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _segment_of(item: object) -> SegmentRef | None:
    """The segment an item of ``segments`` stands for, or ``None`` when it has none."""
    metadata: object = getattr(item, "segment", None)
    if metadata is None or metadata is MISSING:
        return None
    segment_id: object = getattr(metadata, "segment_id", None)
    name: object = getattr(metadata, "name", None)
    return SegmentRef(
        segment_id if isinstance(segment_id, int) else None,
        name if isinstance(name, str) else None,
        _as_uuid(getattr(metadata, "segment_logical_id", None)),
    )


def _model_classes(annotation: object) -> Iterator[type[VcoModel]]:
    """The model classes a field type can hold, through ``Annotated``, unions, ``list``, ``dict`` and ``tuple``."""
    if isinstance(annotation, TypeAliasType):
        yield from _model_classes(annotation.__value__)
    elif isinstance(annotation, type):
        if issubclass(annotation, VcoModel):
            yield annotation
    elif get_origin(annotation) is not Literal:
        args = get_args(annotation)  # ``Annotated`` has its type first, then markers
        for arg in args[:1] if get_origin(annotation) is Annotated else args:
            yield from _model_classes(arg)


@functools.cache
def can_hold_owner(model_cls: type[VcoModel]) -> bool:
    """Whether a model of *model_cls* can hold a declared owner, at any depth.

    True when one of its fields carries ``OwnsRef`` or ``RefOnly``, or the type
    of a field (through ``Annotated``, unions, ``list``, ``dict`` and
    ``tuple``) is a model class that can. It is computed from the classes, once
    per class, from the declared field types: a subclass that adds an owner
    below a field declared with a class that has none is not found.
    """
    seen: set[type[VcoModel]] = set()
    pending = [model_cls]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for field_info in current.model_fields.values():
            if get_owns_ref(field_info) is not None or get_ref_only(field_info):
                return True
            pending.extend(_model_classes(field_info.annotation))
    return False


@functools.cache
def declared_ref_names(
    model_cls: type[VcoModel], level: ConfigContext | None
) -> frozenset[str]:
    """The ref names that a model of *model_cls* declares at *level*, at any depth.

    The name of each ``OwnsRef`` whose ``level`` is not set or is *level*, and
    the default name of each ``RefOnly``. A row of one of these names that no
    owner matches is a row that disagrees with the data. It is computed from
    the declared field types, as ``can_hold_owner`` is.
    """
    names: set[str] = set()
    seen: set[type[VcoModel]] = set()
    pending = [model_cls]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for field_info in current.model_fields.values():
            owns = get_owns_ref(field_info)
            ref_only = get_ref_only(field_info)
            if owns is not None and (owns.level is None or owns.level == level):
                names.add(owns.name)
            if ref_only is not None and ref_only.default is not None:
                names.add(ref_only.default)
            pending.extend(_model_classes(field_info.annotation))
    return frozenset(names)


def _sites(data: VcoModel) -> Iterator[_Site]:
    for site in walk_fields(data, holds=can_hold_owner):
        if get_owns_ref(site.field_info) is not None or get_ref_only(site.field_info):
            yield _Site(
                site.model,
                site.name,
                site.field_info,
                site.path,
                None if site.segment is None else _segment_of(site.segment),
            )


def data_segments(data: VcoModel) -> tuple[SegmentRef, ...]:
    """The descriptor of each item of the root's ``segments`` that has one."""
    items: object = getattr(data, "segments", None)
    if not is_list(items):
        return ()
    found = (_segment_of(item) for item in items)
    return tuple(segment for segment in found if segment is not None)


def _ref_name(site: _Site, fallback: str | None) -> str | None:
    """The ref name of *site*: the ``ref`` its model holds, else *fallback*."""
    if "ref" in type(site.model).model_fields:
        stub: object = getattr(site.model, "ref", None)
        if isinstance(stub, str) and stub:
            return stub
    return fallback


def _identity(value: object, path: str | None) -> object | None:
    """The identity *value* holds (at the dotted *path* below it), or ``None`` when it holds none."""
    if path is not None:
        for step in path.split("."):
            if value is None or value is MISSING:
                return None
            value = getattr(value, step, None)
    if value is None or value is MISSING or value == "":
        return None
    parsed = _as_uuid(value)
    return value if parsed is None else parsed


def ref_owners(data: VcoModel, level: ConfigContext | None) -> tuple[RefOwner, ...]:
    """Every declared owner in *data* that holds an identity, as it sits at *level*.

    An ``OwnsRef`` whose ``level`` is not *level* is skipped, also when *level*
    is ``None``. ``None``, ``MISSING`` and ``""`` hold no identity.
    """
    owners: list[RefOwner] = []
    for site in _sites(data):
        value: object = getattr(site.model, site.field)
        owns = get_owns_ref(site.field_info)
        ref_only = get_ref_only(site.field_info)
        marker: OwnsRef | RefOnly
        if owns is not None:
            if owns.level is not None and owns.level != level:
                continue
            marker = owns
            identity = _identity(value, owns.value)
            identity_type = owns.identity
            name = _ref_name(site, None) or owns.name
            per_segment = owns.per_segment
        elif ref_only is not None:
            marker = ref_only
            identity = _identity(value, None)
            resolvable = get_resolvable(site.field_info)
            if resolvable is None:
                raise TypeError(
                    f"the RefOnly field {site.path} has no Resolvable; "
                    + "declare the identity type that gives the object_id of a new row"
                )
            identity_type = resolvable.identity_type
            name = _ref_name(site, ref_only.default)
            per_segment = True
        else:  # pragma: no cover - a site holds one of the two markers
            continue
        if identity is None or name is None:
            continue
        segment = site.segment if per_segment else None
        owners.append(
            RefOwner(
                name=name,
                marker=marker,
                identity_type=identity_type,
                value=identity,
                segment_id=segment.segment_id if segment is not None else None,
                segment_name=segment.name if segment is not None else None,
                path=site.path,
                holder=site.model,
            )
        )
    return tuple(owners)


def ref_only_values(data: VcoModel) -> dict[Path, object]:
    """The value of every ``RefOnly`` field the walk reaches, by its path in python names.

    ``MISSING`` (the refs were not read) and ``None`` (no row) are values here.
    A value given as UUID text is the ``UUID``.
    """
    values: dict[Path, object] = {}
    for site in _sites(data):
        if get_ref_only(site.field_info) is None:
            continue
        value: object = getattr(site.model, site.field)
        parsed = _as_uuid(value)
        values[site.path] = value if parsed is None else parsed
    return values


def rows_named(raw_refs: object, name: str) -> list[dict[object, object]]:
    """The rows of ref *name* in *raw_refs*: a bare object or a list, each as a ``dict``."""
    if not is_dict(raw_refs):
        return []
    held = raw_refs.get(name)
    if is_list(held):
        return [row for row in held if is_dict(row)]
    return [held] if is_dict(held) else []


def segment_object_ids(raw_refs: object) -> dict[int, int]:
    """``data.segmentId`` to ``enterpriseObjectId`` of the ``deviceSettings:segment`` rows."""
    found: dict[int, int] = {}
    for row in rows_named(raw_refs, SEGMENT_REF):
        data = row.get("data")
        segment_id = data.get("segmentId") if is_dict(data) else None
        object_id = row.get("enterpriseObjectId")
        if isinstance(segment_id, int) and isinstance(object_id, int):
            found[segment_id] = object_id
    return found


def row_is_of_segment(
    row: dict[object, object], segment: SegmentRef | None, object_ids: dict[int, int]
) -> bool:
    """Whether *row* is a row of *segment*; ``None`` is a row with no segment."""
    segment_object = row.get("segmentObjectId")
    if segment is None:
        return segment_object is None
    for segment_id, object_id in object_ids.items():
        if object_id == segment_object:
            return segment_id == segment.segment_id
    logical_id = _as_uuid(row.get("segmentLogicalId"))
    return logical_id is not None and logical_id == segment.logical_id


def fill_ref_only(data: VcoModel, raw_refs: object) -> None:
    """Set each ``RefOnly`` field of *data* from the row of its name that is of its segment.

    The row's segment is found by its ``segmentObjectId`` among the module's
    own ``deviceSettings:segment`` rows, and failing that by its
    ``segmentLogicalId`` against the segment entry's. A field outside any
    segment takes a row with no ``segmentObjectId``. The value is the row's
    ``logicalId`` as a ``UUID``: ``None`` when no row matches, the first in
    read order when several do.

    The field is set without the hook of assignment, so the stub's ``ref`` is
    never written (an effective-style stub such as ``"backupProvider": {}``
    stays as read), and without ``model_fields_set``: a fill is not an edit.
    """
    object_ids = segment_object_ids(raw_refs)
    for site in _sites(data):
        ref_only = get_ref_only(site.field_info)
        if ref_only is None:
            continue
        name = _ref_name(site, ref_only.default)
        found: uuid.UUID | None = None
        for row in () if name is None else rows_named(raw_refs, name):
            logical_id = _as_uuid(row.get("logicalId"))
            if logical_id is not None and row_is_of_segment(
                row, site.segment, object_ids
            ):
                found = logical_id
                break
        object.__setattr__(site.model, site.field, found)
