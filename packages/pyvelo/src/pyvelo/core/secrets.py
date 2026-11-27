"""Show configuration with its secrets left out, and put them back.

A field marked ``Secret`` holds a password, a key or a community string.
``hide_secrets`` turns a value about to be shown (in a plan, an error or a
dump) into one that is safe to show: the mask ``HIDDEN`` stands where a secret
is. ``restore_secrets`` is the way back, for a document that was shown and is
given again: each mask is replaced by the value stored at the same place.
``declares_secret`` says whether a model has a secret at any depth.
"""

import copy
import functools
from dataclasses import dataclass, replace
from typing import Final, TypeIs

from pydantic import MISSING

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
from .rebase import KeyedList, Path, node_child, pair_items

HIDDEN: Final = "***"
"""The mask: what stands in place of a secret in everything pyvelo shows."""

type _Classes = tuple[type[VcoModel], ...]


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _under(classes: _Classes, key: object) -> tuple[bool, _Classes, bool] | None:
    """The field JSON key *key* names in *classes*.

    Whether it is secret, what it holds, and whether it is a map.

    None when no class has a field read from *key*. With several classes a
    field is secret if it is in any of them, and holds what it holds in each.
    """
    if not isinstance(key, str):
        return None
    known = secret = is_map = False
    below: list[type[VcoModel]] = []
    for cls in classes:
        name = python_name(cls, key)
        if name is None:
            continue
        known = True
        field_info = cls.model_fields[name]
        secret = secret or is_secret(field_info)
        is_map = is_map or list_kind(field_info) == "map"
        below.extend(m for m in field_models(field_info) if m not in below)
    return (secret, tuple(below), is_map) if known else None


def _hide(value: object, classes: _Classes, *, is_map: bool) -> object:
    if not classes:
        return value
    if is_list(value) or _is_tuple(value):
        return [_hide(item, classes, is_map=is_map) for item in value]
    if not is_dict(value):
        return value
    if is_map:  # the keys are map keys, not field names
        return {key: _hide(item, classes, is_map=False) for key, item in value.items()}
    hidden: dict[object, object] = {}
    for key, item in value.items():
        found = _under(classes, key)
        if found is None:
            hidden[key] = item
        elif found[0]:
            hidden[key] = HIDDEN
        else:
            hidden[key] = _hide(item, found[1], is_map=found[2])
    return hidden


def hide_secrets(model: type[VcoModel] | None, path: Path, value: object) -> object:
    """*value*, the JSON at *path* of a document of *model*, with the secrets hidden.

    The string ``"***"`` stands in place of the value of every field marked
    ``Secret``, at any depth, and is the result when *path* is at or below such
    a field. A tuple is walked as a list is. *value* is returned as it is when
    it is ``MISSING``, when *model* is None, and where the model does not know
    the place. The value given is never changed.
    """
    if model is None or value is MISSING:
        return value
    classes: _Classes = (model,)
    is_map = False
    for part in path.parts:
        if not isinstance(part, str):
            continue  # an item of a list: the same classes
        if is_map:
            is_map = False  # an entry of a map: the same classes
            continue
        found = _under(classes, part)
        if found is None:
            return value
        secret, classes, is_map = found
        if secret:
            return HIDDEN
    return _hide(value, classes, is_map=is_map)


@functools.cache
def declares_secret(model: type[VcoModel]) -> bool:
    """Whether *model*, or a class a field of it can hold at any depth, has a secret.

    The classes are those ``hide_secrets`` walks (``field_models`` of each
    field), each looked at once, so a class that holds itself ends the search.
    """
    seen: set[type[VcoModel]] = set()
    left = [model]
    while left:
        cls = left.pop()
        if cls in seen:
            continue
        seen.add(cls)
        for field_info in cls.model_fields.values():
            if is_secret(field_info):
                return True
            left.extend(field_models(field_info))
    return False


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
    is_map: bool
    """Whether an object there is a map, whose keys are data."""
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
    hidden = [_hide(item, place.classes, is_map=place.is_map) for item in stored]
    kept: list[object] = []
    for item in value:
        shown = _hide(item, place.classes, is_map=place.is_map)
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
        secret, below, is_map = found
        kept = _stored_field(stored, place.classes, key)
        path = place.path.child(_attribute(place.classes, key))
        if not secret:
            keyed = _keyed(place.classes, key)
            held = _Place(below, is_map=is_map, keyed=keyed, path=path)
            restored[key] = _restore(item, kept, held)
        elif isinstance(item, str) and item == HIDDEN:
            if isinstance(kept, _NoValue):
                raise NamedDocumentError(kept.refusal, path, "masked_secret")
            restored[key] = copy.deepcopy(kept)
        else:
            restored[key] = copy.deepcopy(item)
    return restored


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
                replace(place, keyed=None, path=place.path.child(position)),
            )
            for position, item in enumerate(value)
        ]
    if not is_dict(value):
        return value
    if place.is_map:  # the keys are map keys, not field names
        return {
            key: _restore(
                item,
                _entry(stored, key),
                _Place(
                    place.classes,
                    is_map=False,
                    keyed=None,
                    path=place.path.child(str(key)),
                ),
            )
            for key, item in value.items()
        }
    return _restore_fields(value, stored, place)


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
    top = _Place((model,), is_map=False, keyed=None, path=Path(()))
    return _restore(value, kept, top)
