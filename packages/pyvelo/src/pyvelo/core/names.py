"""A path and a value as pyvelo shows them.

What pyvelo computes with names a field by the key the VCO uses and an item
by its key or its position. What it prints names a field by its python
attribute and an item by the text a reader knows it by, in the one form
``Path`` writes: ``segments[Guest].rules[Web].rule_logical_id``.

``named_path`` converts a path, ``named_change_path`` the path of a change
between the data as read and as edited, ``named_error`` the path of a
``PatchError``, ``named_value`` the keys of a JSON value found at one, and
``item_part`` gives the part that names one item of a list.
"""

import functools
from collections import Counter
from dataclasses import dataclass
from types import UnionType
from typing import (
    Annotated,
    Union,  # pyright: ignore[reportDeprecated]  # the origin of ``T | MISSING``, below
    get_args,
    get_origin,
)

from pydantic.fields import FieldInfo

from .base import (
    IdentityResolver,
    VcoModel,
    is_dict,
    is_list,
    list_kind,
    python_name,
)
from .fields import get_key_spec
from .identity import ResolverNotLoadedError
from .layers import field_kind
from .merge import item_selector, select_items
from .owned import unwrap
from .patch import PatchError, patch_key, selector_of
from .rebase import (
    ItemPart,
    Key,
    KeyForm,
    KeyValue,
    Path,
    PathPart,
    item_key,
    key_form,
    key_text,
    node_field,
    pair_unique,
)


def item_part(model: VcoModel, field_name: str, position: int) -> ItemPart | None:
    """The path part that names the item at *position* of the list *field_name*.

    It names that item and no other: the label where the list declares one,
    else the item's key, else its alt key, else its position, each used only
    when it selects exactly that item (``item_selector``). A label and an alt
    key are a ``Key`` of their text; the key is the item's own ``Key``, with
    its parts as they are stored, so a composite key prints as ``Path`` writes
    it; a position is an ``int``. In ``hubs`` an item labelled ``Hub-A`` is
    ``Key("Hub-A")``, which a path prints as ``hubs[Hub-A]``, and an item of a
    list without a key is its position, ``plain[#0]``. An entry of a list of
    values is a ``Key`` of the entry, so a number prints bare (``ports[443]``).
    None where nothing names the item alone, and for an untyped list.

    It differs from ``item_selector`` in one case: an item whose label is the
    empty text is shown by its key (then its alt key, then its position),
    since ``areas[""]`` tells a reader nothing. A patch may still select that
    item by ``""``.

    Raises ``IndexError`` if there is no item at *position*, and ``TypeError``
    if the field is not a list: an entry of a map is named by its key, which a
    path writes after a dot, and not by an item part.
    """
    model_cls = type(model)
    field_info = model_cls.model_fields[field_name]
    kind = list_kind(field_info)
    if kind == "map":
        raise TypeError(
            f"{model_cls.__name__}.{field_name} is a map: an item part would "
            "show its entry as an item of a list. A path names an entry of a "
            "map by its key, after a dot; use the key"
        )
    text = item_selector(model, field_name, position)
    spec = get_key_spec(field_info)
    items: object = getattr(model, field_name)
    item = items[position] if is_list(items) else None
    key: KeyValue | None = None
    if spec is not None:
        key = item_key(item, spec.paths)
    elif kind == "value" and isinstance(item, (str, int)):
        # An entry of a list of values is its own key: a number prints bare.
        key = item
    if text == "" and spec is not None and spec.label is not None:
        alt = None if spec.alt is None else item_key(item, (spec.alt,))
        text = _selector_without_label(model, field_name, position, key, alt)
    if text is None:
        return None
    if key is not None and text == selector_of(Key(key)):
        return Key(key)
    # Only the text ``at(position)`` writes is the position: ``#01`` reads as
    # position 1 too, but printed as ``[#1]`` it would be read as another text.
    return position if text == selector_of(position) else Key(text)


def _selector_without_label(
    model: VcoModel,
    field_name: str,
    position: int,
    key: KeyValue | None,
    alt: KeyValue | None,
) -> str | None:
    """The selector of the item at *position* that is not its label, or None.

    *key* and *alt* are the item's key and its alt key, None when it has none.
    Each is tried in that order, then the position, and the first that selects
    exactly that item is used, as ``item_selector`` uses a candidate.
    """
    candidates = [
        *([] if key is None else [selector_of(Key(key))]),
        *([] if alt is None else [str(alt)]),
        selector_of(position),
    ]
    for candidate in candidates:
        try:
            if select_items(model, field_name, candidate) == [position]:
                return candidate
        except PatchError:
            # Two readings of a composite key: the next candidate is tried.
            continue
    return None


@dataclass(frozen=True, slots=True)
class _Model:
    """A place of a path that holds a model."""

    held: VcoModel | None
    """The model the data has there; None where the data ends above it."""
    classes: tuple[type[VcoModel], ...]
    """The classes a model there can be: the class of *held* where there is one."""


@dataclass(frozen=True, slots=True)
class _Items:
    """A place of a path that holds a list, or a map."""

    owner: VcoModel | None
    """The model the list is a field of; None where the data ends above it."""
    field: str
    """The attribute name of the list on its owner."""
    classes: tuple[type[VcoModel], ...]
    """The classes an item can be; empty where the items are not models."""
    is_map: bool
    """Whether it is a map, whose entries a path names by their keys."""


@dataclass(frozen=True, slots=True)
class _Either:
    """A place the data does not reach, declared as one model or a list of them."""

    models: tuple[type[VcoModel], ...]
    """The classes the one model can be."""
    items: tuple[type[VcoModel], ...]
    """The classes an item of the list can be."""


type _Place = _Model | _Items | _Either | None
"""Where a walk of a path stands; None where no class says what is there."""


@dataclass(frozen=True, slots=True)
class _Shape:
    """What a field is declared to hold, one level deep."""

    models: tuple[type[VcoModel], ...]
    """The classes of a model it holds as it is."""
    items: tuple[type[VcoModel], ...]
    """The classes of the items of a list it holds."""
    entries: tuple[type[VcoModel], ...]
    """The classes of the values of a map it holds."""
    is_list: bool
    """Whether it can hold a list, of models or of anything else."""


_UNION_ORIGINS = (Union, UnionType)  # pyright: ignore[reportDeprecated]  # still an origin
"""The origins of a union.

Before Python 3.14 ``X | Y`` and ``typing.Union`` are two, and ``T | MISSING``
is the second, since ``MISSING`` is a value and not a class.
"""


def _alternatives(annotation: object) -> list[object]:
    """The types *annotation* allows: the members of a union, else itself.

    Each is without its ``Annotated`` wrapper.
    """
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in _UNION_ORIGINS:
        return [
            inner for member in get_args(annotation) for inner in _alternatives(member)
        ]
    return [annotation]


def _model_classes(annotation: object) -> list[type[VcoModel]]:
    """The model classes among the types *annotation* allows."""
    return [
        member
        for member in _alternatives(annotation)
        if isinstance(member, type) and issubclass(member, VcoModel)
    ]


@functools.cache
def _shape(field_info: FieldInfo) -> _Shape:
    """What *field_info* is declared to hold.

    A model is looked for in the field itself, in the items of a list and in
    the values of a map, and no deeper: the keys of a map of lists are data,
    and nothing below them is known.
    """
    models: list[type[VcoModel]] = []
    items: list[type[VcoModel]] = []
    entries: list[type[VcoModel]] = []
    holds_list = False
    for member in _alternatives(field_kind(field_info).inner_type):
        origin, args = get_origin(member), get_args(member)
        if member is list or origin is list:
            holds_list = True
            items += _model_classes(args[0]) if args else []
        elif origin is dict and len(args) == 2:  # noqa: PLR2004 (dict[K, V] has two type arguments)
            entries += _model_classes(args[1])
        else:
            models += _model_classes(member)
    return _Shape(tuple(models), tuple(items), tuple(entries), holds_list)


def _holding(value: object, classes: tuple[type[VcoModel], ...]) -> _Model:
    """The place of *value*: the model it is, or one of *classes* the data lacks."""
    if isinstance(value, VcoModel):
        return _Model(value, (type(value),))
    return _Model(None, classes)


def _held(owner: VcoModel, name: str, field_info: FieldInfo) -> _Place:
    """The place of what the field *name* of *owner* holds; None if it holds none.

    The value decides, not the declared type: a field declared as one model or
    a list of them is a model place where it holds a model and a list where it
    holds a list. None where it holds neither (it is unset, a scalar, or a
    wrapped value), and for a list or a map the declared type has none of.
    """
    value = unwrap(getattr(owner, name))
    if isinstance(value, VcoModel):
        return _holding(value, ())
    shape = _shape(field_info)
    if is_list(value) and list_kind(field_info) not in (None, "map"):
        return _Items(owner, name, shape.items, is_map=False)
    if is_dict(value) and shape.entries:
        return _Items(owner, name, shape.entries, is_map=True)
    return None


def _declared(name: str, infos: list[FieldInfo]) -> _Place:
    """The place of the field *name* where the data does not reach it.

    *infos* are the field in each class that has it. A field that can hold a
    model or a list is an ``_Either``; a map only where it can hold nothing
    else.
    """
    shapes = [_shape(info) for info in infos]
    models = tuple(dict.fromkeys(cls for shape in shapes for cls in shape.models))
    items = tuple(dict.fromkeys(cls for shape in shapes for cls in shape.items))
    entries = tuple(dict.fromkeys(cls for shape in shapes for cls in shape.entries))
    if any(shape.is_list for shape in shapes):
        if models:
            return _Either(models, items)
        return _Items(None, name, items, is_map=False)
    if entries and not models:
        return _Items(None, name, entries, is_map=True)
    return _Model(None, models)


def _field(place: _Place, key: object) -> tuple[str, _Place] | None:
    """The attribute name of the field *key* names at *place*, and its value's place.

    *key* is any key a class reads the field from. The first of the place's
    classes that has a field for it names it. The place below is what the
    field holds, where the data has it; else what the field is declared to
    hold in the classes that agree on the name. None where *place* is not a
    model or no class has such a field.
    """
    if not isinstance(place, _Model):
        return None
    found = [
        field for cls in place.classes if (field := node_field(cls, key)) is not None
    ]
    if not found:
        return None
    name, first = found[0]
    below = None if place.held is None else _held(place.held, name, first)
    if below is None:
        below = _declared(name, [info for other, info in found if other == name])
    return name, below


def _selectors(part: PathPart) -> tuple[str, ...]:
    """The texts the item part *part* may be read as, as a selector.

    Its patch key (``patch_key``), which ``nest_patch`` nests a path under,
    and for a composite key also the text ``Path`` writes inside its brackets
    (``key_text``), which is what a reader sees. They differ where a part is
    a boolean: ``Path.parse`` reads a bare ``true`` as one, and its patch key
    writes it ``True``. An item is named only where every reading takes the
    part to it.
    """
    key = patch_key(part)
    if isinstance(part, Key) and isinstance(part.value, tuple):
        text = key_text(part.value)
        if text != key:
            return (key, text)
    return (key,)


def _position(
    place: _Items, owner: VcoModel, part: PathPart, resolvers: IdentityResolver | None
) -> int | None:
    """The position, in the list at *place* of *owner*, of the one item *part* names.

    None where it names none or several. A position names the item there,
    whatever the keys of the others (as a selector, ``#1`` would first be read
    as a key or a value that is that text). A key and a bare name are read as
    a patch's selector is, in each of their readings (``_selectors``), and
    name an item only where the readings agree; in a map, as the key of an
    entry in string form.
    """
    entries: object = getattr(owner, place.field)
    if isinstance(part, int):
        return part if is_list(entries) and part < len(entries) else None
    if place.is_map:
        text = patch_key(part)
        keys: list[object] = list(entries) if is_dict(entries) else []
        found = [index for index, key in enumerate(keys) if str(key) == text]
        return found[0] if len(found) == 1 else None
    readings = [
        select_items(owner, place.field, text, resolvers) for text in _selectors(part)
    ]
    found = readings[0]
    if any(other != found for other in readings[1:]):
        return None
    return found[0] if len(found) == 1 else None


def _item(
    place: _Items, part: PathPart, resolvers: IdentityResolver | None
) -> tuple[PathPart, _Place]:
    """The part that shows the item *part* names in the list at *place*, and its place.

    Whatever its kind, the part after a list names an item: a key, a
    position, or a bare name, which is a selector written as text. It is kept
    as written where the data has no list there and where it names no item or
    several. An entry of a map is shown by its key, as a name.
    """
    below = _Model(None, place.classes)
    owner = place.owner
    if owner is None:
        return part, below
    try:
        position = _position(place, owner, part, resolvers)
        if position is None:
            return part, below
        entries: object = getattr(owner, place.field)
        if is_dict(entries):
            entry = list(entries.values())[position]
            return patch_key(part), _holding(entry, place.classes)
        named = item_part(owner, place.field, position)
    except (PatchError, ResolverNotLoadedError, LookupError):
        # A path is shown whatever the data holds, so a lookup that fails keeps
        # the part as written. A PatchError is a text that two readings of a
        # composite key take to different items. A ResolverNotLoadedError (a
        # LookupError, named because it is the one a caller's resolvers raise)
        # is a name whose table is not loaded, and a ResolutionError a name the
        # table lacks. An IndexError or a KeyError is a list whose items are
        # not plain models (the effective view's lists hold wrapped values).
        return part, below
    item = entries[position] if is_list(entries) else None
    return part if named is None else named, _holding(item, place.classes)


def _either(place: _Either, part: PathPart) -> tuple[PathPart, _Place]:
    """*part* as shown below a field the data does not reach, a model or a list.

    A key or a position is an item of the list. A name is a field of the one
    model, unless the items of the list give that key another name: the class
    alone cannot say which is meant, so the name is kept as written and
    nothing below it is converted. A name the one model has no field for is a
    selector written as text.
    """
    items = _Model(None, place.items)
    if not isinstance(part, str):
        return part, items
    found = _field(_Model(None, place.models), part)
    if found is None:
        return part, items
    other = _field(items, part)
    if other is not None and other[0] != found[0]:
        return part, None
    return found


def _step(
    place: _Place, part: PathPart, resolvers: IdentityResolver | None
) -> tuple[PathPart, _Place]:
    """*part* as shown at *place*, and the place below it."""
    if isinstance(place, _Items):
        return _item(place, part, resolvers)
    if isinstance(place, _Either):
        return _either(place, part)
    found = _field(place, part)
    return (part, None) if found is None else found


def named_path(
    path: Path,
    data: VcoModel | type[VcoModel] | None,
    *,
    resolvers: IdentityResolver | None = None,
) -> Path:
    """*path* as pyvelo shows it: fields by attribute name, items by what names them.

    *data* is the model the path is a path of. A field part, written as any
    key the model reads the field from, becomes its attribute name. An item
    part is looked up in the list it follows (a position is the item there, a
    key is read as a patch's selector is), and where it names exactly one item
    it becomes ``item_part`` of that item, so ``rules[#0].hubs[h1].logicalId``
    is ``rules[Web].hubs[Hub-A].logical_id`` where the first rule is ``Web``
    and the hub ``h1`` is labelled ``Hub-A``. A bare name after a list is a
    selector too (``rules.Web`` is ``rules[Web]``). An entry of a map is its
    key as stored, after a dot. Every other part is kept as written: a key the
    model does not declare and all below it, and an item the data lacks.

    Where the data ends (a field is unset, an item is not found, or *data* is
    a class) the walk goes on over the classes, so the field names below are
    still converted; in a union, the first class with a field for the key
    names it. With *data* of None the path is returned as it is.

    A field declared as one model or a list of them is walked by what it
    holds. Where the data does not say, a key or a position after it is an
    item of the list, and a name after it is a field of the one model
    (``wssUsage.locationCount`` is ``wss_usage.location_count``); a name the
    items of the list read as another field is kept as written, with all
    below it. The keys of a map of lists are data: they and all below them
    are kept as written.

    *resolvers* is for a list whose key is a name the resolver knows another
    way; no label is looked up through it. Nothing is raised: a lookup that
    fails keeps the part as written.
    """
    place: _Place
    if data is None:
        place = None
    elif isinstance(data, VcoModel):
        place = _holding(data, ())
    else:
        place = _Model(None, (data,))
    parts: list[PathPart] = []
    for part in path.parts:
        shown, place = _step(place, part, resolvers)
        parts.append(shown)
    return Path(parts)


def named_change_path(
    path: Path,
    read: VcoModel,
    edited: VcoModel,
    *,
    removed: bool = False,
    resolvers: IdentityResolver | None = None,
) -> Path:
    """The path of a change from *read* to *edited*, as pyvelo shows it.

    A change names an item both documents have by its key in *read*, also
    where the edit renamed it, and an item only one of them has by its key
    there (``raw_changes``). Each part is shown as ``named_path`` shows it, in
    the data that has the place:

    - An item both have is found in *read* and shown as its partner in
      *edited* is named, so a change inside a renamed rule is shown under the
      rule's new name, and the path names that rule in the data the caller
      holds. The partner is the item with the same alt key, else with the
      same key, each where it is one item's alone on each side: the first two
      ways a save pairs the items of a keyed list (``pair_items``).
    - An item with no partner is taken for one the edit added, and is found
      in *edited*. With *removed* (the change removes what the path names) it
      is the item the edit removed: it and all below it are shown as *read*
      has them, below the parts above it as *edited* names them.
    - An entry of a map is named by its key in both.

    A part neither has is kept as written, and the fields below it are named
    by their classes. Nothing is raised.
    """
    was: _Place = _holding(read, ())
    now: _Place = _holding(edited, ())
    parts: list[PathPart] = []
    gone = False
    for part in path.parts:
        held, there = (False, None) if gone else _partner(was, now, part, resolvers)
        if gone or (removed and held and there is None):
            gone = True
            shown, was = _step(was, part, resolvers)
        else:
            shown, now = _step(now, part if there is None else there, resolvers)
            # An item of *read* that is not this one's partner is another item.
            unpaired = held and there is None
            was = None if unpaired else _step(was, part, resolvers)[1]
        parts.append(shown)
    return Path(parts)


def _found(
    place: _Place, part: PathPart, resolvers: IdentityResolver | None
) -> int | None:
    """The position of the one item *part* names in the list or map at *place*.

    None where *place* is no list or map the data has, where *part* names no
    item of it or several, and where the lookup fails (as ``_item`` has it).
    """
    if not isinstance(place, _Items) or place.owner is None:
        return None
    try:
        return _position(place, place.owner, part, resolvers)
    except (PatchError, ResolverNotLoadedError, LookupError):
        return None


def _partner(
    was: _Place, now: _Place, part: PathPart, resolvers: IdentityResolver | None
) -> tuple[bool, PathPart | None]:
    """Whether *part* names an item at *was*, and the part for its partner at *now*.

    *was* and *now* are one place of the data as read and as edited. The
    partner of an item of a list is given as its position in the list as
    edited (``_paired``); that of an entry of a map as *part*, where the map
    as edited has that key. The part is None where *was* has no such item,
    and where the item has no partner.
    """
    position = _found(was, part, resolvers)
    if position is None or not isinstance(was, _Items):
        return False, None
    if not isinstance(now, _Items) or was.is_map != now.is_map:
        return True, None
    if was.is_map:
        return True, None if _found(now, part, resolvers) is None else part
    return True, _paired(was, now).get(position)


def _paired(was: _Items, now: _Items) -> dict[int, int]:
    """The items of the keyed list at *was* that have a partner in the one at *now*.

    Each is a position as read with its partner's position as edited. The
    items are paired by alt key, then those left by key, each for a value one
    item has alone among those left on each side; keys that differ only by
    letter case are one where the list folds case. Empty for a list without a
    key, and where either side has no list.
    """
    if was.owner is None or now.owner is None:
        return {}
    spec = get_key_spec(type(was.owner).model_fields[was.field])
    old: object = getattr(was.owner, was.field)
    new: object = getattr(now.owner, now.field, None)
    if spec is None or not (is_list(old) and is_list(new)):
        return {}
    pairs: dict[int, int] = {}  # position as edited -> position as read
    old_left, new_left = list(range(len(old))), list(range(len(new)))
    passes = (((spec.alt,), False),) if spec.alt is not None else ()
    for paths, fold in (*passes, (spec.paths, spec.fold_case)):
        pair_unique(
            [_key_form(item, paths, fold=fold) for item in old],
            [_key_form(item, paths, fold=fold) for item in new],
            old_left,
            new_left,
            pairs,
        )
        paired = set(pairs.values())
        old_left = [i for i in old_left if i not in paired]
        new_left = [j for j in new_left if j not in pairs]
    return {i: j for j, i in pairs.items()}


def _key_form(item: object, paths: tuple[str, ...], *, fold: bool) -> KeyForm | None:
    """The key of the model *item* at *paths* in string form.

    Each part is case-folded with *fold*.
    """
    form = key_form(item_key(item, paths))
    if form is None or not fold:
        return form
    if isinstance(form, tuple):
        return tuple(text.casefold() for text in form)
    return form.casefold()


def named_error(
    error: PatchError,
    data: VcoModel,
    resolvers: IdentityResolver | None = None,
    *,
    patch: object,
) -> PatchError:
    """*error*, a refusal of *patch*, at its path as pyvelo shows it in *data*.

    *data* is the model *patch* was applied to, as it was: a selector the
    patch wrote (``#0``, a key, an alt key, an ID) names an item of it, and
    that item is shown as pyvelo shows it (``named_path``). An item *data*
    lacks keeps the selector as written.

    *patch* is needed because a path alone does not say whose item a part
    is: in a list the patch gives whole, as a list value, ``rules[#0]`` is
    the value's first item, not the one *data* holds there. So the path is
    walked over *patch* beside *data*: a field part is the one key of the
    patch's object that names that field, and an item part is the one entry
    of its object of item operations whose selector names the item. Where
    the patch has a list value there, or the walk cannot follow it (no one
    key or entry for the part), each item from there on keeps the part as
    raised, and the fields below are named by their classes.

    A ``duplicate`` error keeps its last part as raised: it is the selector,
    the add or the field the patch gives a second time, which the message
    sets beside the first, and the place to find in the patch.

    Nothing is raised: where the naming fails, *error* itself is returned, at
    its path as raised. The message, the kind and the cause are the error's
    (``PatchError.at``); *error* is not changed.
    """
    try:
        path = _error_path(error, data, resolvers, patch)
    except Exception:  # noqa: BLE001 (naming an error must never hide the refusal it carries)
        return error
    return error.at(path)


def _error_path(
    error: PatchError,
    data: VcoModel,
    resolvers: IdentityResolver | None,
    patch: object,
) -> Path:
    """The path of *error* as ``named_error`` shows it: ``named_path``'s walk.

    Beside each place of *data*, the walk holds what *patch* has there, as a
    one-tuple, and None from where it lost the patch; from there each list
    is walked as one the data does not reach, so its items keep their parts.
    """
    place: _Place = _holding(data, ())
    given: tuple[object] | None = (patch,)
    parts = error.path.parts
    shown: list[PathPart] = []
    for index, part in enumerate(parts):
        if error.kind == "duplicate" and index == len(parts) - 1:
            shown.append(part)
            break
        if isinstance(place, _Items) and place.owner is not None:
            given = None if given is None else _entry(place, part, given[0], resolvers)
            if given is None:
                place = _Items(None, place.field, place.classes, is_map=place.is_map)
        elif isinstance(place, _Model) and place.held is not None and given:
            given = _given_field(place.held, part, given[0])
        part_shown, place = _step(place, part, resolvers)
        shown.append(part_shown)
    return Path(shown)


def _given_field(
    model: VcoModel, part: PathPart, given: object
) -> tuple[object] | None:
    """What *given*, a patch for *model*, has for the field *part*, as a one-tuple.

    The value of the one key of *given* that names that field, by any name a
    patch accepts. None where *part* is not a field of *model*, *given* is
    not an object, or it has no one key for the field.
    """
    name = python_name(type(model), part) if isinstance(part, str) else None
    if name is None or not is_dict(given):
        return None
    keys = [
        key
        for key in given
        if isinstance(key, str) and python_name(type(model), key) == name
    ]
    return (given[keys[0]],) if len(keys) == 1 else None


def _entry(
    place: _Items, part: PathPart, given: object, resolvers: IdentityResolver | None
) -> tuple[object] | None:
    """What *given* has for the item *part* names in the list at *place*.

    *given* is what a patch has for that list or map: the entry of its object
    of item operations whose selector names that item, as a one-tuple. None
    where *given* is no such object (a list value, a scalar), the data has no
    one item for *part*, or no entry or several select it.
    """
    owner = place.owner
    if owner is None or not is_dict(given):
        return None
    try:
        position = _position(place, owner, part, resolvers)
    except (PatchError, LookupError):
        return None
    if position is None:
        return None
    keys = [key for key in given if _selects(place, owner, key, position, resolvers)]
    return (given[keys[0]],) if len(keys) == 1 else None


def _selects(
    place: _Items,
    owner: VcoModel,
    selector: object,
    position: int,
    resolvers: IdentityResolver | None,
) -> bool:
    """Whether *selector* names the item at *position* of the list at *place* alone."""
    try:
        return select_items(owner, place.field, selector, resolvers) == [position]
    except (PatchError, LookupError):
        return False


def named_value(
    value: object, model: type[VcoModel] | None, path: Path | None = None
) -> object:
    """The JSON *value* with each key a model declares by its attribute name.

    *value* is what a document of *model* holds at *path* (the whole document
    with no *path*); the path may name fields by any key the model reads them
    from. Objects are converted at every depth: at ``rules[Web].hubs[h1]``,
    ``{"logicalId": "h1"}`` is ``{"logical_id": "h1"}``. A key no class
    declares is kept as stored with all below it, and so are the keys of a
    map; in a union, the first class with a field for a key names it. Two keys
    of an object that name one field are both kept as stored, so neither value
    is dropped. A scalar and a ``MISSING`` are returned as they are.

    Where a field is declared as one model or a list of them, the value says
    which it is: an object is the one model, a list its items. One object
    found where only a list is declared is one item of it.

    The result shares no ``dict`` or ``list`` with *value*.
    """
    place: _Place = None if model is None else _Model(None, (model,))
    for part in () if path is None else path.parts:
        place = _step(place, part, None)[1]
    return _named(value, place)


def _named(value: object, place: _Place) -> object:
    """A copy of *value*, found at *place*, with its declared keys by attribute name.

    The value decides what it is, where *place* allows more than one shape: a
    list is a list of items; an object is one model, or one item where only a
    list is declared, or a map where *place* is one.
    """
    if is_list(value):
        below: _Place = None
        if isinstance(place, _Either):
            below = _Model(None, place.items)
        elif isinstance(place, _Items) and not place.is_map:
            below = _Model(None, place.classes)
        return [_named(item, below) for item in value]
    if not is_dict(value):
        return value
    if isinstance(place, _Items):
        entry = _Model(None, place.classes)
        if place.is_map:
            return {key: _named(item, entry) for key, item in value.items()}
        place = entry
    elif isinstance(place, _Either):
        place = _Model(None, place.models)
    fields = {key: _field(place, key) for key in value}
    claims = Counter(found[0] for found in fields.values() if found is not None)
    named: dict[object, object] = {}
    for key, item in value.items():
        found = fields[key]
        if found is None:
            named[key] = _named(item, None)
        else:
            name, below = found
            named[name if claims[name] == 1 else key] = _named(item, below)
    return named
