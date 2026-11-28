"""Show configuration with its secrets left out, and put them back.

A field marked ``Secret`` holds a password, a key or a community string.
``hide_secrets`` turns a value about to be shown (in a plan, an error or a
dump) into one that is safe to show: the mask ``HIDDEN`` stands where a secret
is. ``restore_secrets`` is the way back, for a document that was shown and is
given again: each mask is replaced by the value stored at the same place.
``declares_secret`` says whether a model has a secret at any depth.

A value of the shape its field declares is read as declared: the keys of a
model are fields, a key no class declares is data with all below it, and the
keys of a map are data. A value that cannot be what its field declares is
read both as a model of the classes there and as the field its key names, and
a secret under either reading is hidden. There are three such values:

- an item of a list found where a map is declared (an entry of the map, or a
  map itself);
- an entry of a map that is neither an object nor ``null`` (the map is then
  one model, and the entry a field of it);
- an object found where a list of models is all the field declares (one item
  of the list, or a map of its items).

A mask in one of them is refused, since nothing stored is at its place. An
object under a key of a map is always an entry of the map, which is what the
field declares, and a key spelled as a field is no exception. So where a map
is in fact one model, a secret below an object of it is hidden only where the
entry's class marks that key too.
"""

import copy
import functools
from dataclasses import dataclass, replace
from enum import Enum, auto
from types import UnionType
from typing import (
    Annotated,
    Final,
    TypeIs,
    Union,  # pyright: ignore[reportDeprecated]  # the origin of ``T | MISSING``, below
    get_args,
    get_origin,
)

from pydantic import MISSING
from pydantic.fields import FieldInfo

from .base import (
    VcoModel,
    field_models,
    is_dict,
    is_list,
    list_kind,
    python_name,
    same_json,
)
from .documents import NamedDocumentError
from .fields import is_secret
from .layers import field_kind
from .rebase import KeyedList, Path, PathPart, node_child, pair_items
from .walk import can_hold_field

HIDDEN: Final = "***"
"""The mask: what stands in place of a secret in everything pyvelo shows."""

type _Classes = tuple[type[VcoModel], ...]


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


class _Holds(Enum):
    """How the classes at a place of a walk are held there."""

    MODEL = auto()
    """One model. A list found there is a list of them."""
    ITEMS = auto()
    """The items of a list, where a list of models is all the field declares."""
    ENTRIES = auto()
    """The values of a map, whose keys are data."""
    EITHER = auto()
    """One model, or the values of a map.

    An item of a list found where a map is declared: nothing says which.
    """


_ITEM: Final = {
    _Holds.MODEL: _Holds.MODEL,
    _Holds.ITEMS: _Holds.MODEL,
    _Holds.ENTRIES: _Holds.EITHER,
    _Holds.EITHER: _Holds.EITHER,
}
"""How the classes are held by an item of a list, by how the list holds them."""

type _Reading = tuple[_Classes, _Holds]
"""One way to read a value: the classes it is of, and how it holds them."""

_UNION_ORIGINS: Final = (Union, UnionType)  # pyright: ignore[reportDeprecated]  # still an origin
"""The origins of a union: two before Python 3.14, and ``T | MISSING`` has the first."""


def _allows_one_model(annotation: object) -> bool:
    """Whether *annotation* allows a model as it is, in no list and no map."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in _UNION_ORIGINS:
        return any(_allows_one_model(member) for member in get_args(annotation))
    return isinstance(annotation, type) and issubclass(annotation, VcoModel)


@functools.cache
def _only_a_list(field_info: FieldInfo) -> bool:
    """Whether a list is all *field_info* declares: an object there is never valid."""
    if list_kind(field_info) in (None, "map"):
        return False
    return not _allows_one_model(field_kind(field_info).inner_type)


def _under(classes: _Classes, key: object) -> tuple[bool, _Classes, _Holds] | None:
    """The field JSON key *key* names in *classes*.

    Whether it is secret, the classes it holds, and how it holds them.

    None when no class has a field read from *key*. With several classes a
    field is secret if it is in any of them, and holds what it holds in each:
    a map if it is one in any, and only a list if it is that in every one.
    """
    if not isinstance(key, str):
        return None
    known = secret = is_map = False
    only_lists = True
    below: list[type[VcoModel]] = []
    for cls in classes:
        name = python_name(cls, key)
        if name is None:
            continue
        known = True
        field_info = cls.model_fields[name]
        secret = secret or is_secret(field_info)
        is_map = is_map or list_kind(field_info) == "map"
        only_lists = only_lists and _only_a_list(field_info)
        below.extend(m for m in field_models(field_info) if m not in below)
    if not known:
        return None
    if is_map:
        return secret, tuple(below), _Holds.ENTRIES
    return secret, tuple(below), _Holds.ITEMS if only_lists else _Holds.MODEL


def _is_entry(item: object) -> bool:
    """Whether *item* is what an entry of a map of models is: an object, or null."""
    return is_dict(item) or item is None


def _hide(value: object, classes: _Classes, holds: _Holds) -> object:
    """*value*, found where *classes* are held as *holds* says, with no secret.

    An object is one model only at ``_Holds.MODEL``: its keys are fields, and
    a key no class declares is kept with all below it.
    """
    if not classes:
        return value
    if is_list(value) or _is_tuple(value):
        return [_hide(item, classes, _ITEM[holds]) for item in value]
    if not is_dict(value):
        return value
    if holds is not _Holds.MODEL:
        return {
            key: _hide_under(key, item, classes, holds) for key, item in value.items()
        }
    hidden: dict[object, object] = {}
    for key, item in value.items():
        found = _under(classes, key)
        if found is None:
            hidden[key] = item
        elif found[0]:
            hidden[key] = HIDDEN
        else:
            hidden[key] = _hide(item, found[1], found[2])
    return hidden


def _hide_under(key: object, item: object, classes: _Classes, holds: _Holds) -> object:
    """*item*, under *key* of an object that is not one model, with no secret.

    The entry of a map, as it is declared, is a model of *classes* under a
    key that is data. Anything else is not what its field declares, and is
    read both ways: as such a model, and as the field *key* names. So it is
    for an entry that is neither an object nor null (the map is one model),
    for an object found where only a list is declared (one item of it, or a
    map of its items) and for an item of a list found where a map is declared
    (an entry of the map, or a map).
    """
    hidden = _hide(item, classes, _Holds.MODEL)
    if holds is _Holds.ENTRIES and _is_entry(item):
        return hidden
    found = _under(classes, key)
    if found is None:
        return hidden
    return HIDDEN if found[0] else _hide(hidden, found[1], found[2])


def _below(
    reading: _Reading, part: PathPart, *, is_entry: bool
) -> list[_Reading] | None:
    """The readings of what is under *part* of a value read as *reading*.

    None where it is, or is below, a secret; no reading where no class knows
    the place. *is_entry* is whether what is under *part* is an object or
    null. The readings are those of ``_hide``.
    """
    classes, holds = reading
    if not isinstance(part, str):
        return [(classes, _ITEM[holds])]  # an item of a list: the same classes
    readings: list[_Reading] = (
        [] if holds is _Holds.MODEL else [(classes, _Holds.MODEL)]
    )
    if holds is _Holds.ENTRIES and is_entry:
        return readings  # an entry of a map: the same classes
    found = _under(classes, part)
    if found is None:
        return readings
    return None if found[0] else [*readings, (found[1], found[2])]


def hide_secrets(model: type[VcoModel] | None, path: Path, value: object) -> object:
    """*value*, the JSON at *path* of a document of *model*, with the secrets hidden.

    The string ``"***"`` stands in place of the value of every field marked
    ``Secret``, at any depth, and is the result when *path* is at or below such
    a field. A tuple is walked as a list is. *value* is returned as it is when
    it is ``MISSING``, when *model* is None, and where the model does not know
    the place. The value given is never changed.

    A value of another shape than its field declares (a list where a map is
    declared, an object where only a list is, an entry of a map that is no
    object) is read both as a model of the classes there and as the field its
    key names, and a secret under either reading is hidden. The module's text
    says which values those are, and what is still read as declared.
    """
    if model is None or value is MISSING:
        return value
    readings: list[_Reading] = [((model,), _Holds.MODEL)]
    parts = path.parts
    for position, part in enumerate(parts):
        rest = parts[position + 1 :]
        is_entry = isinstance(rest[0], str) if rest else _is_entry(value)
        found: list[_Reading] = []
        for reading in readings:
            below = _below(reading, part, is_entry=is_entry)
            if below is None:
                return HIDDEN
            found += below
        if not found:
            return value
        readings = found
    for classes, holds in readings:
        value = _hide(value, classes, holds)
    return value


@functools.cache
def declares_secret(model: type[VcoModel]) -> bool:
    """Whether *model*, or a class a field of it can hold at any depth, has a secret.

    The classes are those ``hide_secrets`` walks (``field_models`` of each
    field, which ``models_below`` follows), each looked at once. The answer
    is kept per class.
    """
    return can_hold_field(model, is_secret)


MASK: Final = (
    'holds the mask "***", and there is no stored value to keep in its place '
    "({why}). The mask would be written to the VCO as the secret. Give the model "
    "this document was dumped from as `stored`, or write the secret in full."
)
"""What refuses a mask with no stored value at its place, for one of four reasons."""

NO_STORED: Final = "no `stored` was given"
NO_VALUE: Final = "`stored` has no value there"
NO_ITEM: Final = "`stored` has no such item: the item is new"
SEVERAL_KEYS: Final = "`stored` holds the field under {count} keys"

MASK_OF_ITEM: Final = (
    'holds the mask "***", and the stored item whose secret it keeps cannot be '
    "told ({why}). No key tells the items of this list apart, so an item is the "
    "one stored item it equals once the secrets of both are hidden; the secret of "
    "any other item would be written to the VCO as the secret of this one. Either "
    "give the secret in full, or leave the item as it was dumped; an item that "
    "matches several stored items needs every secret of it in full."
)
"""What refuses a mask in an item matched by its content, for one of two reasons."""

NO_MATCH: Final = "the item matches no stored item: it is new, or it was edited"
SEVERAL_ITEMS: Final = "the item matches {count} stored items, and their secrets differ"

NOT_JSON: Final = (
    "restore_secrets reads JSON as json.loads gives it (dict, list, str, int, "
    "float, bool and None), and {what} holds a {type}. A mask inside any other "
    "container would not be found, and would be written to the VCO as the secret. "
    "Give plain JSON: json.loads(json.dumps(...)) makes it."
)
"""What refuses a document, or a *stored*, that holds anything else."""


@dataclass(frozen=True, slots=True)
class _NoValue:
    """That the stored document has no value at a place of the given one."""

    refusal: str
    """What refuses a mask there, and all below it."""


_NO_STORED: Final = _NoValue(MASK.format(why=NO_STORED))
_NO_VALUE: Final = _NoValue(MASK.format(why=NO_VALUE))
_NO_ITEM: Final = _NoValue(MASK.format(why=NO_ITEM))
_NO_MATCH: Final = _NoValue(MASK_OF_ITEM.format(why=NO_MATCH))


@dataclass(frozen=True, slots=True)
class _Place:
    """Where the walk of a document stands."""

    classes: _Classes
    """The classes a model there can be, as ``_hide`` has them."""
    holds: _Holds
    """How the place holds them."""
    keyed: KeyedList | None
    """What pairs the items of a list there; None for a list without a key."""
    path: Path
    """The place, in attribute names."""


def _refuse_what_is_not_json(value: object, what: str) -> None:
    """Raise ``TypeError`` unless *value* is JSON as ``json.loads`` gives it.

    *what* names the value in the error. No value is quoted.
    """
    left = [value]
    while left:
        item = left.pop()
        if is_dict(item):
            left.extend(item.values())
        elif is_list(item):
            left.extend(item)
        elif item is not None and not isinstance(item, (str, int, float)):
            raise TypeError(NOT_JSON.format(what=what, type=type(item).__name__))


def _attribute(classes: _Classes, key: str) -> str:
    """The attribute name of the field *key* names, in the first class that has it.

    *key* names a field in one of *classes* (``_under`` found it).
    """
    return next(name for cls in classes if (name := python_name(cls, key)) is not None)


def _keyed(classes: _Classes, key: str) -> KeyedList | None:
    """The keyed list the field *key* names is, in every class that has it.

    None where it is none, and where two classes do not agree. *key* names a
    field in one of *classes* (``_under`` found it).
    """
    first, *others = [
        node_child(cls, key) for cls in classes if python_name(cls, key) is not None
    ]
    if isinstance(first, KeyedList) and all(node == first for node in others):
        return first
    return None


def _entry(stored: object, key: object) -> object:
    """What *stored* holds under *key*; a ``_NoValue`` where it holds nothing."""
    if isinstance(stored, _NoValue):
        return stored
    if is_dict(stored) and key in stored:
        return stored[key]
    return _NO_VALUE


def _stored_field(stored: object, classes: _Classes, key: str) -> object:
    """What *stored* holds for the field *key* names in *classes*.

    Under *key* where it has that key; else under the one key of it that a
    class reads the same field from. A ``_NoValue`` where it has none, and
    where it has several: nothing says which of them the document's is.
    """
    if not is_dict(stored) or key in stored:
        return _entry(stored, key)
    others = [
        other
        for other in stored
        if isinstance(other, str)
        and any(
            (name := python_name(cls, key)) is not None
            and python_name(cls, other) == name
            for cls in classes
        )
    ]
    if len(others) > 1:
        return _NoValue(MASK.format(why=SEVERAL_KEYS.format(count=len(others))))
    return stored[others[0]] if others else _NO_VALUE


def _matched_by_content(
    value: list[object], stored: list[object], place: _Place
) -> list[object]:
    """For each item of *value*, the stored item it equals with the secrets hidden.

    The one item of *stored* that is the same JSON as it once ``_hide`` has
    hidden the secrets of both, at every depth. Several such items that are
    the same JSON as one another are one. A ``_NoValue`` where there is none,
    and where there are several with different secrets. Position plays no part.
    """
    holds = _ITEM[place.holds]
    hidden = [_hide(item, place.classes, holds) for item in stored]
    kept: list[object] = []
    for item in value:
        shown = _hide(item, place.classes, holds)
        same = [
            old
            for old, old_shown in zip(stored, hidden, strict=True)
            if same_json(old_shown, shown)
        ]
        if not same:
            kept.append(_NO_MATCH)
        elif all(same_json(same[0], other) for other in same[1:]):
            kept.append(same[0])
        else:
            why = SEVERAL_ITEMS.format(count=len(same))
            kept.append(_NoValue(MASK_OF_ITEM.format(why=why)))
    return kept


def _stored_items(value: list[object], stored: object, place: _Place) -> list[object]:
    """For each item of the list *value*, the stored item at the same place.

    In a keyed list, the item ``pair_items`` pairs it with, as a save does. In
    any other list, and in a keyed list whose items ``pair_items`` cannot
    pair (the stored keys do not tell them apart), the item it matches by
    content (``_matched_by_content``). A ``_NoValue`` for an item with none.
    """
    if isinstance(stored, _NoValue):
        return [stored] * len(value)
    if not is_list(stored):
        return [_NO_VALUE] * len(value)
    pairing = None if place.keyed is None else pair_items(stored, value, place.keyed)
    if pairing is None:
        return _matched_by_content(value, stored, place)
    kept: list[object] = [_NO_ITEM] * len(value)
    for old, new in pairing.pairs:
        kept[new] = stored[old]
    return kept


def _restore_fields(
    value: dict[object, object], stored: object, place: _Place
) -> dict[object, object]:
    """A copy of *value*, a model at *place*, with each mask replaced from *stored*."""
    restored: dict[object, object] = {}
    for key, item in value.items():
        found = _under(place.classes, key)
        if found is None or not isinstance(key, str):
            restored[key] = copy.deepcopy(item)
            continue
        secret, below, holds = found
        kept = _stored_field(stored, place.classes, key)
        path = place.path.child(_attribute(place.classes, key))
        if not secret:
            keyed = _keyed(place.classes, key)
            held = _Place(below, holds=holds, keyed=keyed, path=path)
            restored[key] = _restore(item, kept, held)
        elif isinstance(item, str) and item == HIDDEN:
            if isinstance(kept, _NoValue):
                raise NamedDocumentError(kept.refusal, path, "masked_secret")
            restored[key] = copy.deepcopy(kept)
        else:
            restored[key] = copy.deepcopy(item)
    return restored


def _restore_under(key: object, item: object, stored: object, place: _Place) -> object:
    """A copy of *item*, under *key* of an object at *place* that is not one model.

    The entry of a map, as it is declared, takes each mask from the entry
    *stored* has under the same key. Anything else is read as ``_hide_under``
    reads it, both ways, and nothing stored is at its place: a mask where
    either reading has a secret is refused, and the rest is copied.
    """
    entry = _Place(
        place.classes,
        holds=_Holds.MODEL,
        keyed=None,
        path=place.path.child(str(key)),
    )
    if place.holds is _Holds.ENTRIES and _is_entry(item):
        return _restore(item, _entry(stored, key), entry)
    nothing = stored if isinstance(stored, _NoValue) else _NO_VALUE
    found = _under(place.classes, key)
    if found is None or not isinstance(key, str):
        return _restore(item, nothing, entry)
    secret, below, holds = found
    path = place.path.child(_attribute(place.classes, key))
    if not secret:
        held = _Place(below, holds=holds, keyed=None, path=path)
        return _restore(_restore(item, nothing, entry), nothing, held)
    if isinstance(item, str) and item == HIDDEN:
        raise NamedDocumentError(nothing.refusal, path, "masked_secret")
    return copy.deepcopy(item)


def _restore(value: object, stored: object, place: _Place) -> object:
    """A copy of *value*, found at *place*, with each mask replaced from *stored*.

    *stored* is what the stored document holds at the same place, or a
    ``_NoValue``. The walk is that of ``_hide``: the same places are secret.
    """
    if not any(declares_secret(cls) for cls in place.classes):
        return copy.deepcopy(value)  # no secret at any depth: all of it is data
    if is_list(value):
        kept = _stored_items(value, stored, place)
        return [
            _restore(
                item,
                kept[position],
                replace(
                    place,
                    holds=_ITEM[place.holds],
                    keyed=None,
                    path=place.path.child(position),
                ),
            )
            for position, item in enumerate(value)
        ]
    if not is_dict(value):
        return value
    if place.holds is _Holds.MODEL:
        return _restore_fields(value, stored, place)
    return {
        key: _restore_under(key, item, stored, place) for key, item in value.items()
    }


def restore_secrets(
    model: type[VcoModel], value: object, stored: object = MISSING
) -> object:
    """*value*, a document of *model* in the VCO's keys, with each mask replaced.

    A mask is ``HIDDEN`` as the whole value of a field marked ``Secret``. It is
    replaced by what *stored*, the wire dump of the model the document was
    dumped from, holds at the same place. The same text anywhere else (in a
    field that is no secret, under a key no class declares, as an item of a
    secret that is a list) is data, and a secret given in full is taken as
    given. The secret places are those ``hide_secrets`` masks.

    The same place is:

    - of a field, the field, read from *stored* under the same key, else under
      the one key of it that the class reads the field from;
    - of an entry of a map, the entry with the same key;
    - of an item of a keyed list, the item a save pairs it with
      (``pair_items``: by alt key, then by key), so an item that is moved, or
      renamed where the list has an alt key, keeps its own secrets;
    - of an item of any other list, the one stored item of that list it equals
      when the secrets of both are hidden, at every depth. Stored items that
      are the same, secrets included, are one. Position plays no part: a
      reorder, an addition or a removal never moves a secret to another item,
      and an edited item of such a list restates its secrets. So it is in a
      keyed list whose stored keys do not tell its items apart.

    Below an item the same rules hold. An item with no mask in it needs no
    stored item.

    Nothing stored is at the same place as a value of another shape than its
    field declares (the three kinds of the module's text). A mask there, where
    ``hide_secrets`` has a secret under either reading, is refused, and the
    rest of the value is copied.

    A mask with no stored value at its place raises ``NamedDocumentError`` of
    kind ``masked_secret``, at the path of the secret in attribute names (an
    item by its position in *value*): it would be written to the VCO as the
    secret.

    *value* and *stored* are JSON as ``json.loads`` gives it: an object is a
    ``dict`` and an array a ``list``. A ``tuple``, a mapping that is not a
    ``dict`` or any other value ``json.loads`` does not give, anywhere in
    either, raises ``TypeError``, which quotes no value. The result is a copy:
    it shares no ``dict`` or ``list`` with *value* or with *stored*, and
    neither is changed.
    """
    _refuse_what_is_not_json(value, "the document")
    kept: object = _NO_STORED
    if stored is not MISSING:
        _refuse_what_is_not_json(stored, "`stored`")
        kept = stored
    top = _Place((model,), holds=_Holds.MODEL, keyed=None, path=Path(()))
    return _restore(value, kept, top)
