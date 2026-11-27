"""A path and a value as pyvelo shows them.

What pyvelo computes with names a field by the key the VCO uses and an item
by its key or its position. What it prints names a field by its python
attribute and an item by the text a reader knows it by, in the one form
``Path`` writes: ``segments[Guest].rules[Web].rule_logical_id``.

``named_path`` converts a path, ``ChangeNames`` the paths of the changes
between the data as read and as edited, ``named_error`` the path of a
``PatchError``, ``named_value`` the keys of a JSON value found at one, and
``item_part`` gives the part that names one item of a list. ``shown_value``
is ``named_value`` with every secret hidden: what a value shown goes through.
``vco_keys`` is the way back: a JSON value written in attribute names, in the
VCO's keys.
"""

import functools
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from types import UnionType
from typing import (
    Annotated,
    Final,
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
    wire_name,
)
from .documents import NamedDocumentError
from .fields import KeySpec, get_key_spec
from .identity import ResolverNotLoadedError
from .layers import field_kind
from .merge import item_selector, item_selectors, select_items
from .owned import unwrap
from .patch import PatchError, patch_key, selector_of
from .rebase import (
    ItemPart,
    Key,
    KeyForm,
    KeyValue,
    PairedList,
    Path,
    PathPart,
    compared_form,
    item_key,
    key_text,
    node_field,
    same_key,
)
from .secrets import hide_secrets


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
    _refuse_a_map(model, field_name)
    return _part_of(
        model, field_name, position, item_selector(model, field_name, position)
    )


def shown_item_parts(model: VcoModel, field_name: str) -> list[ItemPart | None]:
    """``item_part`` of each item of the list *field_name*, in the list's order.

    The parts are the same as ``item_part`` gives one position at a time; the
    selectors of all the items are found in one pass over the list
    (``item_selectors``), so naming every item of a long list reads it about
    once, not once for each item.

    Raises ``TypeError`` as ``item_part`` does.
    """
    _refuse_a_map(model, field_name)
    return [
        _part_of(model, field_name, position, text)
        for position, text in enumerate(item_selectors(model, field_name))
    ]


def _refuse_a_map(model: VcoModel, field_name: str) -> None:
    """Raise ``TypeError`` where the field *field_name* of *model* is a map."""
    model_cls = type(model)
    if list_kind(model_cls.model_fields[field_name]) == "map":
        raise TypeError(
            f"{model_cls.__name__}.{field_name} is a map: an item part would "
            "show its entry as an item of a list. A path names an entry of a "
            "map by its key, after a dot; use the key"
        )


def _part_of(
    model: VcoModel, field_name: str, position: int, text: str | None
) -> ItemPart | None:
    """``item_part`` of the item at *position*, whose ``item_selector`` is *text*."""
    field_info = type(model).model_fields[field_name]
    kind = list_kind(field_info)
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
    and nothing below them is known. A tuple of any length (``tuple[X, ...]``)
    is a list here: its JSON is one. A tuple of fixed length is not read.
    """
    models: list[type[VcoModel]] = []
    items: list[type[VcoModel]] = []
    entries: list[type[VcoModel]] = []
    holds_list = False
    for member in _alternatives(field_kind(field_info).inner_type):
        origin, args = get_origin(member), get_args(member)
        any_length = origin is tuple and args[1:] == (Ellipsis,)
        if member is list or origin is list or any_length:
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


@dataclass(frozen=True, slots=True)
class _Keyed:
    """A keyed list the data has: the model it is a field of, its key, its items."""

    owner: VcoModel
    field: str
    spec: KeySpec
    items: list[object]
    classes: tuple[type[VcoModel], ...]
    """The classes an item can be."""

    def below(self, position: int) -> _Model:
        """The place of the item at *position*."""
        return _holding(self.items[position], self.classes)


def _keyed(place: _Place) -> _Keyed | None:
    """The keyed list the data has at *place*; None for any other place."""
    if not isinstance(place, _Items) or place.owner is None or place.is_map:
        return None
    spec = get_key_spec(type(place.owner).model_fields[place.field])
    items: object = getattr(place.owner, place.field)
    if spec is None or not is_list(items):
        return None
    return _Keyed(place.owner, place.field, spec, items, place.classes)


type _Name = int | KeyForm
"""What an item part is found by: a position, or a key as its list compares keys."""


def _name(part: PathPart, spec: KeySpec) -> _Name:
    """*part* as an item of the list of *spec* is found by it.

    A bare name after a list is the key of that text.
    """
    if isinstance(part, int):
        return part
    form = compared_form(part.value if isinstance(part, Key) else part, spec)
    return "" if form is None else form


def _own_keys(keys: list[KeyValue | None], spec: KeySpec) -> dict[_Name, int]:
    """The position of each item whose key no other item has, by that key.

    *keys* are the keys of the items of the list of *spec*, None for an item
    without one.
    """
    forms = [compared_form(key, spec) for key in keys]
    counts = Counter(forms)
    return {
        form: position
        for position, form in enumerate(forms)
        if form is not None and counts[form] == 1
    }


def _positions(
    parts: tuple[ItemPart, ...], keys: list[KeyValue | None], spec: KeySpec
) -> list[int | None]:
    """Where each item of a JSON list is in the models of that list.

    *parts* are what names each item of the JSON list there (``item_parts``),
    and *keys* the keys of the models. The model of an item is at the item's
    position where the two lists have one length and every key that names a
    JSON item alone is the key of the model at its position (``same_key``,
    as a rebase checks a document against its parse). Where they are not (a
    plan puts back the segment an edit of an edge removed, after the others),
    an item is found by its key, where one model has that key; an item its
    position names is then not found, which is None.
    """
    if len(parts) == len(keys) and all(
        isinstance(part, int) or (key is not None and same_key(part.value, key))
        for part, key in zip(parts, keys, strict=True)
    ):
        return list(range(len(parts)))
    own = _own_keys(keys, spec)
    return [
        None if isinstance(part, int) else own.get(_name(part, spec)) for part in parts
    ]


@dataclass(frozen=True, slots=True)
class _ListNames:
    """One keyed list in the data as read and as edited, and the items changes name.

    Every position is one in the models: of ``read``, or of ``edited``.
    """

    spec: KeySpec
    """The key of the list."""
    read: _Keyed | None
    """The list as read; None where that data has none."""
    edited: _Keyed | None
    """The list as edited; None where that data has none."""
    read_at: dict[_Name, int]
    """The position as read of each item a change names as an item as read."""
    edited_at: dict[_Name, int]
    """The position as edited of each item a change names as one as edited."""
    partner: dict[int, int]
    """The position as edited of each item both have, by its position as read."""
    edited_own: dict[_Name, int]
    """The position as edited of each item whose key no other has there, by it."""
    parts: dict[bool, list[ItemPart | None] | None] = dataclass_field(
        default_factory=dict
    )
    """``shown_item_parts`` of the list as edited (True) and as read, once asked."""

    def find(
        self, part: PathPart, *, removed: bool = False
    ) -> tuple[int | None, int | None]:
        """Where the item *part* names is: its position as read, and as edited.

        An item both have has both. An item the edits removed has the first
        only, and an item they added the second only. *part* is read as a
        change writes it: the name of an item as read, then that of an added
        item. One name may be given to both a removed item (its key as read)
        and an added one (its key as edited): with *removed* it is the removed
        item, else the added. A name no change gives an item is the key an
        item has as edited (a path written from that data), and a position no
        change gives one is a position as read, or as edited where the item
        there as read has no partner and *removed* is not given.
        ``(None, None)`` for a part that names no item.
        """
        name = _name(part, self.spec)
        i = self.read_at.get(name)
        if i is None and isinstance(part, int) and name not in self.edited_at:
            i = part if self.read is not None and part < len(self.read.items) else None
            if i is None or (i not in self.partner and not removed):
                as_edited = self.edited is not None and part < len(self.edited.items)
                return (None, part) if as_edited else (i, None)
        j = None if i is None else self.partner.get(i)
        if j is not None or (removed and i is not None):
            return i, j
        added = self.edited_at.get(name)
        if added is None:
            added = self.edited_own.get(name)
        return (i, None) if added is None else (None, added)

    def part(self, position: int, *, edited: bool) -> ItemPart | None:
        """The part that shows the item at *position*: as edited, or as read.

        ``item_part`` of it, else the position: where nothing names the item
        alone, it is counted in the list the item is shown in. The parts of
        the whole list are found in one pass, once (``shown_item_parts``).
        None where that lookup fails, as ``_item`` has it.
        """
        if edited not in self.parts:
            held = self.edited if edited else self.read
            try:
                self.parts[edited] = (
                    None if held is None else shown_item_parts(held.owner, held.field)
                )
            except (PatchError, ResolverNotLoadedError, LookupError):
                self.parts[edited] = None
        parts = self.parts[edited]
        if parts is None:
            return None
        part = parts[position]
        return position if part is None else part

    def shown(self, part: ItemPart) -> ItemPart:
        """The part that shows the item of the list as edited that *part* names.

        *part* is as a change writes it for a place, a move or an order
        entry: an item both have by its name as read, an added item by its
        name as edited. It is kept as written where it names no such item.
        """
        _, j = self.find(part)
        named = None if j is None else self.part(j, edited=True)
        return part if named is None else named


def _list_names(
    spec: KeySpec,
    record: PairedList | None,
    read: _Keyed | None,
    edited: _Keyed | None,
) -> _ListNames:
    """The items of one keyed list, as *record* says the changes name them.

    *read* and *edited* are the list, whose key is *spec*, in the data as read
    and as edited; one of them may be None. *record* is how the changes of
    the list were made (``raw_changes``): the pairing they were made by is
    the one used here, and an item is found by the name the changes give it,
    at its place in the list that name is of. With no *record* (the list did
    not change, or was replaced whole, and no change names an item of it) an
    item is found in each list by its own key, where no other item of that
    list has the key, and one both have is taken for one item.
    """
    read_keys, edited_keys = (
        [] if side is None else [item_key(item, spec.paths) for item in side.items]
        for side in (read, edited)
    )
    read_at: dict[_Name, int] = {}
    edited_at: dict[_Name, int] = {}
    partner: dict[int, int] = {}
    own = _own_keys(edited_keys, spec)
    if record is None:
        read_at = _own_keys(read_keys, spec)
        partner = {i: own[name] for name, i in read_at.items() if name in own}
        return _ListNames(spec, read, edited, read_at, own, partner, own)
    old_at = _positions(record.old_parts, read_keys, spec)
    new_at = _positions(record.new_parts, edited_keys, spec)
    for part, i in zip(record.old_parts, old_at, strict=True):
        if i is not None:
            read_at[_name(part, spec)] = i
    for part, j in zip(record.names, new_at, strict=True):
        if j is not None:
            edited_at[_name(part, spec)] = j
    for old, new in record.pairs:
        i, j = old_at[old], new_at[new]
        if i is not None and j is not None:
            partner[i] = j
    return _ListNames(spec, read, edited, read_at, edited_at, partner, own)


@dataclass(frozen=True, slots=True)
class ChangeNames:
    """The paths of the changes from one document to another, as pyvelo shows them.

    A change names an item both documents have by its key as read, also
    where the edit renamed it, and an item only one of them has by its key
    there; an item whose key is absent, or is another item's too, by its
    position in that list (``raw_changes``). ``lists`` is how the changes
    paired the items of each keyed list. An item part of a change is found
    by it: at its position in the list the name is of, and from there, for an
    item both have, at the position of its partner. No part is read as a
    selector, so a name is never taken for the label, the alt key or the
    position of another item. The item found is then shown as ``item_part``
    names it, in the data that has the place:

    - An item both have is shown as *edited* names it, so a change inside a
      renamed rule is shown under the rule's new name, and the path names
      that rule in the data the caller holds.
    - An item the edits removed, and all below it, is shown as *read* has it,
      below the parts above it as *edited* names them.
    - An item the edits added is shown as *edited* has it.
    - Where nothing names an item alone, its position is shown: in the list
      as edited, or, for a removed item, in the list as read.
    - An entry of a map is named by its key in both, and an item of a list
      without a key by its position.

    A part neither has is kept as written, and the fields below it are named
    by their classes. Nothing is raised.

    ``path`` shows a path and ``items`` the items of one list. Each list is
    known by the two models that hold it, and what is found for it is kept,
    so neither document may change while this is used.
    """

    read: VcoModel
    """The document as read."""
    edited: VcoModel
    """The document as edited."""
    lists: Mapping[Path, PairedList] = dataclass_field(
        default_factory=dict[Path, PairedList]
    )
    """How the changes paired each keyed list, by its path as they write it.

    It is what ``raw_changes`` records for the two JSON documents *read* and
    *edited* are the models of, a list before the lists inside its items. A
    list it lacks was not paired: no change names an item of it, and a path
    through it finds an item by its own key.
    """
    resolvers: IdentityResolver | None = None
    """For a list without a key whose entries a resolver knows another way."""
    _made: dict[tuple[int, int, str], _ListNames] = dataclass_field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Find each list of ``lists`` in the two documents, once.

        A list is then known by the models that hold it, however a path to
        it is written.
        """
        for path, record in self.lists.items():
            _, was, now = self._walk(path, removed=False)
            read, edited = _keyed(was), _keyed(now)
            held = read if read is not None else edited
            if held is not None:
                key = _list_key(read, edited, held.field)
                self._made[key] = _list_names(held.spec, record, read, edited)

    def path(self, path: Path, *, removed: bool = False) -> Path:
        """*path*, the path of a change from ``read`` to ``edited``, as shown.

        With *removed* (the change removes what the path names) a name that
        both a removed and an added item have is the removed item's.
        """
        return Path(self._walk(path, removed=removed)[0])

    def items(self, path: Path) -> Callable[[ItemPart], ItemPart] | None:
        """What shows an item of the keyed list at *path*, as a change names it.

        *path* is the path of the list, as the changes write it. What is
        given takes the name a change gives an item of the list as edited
        (the item an added one is placed after, each item of a move, a name
        of an order entry) to the part that shows that item: the one ``path``
        shows it by. A name that is no item's is kept. The whole list is
        named in one pass, once, so the names of a long list cost about the
        list. None where neither document has a keyed list there.
        """
        _, was, now = self._walk(path, removed=False)
        names = self._list(was, now)
        return None if names is None else names.shown

    def _list(self, was: _Place, now: _Place) -> _ListNames | None:
        """The keyed list at *was* as read and at *now* as edited; None for no list.

        One of ``lists`` was found when this was made; any other is one the
        changes did not pair.
        """
        read, edited = _keyed(was), _keyed(now)
        held = read if read is not None else edited
        if held is None:
            return None
        key = _list_key(read, edited, held.field)
        made = self._made.get(key)
        if made is None:
            made = self._made[key] = _list_names(held.spec, None, read, edited)
        return made

    def _walk(
        self, path: Path, *, removed: bool
    ) -> tuple[list[PathPart], _Place, _Place]:
        """The parts of *path* as shown, and where it ends as read and as edited.

        It ends nowhere as edited (None) once it is below a removed item.
        """
        was: _Place = _holding(self.read, ())
        now: _Place = _holding(self.edited, ())
        parts: list[PathPart] = []
        gone = False
        for part in path.parts:
            names = self._list(was, now)
            if names is None:
                shown, was, now, gone = self._plain(
                    (was, now), part, removed=removed, gone=gone
                )
                parts.append(shown)
                continue
            i, j = names.find(part, removed=removed)
            named = None
            if j is not None and names.edited is not None:
                named = names.part(j, edited=True)
                now = names.edited.below(j)
                was = None if i is None or names.read is None else names.read.below(i)
            elif i is not None and names.read is not None:
                named = names.part(i, edited=False)
                was, now, gone = names.read.below(i), None, True
            else:
                was = _Model(None, () if names.read is None else names.read.classes)
                now = (
                    None if names.edited is None else _Model(None, names.edited.classes)
                )
            parts.append(part if named is None else named)
        return parts, was, now

    def _plain(
        self,
        places: tuple[_Place, _Place],
        part: PathPart,
        *,
        removed: bool,
        gone: bool,
    ) -> tuple[PathPart, _Place, _Place, bool]:
        """One step of ``_walk`` at a place that is no keyed list the data has.

        A field, an entry of a map, an item of a list without a key. It gives
        the part as shown, the places below it as read and as edited, and
        whether the path is now below something only the data as read has.
        """
        was, now = places
        if not gone:
            held = _found(was, part, self.resolvers) is not None
            both = (
                held
                and isinstance(was, _Items)
                and isinstance(now, _Items)
                and was.is_map
                and now.is_map
                and _found(now, part, self.resolvers) is not None
            )
            if not (removed and held and not both):
                shown, below = _step(now, part, self.resolvers)
                # An entry or item of ``read`` that ``edited`` lacks is another.
                unpaired = held and not both
                was = None if unpaired else _step(was, part, self.resolvers)[1]
                return shown, was, below, False
        shown, below = _step(was, part, self.resolvers)
        return shown, below, None, True


def _list_key(
    read: _Keyed | None, edited: _Keyed | None, field: str
) -> tuple[int, int, str]:
    """What a keyed list is known by: the models that hold it, and its field.

    What is kept under it holds the two models, so the ID of one is never
    another's.
    """
    return (
        0 if read is None else id(read.owner),
        0 if edited is None else id(edited.owner),
        field,
    )


def _found(
    place: _Place, part: PathPart, resolvers: IdentityResolver | None
) -> int | None:
    """The position of the one entry *part* names in the map or list at *place*.

    For a map and for a list without a key: an item of a keyed list is found
    by ``_ListNames``. None where *place* is no list or map the data has,
    where *part* names no entry of it or several, and where the lookup fails
    (as ``_item`` has it).
    """
    if not isinstance(place, _Items) or place.owner is None:
        return None
    try:
        return _position(place, place.owner, part, resolvers)
    except (PatchError, ResolverNotLoadedError, LookupError):
        return None


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


def shown_value(
    value: object, model: type[VcoModel] | None, path: Path | None = None
) -> object:
    """*value* as pyvelo shows it: ``named_value`` of it, with every secret hidden.

    *value* is what a document of *model* holds at *path* (the whole document
    with no *path*), in the VCO's keys. The mask ``HIDDEN`` stands in place of
    the value of every field marked ``Secret`` (``hide_secrets``), and is the
    result when *path* is at or below such a field; then each key a class
    declares is its attribute name. With *model* of None the value is copied
    and nothing is hidden: nothing says where a secret is.
    """
    hidden = hide_secrets(model, Path(()) if path is None else path, value)
    return named_value(hidden, model, path)


_VCO_KEY_GIVEN: Final = (
    "`{key}` is the VCO's key for `{name}`, and this document is in attribute "
    "names. Every attribute name of it is renamed to the VCO's key; in a document "
    "that is already in the VCO's keys that renames a stored key that happens to "
    "be spelled like an attribute name, and what is saved then differs from what "
    "was read. Write the field as `{name}`, or give a document in the VCO's keys "
    "to model_validate."
)
"""What refuses a key of the VCO's form in a document in attribute names."""


def vco_keys(
    value: object, model: type[VcoModel] | None, path: Path | None = None
) -> object:
    """The JSON *value*, a document in attribute names, with the VCO's keys.

    The inverse of ``named_value``. *value* is what a document of *model*
    holds at *path* (the whole document with no *path*), and it is walked as
    ``named_value`` walks it: a model, the items of a list, the entries of a
    map, and a field declared as one model or a list of them by the shape of
    the value. A key that is the attribute name of a field of a class there
    becomes the key that class writes the field under (the first such class,
    in a union), and the walk goes on below it. The attribute name is looked
    for first: a name that is also a key another field is read from is the
    attribute. A key no class declares is kept as given with all below it, and
    so are the keys of a map. A scalar and a ``MISSING`` are returned as they
    are, and with *model* of None the value is returned as a copy.

    Any other key a class there reads a field from (the VCO's key, the
    camelCase form) raises ``NamedDocumentError`` of kind ``vco_key``, also
    beside the attribute name of the same field: the document mixes the two
    forms. In a document that is in the VCO's keys, a stored key spelled like
    an attribute name would be renamed, and what is saved would differ from
    what was read. The error's path is the field's, by attribute names: *path*
    (which may name fields by any key the model reads them from), then the
    way down the value, an item by its position.

    The result shares no ``dict`` or ``list`` with *value*.
    """
    place: _Place = None if model is None else _Model(None, (model,))
    parts: list[PathPart] = []
    for part in () if path is None else path.parts:
        shown, place = _step(place, part, None)
        parts.append(shown)
    return _with_vco_keys(value, place, Path(parts))


def _with_vco_keys(value: object, place: _Place, path: Path) -> object:
    """A copy of *value*, found at *place*, with its attribute names as the VCO's keys.

    *path* is where *value* is, for a refusal. The value decides what it is,
    as in ``_named``: a list is a list of items; an object is one model, or
    one item where only a list is declared, or a map where *place* is one.
    """
    if is_list(value):
        below: _Place = None
        if isinstance(place, _Either):
            below = _Model(None, place.items)
        elif isinstance(place, _Items) and not place.is_map:
            below = _Model(None, place.classes)
        return [
            _with_vco_keys(item, below, path.child(position))
            for position, item in enumerate(value)
        ]
    if not is_dict(value):
        return value
    if isinstance(place, _Items):
        entry = _Model(None, place.classes)
        if place.is_map:
            return {
                key: _with_vco_keys(item, entry, path.child(str(key)))
                for key, item in value.items()
            }
        place = entry
    elif isinstance(place, _Either):
        place = _Model(None, place.models)
    written: dict[object, object] = {}
    for key, item in value.items():
        attribute = _attribute(place, key)
        if attribute is None:
            _refuse_a_vco_key(place, key, path)
            written[key] = _with_vco_keys(item, None, path.child(str(key)))
        else:
            vco_key, held = attribute
            written[vco_key] = _with_vco_keys(item, held, path.child(str(key)))
    return written


def _attribute(place: _Place, name: object) -> tuple[str, _Place] | None:
    """The VCO's key of the attribute *name* at *place*, and its value's place.

    The first of the place's classes that has the attribute gives the key.
    The place below is what the field is declared to hold in the classes that
    have the attribute. None where *place* is not a model or no class has the
    attribute.
    """
    if not isinstance(place, _Model) or not isinstance(name, str):
        return None
    owners = [cls for cls in place.classes if name in cls.model_fields]
    if not owners:
        return None
    infos = [cls.model_fields[name] for cls in owners]
    return wire_name(owners[0], name), _declared(name, infos)


def _refuse_a_vco_key(place: _Place, key: object, path: Path) -> None:
    """Raise if *key*, no attribute name at *place*, is a key a class there reads.

    *path* is the object the key is in; the error is at the field the key
    names, by its attribute name.
    """
    found = _field(place, key)
    if found is not None:
        name = found[0]
        message = _VCO_KEY_GIVEN.format(key=key, name=name)
        raise NamedDocumentError(message, path.child(name), "vco_key")
