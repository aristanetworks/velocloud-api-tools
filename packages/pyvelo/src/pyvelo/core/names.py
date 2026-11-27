"""A path and a value as pyvelo shows them.

What pyvelo computes with names a field by the key the VCO uses and an item
by its key or its position. What it prints names a field by its python
attribute and an item by the text a reader knows it by, in the one form
``Path`` writes: ``segments[Guest].rules[Web].rule_logical_id``.

``named_path`` converts a path, ``named_value`` the keys of a JSON value found
at one, and ``item_part`` gives the part that names one item of a list.
"""

from collections import Counter
from dataclasses import dataclass

from .base import (
    IdentityResolver,
    VcoModel,
    field_models,
    is_dict,
    is_list,
    list_kind,
)
from .fields import get_key_spec
from .identity import ResolverNotLoadedError
from .merge import item_selector, select_items
from .owned import unwrap
from .patch import PatchError, position_of, selector_of
from .rebase import ItemPart, Key, KeyValue, Path, PathPart, item_key, node_field


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
    values is a ``Key`` of its text. None where nothing names the item alone,
    and for an untyped list.

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
    if list_kind(field_info) == "map":
        raise TypeError(
            f"{model_cls.__name__}.{field_name} is a map: an item part would "
            "show its entry as an item of a list. A path names an entry of a "
            "map by its key, after a dot; use the key"
        )
    text = item_selector(model, field_name, position)
    spec = get_key_spec(field_info)
    items: object = getattr(model, field_name)
    item = items[position] if is_list(items) else None
    key = None if spec is None else item_key(item, spec.paths)
    if text == "" and spec is not None and spec.label is not None:
        alt = None if spec.alt is None else item_key(item, (spec.alt,))
        text = _selector_without_label(model, field_name, position, key, alt)
    if text is None:
        return None
    if key is not None and text == selector_of(Key(key)):
        return Key(key)
    return position if position_of(text) == position else Key(text)


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


type _Place = _Model | _Items | None
"""Where a walk of a path stands; None where no class says what is there."""


def _holding(value: object, classes: tuple[type[VcoModel], ...]) -> _Model:
    """The place of *value*: the model it is, or one of *classes* the data lacks."""
    if isinstance(value, VcoModel):
        return _Model(value, (type(value),))
    return _Model(None, classes)


def _field(place: _Place, key: object) -> tuple[str, _Place] | None:
    """The attribute name of the field *key* names at *place*, and its value's place.

    *key* is any key a class reads the field from. The first of the place's
    classes that has a field for it names it; the place below is what that
    field holds in the classes that agree on the name. None where *place* is
    not a model or no class has such a field.
    """
    if not isinstance(place, _Model):
        return None
    found = [
        field for cls in place.classes if (field := node_field(cls, key)) is not None
    ]
    if not found:
        return None
    name, first = found[0]
    classes = tuple(
        dict.fromkeys(
            cls for other, info in found if other == name for cls in field_models(info)
        )
    )
    kind = list_kind(first)
    if kind is not None:
        return name, _Items(place.held, name, classes, kind == "map")
    value = None if place.held is None else unwrap(getattr(place.held, name))
    return name, _holding(value, classes)


def _selector(part: PathPart) -> str:
    """The selector a patch would name the item *part* names by."""
    return part if isinstance(part, str) else selector_of(part)


def _position(
    owner: VcoModel,
    field: str,
    part: PathPart,
    resolvers: IdentityResolver | None,
) -> int | None:
    """The position in the list *field* of *owner* of the one item *part* names.

    None where it names none or several. A position names the item there,
    whatever the keys of the others (as a selector, ``#1`` would first be read
    as a key or a value that is that text). A key and a bare name are read as
    a patch's selector is.
    """
    if isinstance(part, int):
        entries: object = getattr(owner, field)
        return part if is_list(entries) and part < len(entries) else None
    positions = select_items(owner, field, _selector(part), resolvers)
    return positions[0] if len(positions) == 1 else None


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
        position = _position(owner, place.field, part, resolvers)
        if position is None:
            return part, below
        entries: object = getattr(owner, place.field)
        if is_dict(entries):
            entry = list(entries.values())[position]
            return _selector(part), _holding(entry, place.classes)
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


def _step(
    place: _Place, part: PathPart, resolvers: IdentityResolver | None
) -> tuple[PathPart, _Place]:
    """*part* as shown at *place*, and the place below it."""
    if isinstance(place, _Items):
        return _item(place, part, resolvers)
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

    The result shares no ``dict`` or ``list`` with *value*.
    """
    place: _Place = None if model is None else _Model(None, (model,))
    for part in () if path is None else path.parts:
        place = _step(place, part, None)[1]
    return _named(value, place)


def _named(value: object, place: _Place) -> object:
    """A copy of *value*, found at *place*, with its declared keys by attribute name."""
    if is_list(value):
        below = (
            _Model(None, place.classes)
            if isinstance(place, _Items) and not place.is_map
            else None
        )
        return [_named(item, below) for item in value]
    if not is_dict(value):
        return value
    if isinstance(place, _Items):
        below = _Model(None, place.classes) if place.is_map else None
        return {key: _named(item, below) for key, item in value.items()}
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
