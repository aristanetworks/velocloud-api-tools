"""The values that name something the configuration provides, and the check that each one does.

A field marked ``RefersTo`` holds a value that the items of a list marked
``Provides`` of the same name have (a segment ID that a device settings
segment has, a hub's logical ID that a segment's backhaul list has). A value
that no item has names nothing: the VCO would store it, and what it stands
for would not work. This module finds those values (``references``), the sets
they are looked up in (``provided``), and checks the first against the second
(``check_references``). It does no I/O and knows no module but by name: the
caller says which module the data is, and what the sets are.

A set is held under ``(name, None)``, or under ``(name, segment_id)`` when it
is a ``per_segment`` one: the set of the segment the marked list sits in.
"""

import functools
from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from pydantic import MISSING
from pydantic.fields import FieldInfo

from .base import VcoModel, field_models, is_dict, is_list
from .fields import get_provides, get_refers_to
from .rebase import Path
from .walk import walk_fields

type SetKey = tuple[str, int | None]
"""A set: its name, and the ID of the segment it belongs to (``None`` when the set is of the module)."""
type Sets = Mapping[SetKey, frozenset[object]]
"""The members of each set."""


@dataclass(frozen=True)
class Reference:
    """A value in a module's data that names a member of a set."""

    module: str
    """The module name on the wire: ``deviceSettings``, ``QOS``, ``firewall``."""
    path: Path
    """The field's path in python names."""
    value: object
    set_name: str
    """The name of the set the value is a member of."""
    segment_id: int | None
    """The ID of the segment the field sits in, for a ``per_segment`` reference; else ``None``."""


@dataclass(frozen=True)
class ReferenceFindings:
    """What ``check_references`` found."""

    dangling: tuple[Reference, ...]
    """References to a value that is not a member, and that the save introduces."""
    kept: tuple[Reference, ...]
    """References to a value that is not a member, held as the module was read."""


def _can_hold(model_cls: type[VcoModel], marked: Callable[[FieldInfo], bool]) -> bool:
    """Whether a model of *model_cls* has a field *marked* accepts, or a model below it does, at any depth.

    It follows the declared field types (through ``Annotated``, unions,
    ``list``, ``dict`` and ``tuple``): a subclass that adds a marker below a
    field declared with a class that has none is not found.
    """
    seen: set[type[VcoModel]] = set()
    pending = [model_cls]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for field_info in current.model_fields.values():
            if marked(field_info):
                return True
            pending.extend(field_models(field_info))
    return False


@functools.cache
def can_refer(model_cls: type[VcoModel]) -> bool:
    """Whether a model of *model_cls* can hold a ``RefersTo`` value, at any depth; computed once per class."""
    return _can_hold(model_cls, lambda info: get_refers_to(info) is not None)


@functools.cache
def can_provide(model_cls: type[VcoModel]) -> bool:
    """Whether a model of *model_cls* can hold a ``Provides`` list, at any depth; computed once per class.

    The walk for the sets (``provided``) goes by this, so it passes over the
    rules of a QoS module, which refer to hubs and provide nothing.
    """
    return _can_hold(model_cls, lambda info: get_provides(info) is not None)


def _segment_id(segment: VcoModel | None) -> int | None:
    """The ``segmentId`` of a segment entry, or ``None`` when it has none."""
    metadata: object = getattr(segment, "segment", None)
    segment_id: object = getattr(metadata, "segment_id", None)
    return segment_id if isinstance(segment_id, int) else None


def references(module: str, data: VcoModel) -> tuple[Reference, ...]:
    """Every ``RefersTo`` value in *data*, which is the data of the module named *module*.

    The walk goes through sub-models, lists of models (an item of a keyed list
    is named by its key, any other by its position) and maps of models. A
    value in the field's ``none``, ``None`` and ``MISSING`` refer to nothing
    and are left out.
    """
    found: list[Reference] = []
    for site in walk_fields(
        data,
        holds=can_refer,
        fields=lambda field_info: get_refers_to(field_info) is not None,
    ):
        refers = get_refers_to(site.field_info)
        if refers is None:  # pragma: no cover - the walk gives only these
            continue
        value: object = getattr(site.model, site.name)
        if value is None or value is MISSING or value in refers.none:
            continue
        segment_id = _segment_id(site.segment) if refers.per_segment else None
        found.append(Reference(module, site.path, value, refers.name, segment_id))
    return tuple(found)


def _key_value(item: object, path: str) -> object | None:
    """The value at the dotted *path* of *item*, or ``None`` when a step to it is absent, null or ``MISSING``."""
    value: object = item
    for name in path.split("."):
        if value is None or value is MISSING:
            return None
        value = getattr(value, name, None)
    return None if value is MISSING else value


def _items(value: object) -> Iterator[object]:
    """The items of a list, or the values of a map."""
    if is_list(value):
        yield from value
    elif is_dict(value):
        yield from value.values()


def provided(data: VcoModel) -> dict[SetKey, set[object]]:
    """The members of each set the data of one module provides.

    For each ``Provides`` field, the key values of its items (the values at
    the marker's ``key``), under ``(name, None)``, or under
    ``(name, segment_id)`` of the segment the field sits in for a
    ``per_segment`` one. A set with no member is not in the result (a lookup in
    it finds nothing either way). An item with no key value gives none; a
    ``per_segment`` field outside a segment with an ID gives no set.
    """
    found: dict[SetKey, set[object]] = {}
    for site in walk_fields(
        data,
        holds=can_provide,
        fields=lambda field_info: get_provides(field_info) is not None,
    ):
        provides = get_provides(site.field_info)
        if provides is None:  # pragma: no cover - the walk gives only these
            continue
        segment_id = _segment_id(site.segment) if provides.per_segment else None
        if provides.per_segment and segment_id is None:
            continue
        held: object = getattr(site.model, site.name)
        for item in _items(held):
            key = _key_value(item, provides.key)
            if key is not None:
                found.setdefault((provides.name, segment_id), set()).add(key)
    return found


def union(*sets: Mapping[SetKey, AbstractSet[object]]) -> Sets:
    """The sets of *sets* put together: the members of a set of one name are those of all of them."""
    joined: dict[SetKey, set[object]] = {}
    for one in sets:
        for key, members in one.items():
            joined.setdefault(key, set()).update(members)
    return {key: frozenset(members) for key, members in joined.items()}


def _is_member(reference: Reference, sets: Sets) -> bool:
    return reference.value in sets.get((reference.set_name, reference.segment_id), ())


def check_references(
    targets: Sequence[tuple[str, VcoModel, VcoModel | None]],
    sets_now: Sets,
    sets_as_read: Sets,
) -> ReferenceFindings:
    """Check the references of each target against the sets of the configuration as edited.

    A target is ``(module name, data as edited, data as read or None)``;
    *sets_now* are the sets of the configuration as edited and *sets_as_read*
    those of the configuration as read. A reference to a member is fine. One
    to a value that is not a member is ``kept`` when the data as read holds
    the same value at the same path and it was not a member of the sets as
    read either: the save does not make it worse, and is not asked to mend it.
    Any other is ``dangling``: a new value, a value that was a member and no
    longer is, or the same value at a path where it was not.
    """
    dangling: list[Reference] = []
    kept: list[Reference] = []
    for module, edited, read in targets:
        # The references as read are looked up only for a value that names nothing.
        held: dict[Path, Reference] | None = None
        for reference in references(module, edited):
            if _is_member(reference, sets_now):
                continue
            if held is None:
                held = (
                    {r.path: r for r in references(module, read)}
                    if read is not None
                    else {}
                )
            before = held.get(reference.path)
            if (
                before is not None
                and before.value == reference.value
                and not _is_member(before, sets_as_read)
            ):
                kept.append(reference)
            else:
                dangling.append(reference)
    return ReferenceFindings(tuple(dangling), tuple(kept))


def reference_text(reference: Reference) -> str:
    """A reference as one line: ``<module> <path>: <value> is not a <set> of segment <ID>``.

    The tail ``of segment <ID>`` is left out for a set of the module.
    """
    text = (
        f"{reference.module} {reference.path}: {reference.value} "
        f"is not a {reference.set_name}"
    )
    if reference.segment_id is not None:
        text += f" of segment {reference.segment_id}"
    return text
