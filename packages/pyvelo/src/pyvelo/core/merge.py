"""The merge engine behind ``VcoModel.merge_patch``.

``apply_patch`` merges a partial dict into a model; everything private here
supports it. ``VcoModel.merge_patch`` in ``base.py`` is a thin call into it.
"""

from __future__ import annotations

import copy
import difflib
import enum
import functools
import json
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Final,
    Literal,
    LiteralString,
    cast,
    get_args,
    get_origin,
)

from pydantic import (
    MISSING,
    ConfigDict,
    TypeAdapter,
    ValidationError,
)
from pydantic_core import InitErrorDetails, PydanticCustomError, PydanticUndefined

from .base import (
    IdentityResolver,
    JsonObject,
    ListItemType,
    ListKind,
    VcoModel,
    _DefaultSource,  # pyright: ignore[reportPrivateUsage]
    _effective_default,  # pyright: ignore[reportPrivateUsage]
    _field_accepts_missing,  # pyright: ignore[reportPrivateUsage]
    _field_accepts_none,  # pyright: ignore[reportPrivateUsage]
    _field_model_type,  # pyright: ignore[reportPrivateUsage]
    _field_model_types,  # pyright: ignore[reportPrivateUsage]
    _field_names_by_key,  # pyright: ignore[reportPrivateUsage]
    _field_value,  # pyright: ignore[reportPrivateUsage]
    _held_type,  # pyright: ignore[reportPrivateUsage]
    _holds_empty_text,  # pyright: ignore[reportPrivateUsage]  # the core engines share it
    _id_adapter,  # pyright: ignore[reportPrivateUsage]
    _id_type,  # pyright: ignore[reportPrivateUsage]
    _is_vco_model_type,  # pyright: ignore[reportPrivateUsage]
    _merged_fields,  # pyright: ignore[reportPrivateUsage]
    _union_members,  # pyright: ignore[reportPrivateUsage]
    _unwrap_annotated,  # pyright: ignore[reportPrivateUsage]
    _variant_names,  # pyright: ignore[reportPrivateUsage]
    field_models,
    is_dict,
    is_list,
    list_item_type,
    list_kind,
    python_name,
)
from .fields import (
    ConfigContext,
    FixedList,
    _service_type,  # pyright: ignore[reportPrivateUsage]  # the core engines share it
    get_fixed_list,
    get_key_spec,
    get_name_of,
    get_ref_only,
    get_resolvable,
    has_override_flag,
    is_edge_local,
    is_secret,
)
from .identity import ResolutionError, ResolverNotLoadedError, resolution_error
from .layers import Edge, Kind, Layered, field_kind, inner_adapter
from .owned import ByFlag, Owned, group_of, groups_of, unwrap
from .patch import (
    ItemOp,
    PatchError,
    Place,
    at,
    checked_patch,
    is_new,
    key_selector,
    parse_item_op,
    patch_key,
    position_of,
    selector_of,
)
from .rebase import (
    Key,
    KeyForm,
    KeyScalar,
    KeyValue,
    Path,
    PathPart,
    _folded,  # pyright: ignore[reportPrivateUsage]  # one fold for every key compare
    item_key,
    key_form,
    key_part,
    key_text,
    parse_key_text,
)
from .secrets import HIDDEN

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic.fields import FieldInfo


def apply_patch(
    model: VcoModel, patch: Mapping[str, object], resolvers: IdentityResolver | None
) -> VcoModel:
    """Recursively merge a partial dict into *model*, in place.

    See ``VcoModel.merge_patch``.

    *patch* must be a ``dict`` (``TypeError`` otherwise) of JSON values
    (``checked_patch``). It is checked and copied once, so *model* shares no
    object with it.

    All or nothing: the patch is merged into a deep copy of *model* first, so
    a patch that raises does so before *model* is touched. Each model the
    patch changes, and the copy itself, is checked once the merge into it is
    complete (``_check_merged``); *model* is then merged without a second check.
    """
    if not isinstance(patch, dict):
        raise TypeError(
            f"a patch is a dict, not {type(patch).__name__}: give an object, "
            'and to set one value write {"<field>": value}'
        )
    items = cast("dict[object, object]", checked_patch(patch))
    trial = model.model_copy(deep=True)
    _merge_model(trial, items, resolvers, Path(()))
    # The trial is what is checked: the same merge into *model* gives the same
    # result, so a second check of *model* could only break the all or nothing.
    _check_merged(trial, type(trial).model_validate, Path(()))
    _merge_model(model, items, resolvers, Path(()))
    return model


_PATH_FORM = (
    "field names joined by dots, with an item in brackets after its list, as "
    "pyvelo prints it: segments[Guest].rules[Web].match"
)


def nest_patch(
    model: type[VcoModel],
    patch: object,
    *,
    segment: str | None = None,
    path: str | Path | None = None,
) -> dict[str, object]:
    """The patch for *model* that an addressed call stands for.

    Without *segment* and *path*, *patch* itself, which must be a ``dict``
    (``TypeError`` otherwise). Otherwise *patch* is any JSON value and is
    nested under the address: ``path="a.b"`` gives ``{"a": {"b": patch}}``,
    ``segment="S"`` gives ``{"segments": {"S": patch}}``, and both give
    ``{"segments": {"S": {"a": {"b": patch}}}}``. What is returned is a
    checked copy of the patch (``checked_patch``): it holds JSON values only,
    and an error is reported under the address.

    *path* is a ``Path`` or the text of one (``Path.parse``): field names
    joined by dots, with an item in brackets after its list, as a path is
    printed. Its parts map one for one onto the keys of the nested patch:

    - a field part is the key of that field. It names the field by any name
      ``merge_patch`` accepts, and the nested patch uses the python name;
    - the part after a list field, or a field that holds a map of models, is
      the key of one of its items, whatever its kind: an item part as its
      selector (``rules[Web]`` gives ``{"rules": {"Web": ...}}``, a composite
      key ``key_selector`` of it, ``rules[#0]`` gives ``"#0"``) and a bare
      name as written (``rules.Web`` is ``rules[Web]``). The parts below it
      are fields of the item.

    So ``path='rules[Web].match'`` gives ``{"rules": {"Web": {"match":
    patch}}}``. A path may end anywhere: at a field, at a list, or at an item,
    whose patch is then an item's (a plain object, or an operation).

    A field that takes one model or a list of them (a DNS provider) is walked
    as what the path names: a name after it that is a field of the one model
    is that field, by its python name, and any other part is an item of the
    list. ``merge_patch`` then reads the patch by what the field holds.

    A position part is the key ``"#n"``, which a patch reads first as the key
    of an item of a keyed list, or as an entry of a list of values, and as a
    position only when no item has that text. No instance is consulted here,
    so this cannot be told apart: ``ConfigPipeline.apply``, which holds the
    data, refuses a position that another item's key or value would take.

    ``segments[S]`` at the head of *path* stands for *segment*: the address
    is one path (``address_path``), so ``path="segments[S].a.b"`` and
    ``segment="S", path="a.b"`` give the same patch. Giving the segment both
    ways is refused, whether or not the two name one segment.

    With *segment* the parts start at the item type of the ``segments``
    field, without at *model*. A field part is looked up in every class the
    value there can be: the variants of a union field, of the items of a
    list, and of the ``segments`` item type too. The classes without the
    field drop out, and the next part is looked up in every model the field
    can hold in the classes that remain. Where every class that has the field
    gives it one python name, that name is the key; where two variants read
    one key into fields of different names, the key is the part as written,
    and ``merge_patch``, which holds the value as read, takes the name of the
    variant it is. Only the classes are consulted, never an instance, so a
    patch for another variant than the stored one is accepted here and
    refused by ``merge_patch``, as the nested form is. An item is not looked
    up either: a part that names no item of its list is ``merge_patch``'s to
    refuse (``no_item``).

    A bad address raises ``PatchError`` of kind ``address``. A text that is
    not a path, a path with no parts, and a segment given twice raise at the
    empty path (``address_path``). The others
    raise at the path of the part that failed, in python names, under
    ``segments[S]`` when there is a *segment*: a part that names no field of
    any class (the message gives the classes joined with `` | ``, the part
    and the field names of them all); an item part where no list or map is
    (at the start, after an item, after a field that holds a model or a plain
    value); any part after a list of untyped items, which a patch replaces
    whole; a field part after a field or an item that holds no model (raised
    at that field or item); and *segment* for a class with no ``segments``
    field.
    """
    if segment is None and path is None:
        if not isinstance(patch, dict):
            raise TypeError(
                f"a patch with no address must be a dict, not {type(patch).__name__}: "
                "give an object, or name the place a value goes with segment or path"
            )
        return cast("dict[str, object]", checked_patch(patch))
    parts = address_path(segment, path).parts
    place = _AddressPlace("model", (model,), (), None)
    walked = Path(())
    if segment is not None:
        walked = walked.child("segments")
        place = _AddressPlace(
            "model",
            _segment_item_classes(model, walked),
            (),
            f"an item of {model.__name__}.segments",
        )
        walked = walked.child(Key(segment))
        parts = parts[2:]
    # Where the patch sits once nested, in python names, for its errors and its depth.
    keys, where_nested = _address_names(place, parts, walked)
    nested = checked_patch(patch, where_nested)
    for key in reversed(keys):
        nested = {key: nested}
    if segment is None:
        return cast("dict[str, object]", nested)
    return {"segments": {segment: nested}}


def nested_under(address: Path, patch: object) -> object:
    """*patch* nested under the parts of *address*, with no model consulted.

    Each part is a key (``patch_key``): a name as written, an item part as its
    selector. It is ``nest_patch`` without the classes, for where there is no
    data to take them from: a field is not looked up, so it keeps the name it
    was given, and nothing is checked or copied.
    """
    nested = patch
    for part in reversed(address.parts):
        nested = {patch_key(part): nested}
    return nested


def address_path(segment: str | None, path: str | Path | None) -> Path:
    """The whole address of an addressed patch, as one path.

    ``segments[<segment>]`` when there is a *segment*, then the parts of
    *path*, which is a ``Path`` or the text of one (``Path.parse``). A path
    that starts at an item of ``segments`` names its segment itself, so
    ``address_path(None, "segments[Guest].rules")`` and
    ``address_path("Guest", "rules")`` are one address. With neither, the
    empty path.

    Raises ``PatchError`` of kind ``address`` at the empty path for a text
    that is not a path, for a *path* with no parts, and for a segment given
    twice: by *segment* and by a *path* that starts at an item of
    ``segments``, whether or not the two name one segment. One of them would
    be dropped, and the patch would go to a segment the caller did not mean.
    """
    read = Path(()) if path is None else _read_path(path)
    if segment is None:
        return read
    parts = read.parts
    if len(parts) > 1 and parts[0] == "segments":
        below = Path(parts[2:])
        instead = (
            f"start the path below the segment (path={str(below)!r})"
            if below.parts
            else "leave out the path"
        )
        raise PatchError(
            f"the segment is given twice: by segment={segment!r} and by the "
            f"path, which starts at {Path(parts[:2])}. One of the two would be "
            "dropped without a word. Give it once: leave out segment, or "
            f"{instead}",
            Path(()),
            "address",
        )
    return Path(("segments", Key(segment), *parts))


def _read_path(path: str | Path) -> Path:
    """*path* as a ``Path``: itself, or the path its text is (``Path.parse``).

    A text that is not a path, and a path with no parts, raise ``PatchError``
    of kind ``address`` at the empty path.
    """
    if isinstance(path, Path):
        read = path
    else:
        try:
            read = Path.parse(path)
        except ValueError as exc:
            raise PatchError(
                f"the path {path!r} cannot be read: {exc}. A path is {_PATH_FORM}",
                Path(()),
                "address",
            ) from exc
    if not read.parts:
        raise PatchError(
            "the path is empty, so it names no place and the patch would go to "
            "the whole of what it is sent to. Leave path out to send it there; "
            f"else give {_PATH_FORM}",
            Path(()),
            "address",
        )
    return read


def _segment_item_classes(
    model: type[VcoModel], walked: Path
) -> tuple[type[VcoModel], ...]:
    """Every variant of the item type of *model*'s ``segments`` field.

    A class with no ``segments`` field, or one whose ``segments`` is not a list
    of models, raises ``PatchError`` at *walked*.
    """
    field = model.model_fields.get("segments")
    if field is None:
        raise PatchError(f"{model.__name__} has no segments field", walked, "address")
    item_type = list_item_type(field)
    if item_type is None:
        raise PatchError(
            f"{model.__name__}.segments is not a list of models", walked, "address"
        )
    return item_type.variants


@dataclass(frozen=True, slots=True)
class _AddressPlace:
    """Where the walk of an address stands: what its next part is a part of."""

    holds: Literal["model", "items", "either", "untyped", "value"]
    """What is there.

    ``"model"`` is a model, and the next part names a field of it. ``"items"``
    is a list or a map of models, and the next part names an item of it.
    ``"either"`` is a field that takes one model or a list of them: the next
    part names a field of the model or an item of the list.
    ``"untyped"`` is a list of untyped items and ``"value"`` a plain value: no
    part can follow either.
    """
    classes: tuple[type[VcoModel], ...]
    """The classes the model there can be; empty where no model is."""
    items: tuple[type[VcoModel], ...]
    """The classes an item of the list or map there can be.

    Empty where no list or map is, and where its items are not models.
    """
    name: str | None
    """What is there, as a refusal names it: ``Mid.leaf``, ``an item of Seg.rules``.

    None at the start of an address that names no segment.
    """


def _address_names(
    place: _AddressPlace, parts: Sequence[PathPart], walked: Path
) -> tuple[list[str], Path]:
    """The patch key of each of *parts*, and the path they end at.

    The parts are walked from *place*, which is at *walked*. A field part
    gives the key of its field (``_part_name``), and what that field holds is
    where the next part is read (``_field_place``); the part after a list or a
    map gives the key of one of its items. The path is *walked* with each
    part after it, a field by its key.

    A part that names nothing where it stands raises ``PatchError``
    (``_address_step``).
    """
    keys: list[str] = []
    for part in parts:
        key, place, walked = _address_step(place, part, walked)
        keys.append(key)
    return keys, walked


def _address_step(
    place: _AddressPlace, part: PathPart, walked: Path
) -> tuple[str, _AddressPlace, Path]:
    """The patch key of *part* at *place*, the place below it and its path.

    *walked* is the path of *place*. After a list or a map, any part is the
    key of an item (``patch_key``): an item part as its selector, a name as
    written. At a model, a name is a field. After a field that takes one
    model or a list of them, a name that is a field of the model is that
    field, and any other part is an item of the list
    (``_names_an_item``). Everything else raises ``PatchError`` of kind
    ``address``: a name after a value that holds no model at *walked*, since
    it is that value the path cannot go below, and a part that names nothing
    at the path of the part.
    """
    where = walked.child(part)
    if _names_an_item(place, part):
        below = _AddressPlace(
            "model" if place.items else "value",
            place.items,
            (),
            f"an item of {place.name}",
        )
        return patch_key(part), below, where
    if place.holds == "value" and isinstance(part, str):
        raise PatchError(
            f"{place.name} does not hold a single sub-model, so the path cannot "
            "continue past it",
            walked,
            "address",
        )
    if place.holds not in ("model", "either") or not isinstance(part, str):
        raise PatchError(_names_nothing(place, part), where, "address")
    key = _part_name(place.classes, part, walked)
    return key, _field_place(place.classes, part), walked.child(key)


def _names_an_item(place: _AddressPlace, part: PathPart) -> bool:
    """Whether *part* names an item of a list or a map at *place*.

    After a list or a map, every part does. After a field that takes one
    model or a list of them, an item part does, and so does a name the one
    model has no field for: it is a selector written as text, and
    ``merge_patch``, which holds the value, refuses it where the value is the
    one model. A name the one model has a field for is that field; to name an
    item by such a text, write it in brackets.
    """
    if place.holds == "items":
        return True
    if place.holds != "either":
        return False
    return not isinstance(part, str) or not _fields_named(place.classes, part)


def _names_nothing(place: _AddressPlace, part: PathPart) -> str:
    """Why *part* names nothing at *place*, and what to write for it."""
    shown = Path((part,))
    if place.holds == "untyped":
        return (
            f"{place.name} is a list of untyped items, which a patch replaces "
            f"whole, so {shown} names nothing there. End the path at the list "
            "and give a list value"
        )
    if place.name is None:
        return (
            f"a path starts with a field name, so {shown} names nothing there. "
            "Start with the field, and put the item after the list it is in"
        )
    advice = "Give a field name after it" if place.classes else "End the path at it"
    return (
        f"{place.name} is not a list or a map, so {shown} names nothing there. "
        f"{advice}, or put the item after the list it is in"
    )


def _fields_named(
    classes: tuple[type[VcoModel], ...], part: str
) -> list[tuple[type[VcoModel], str]]:
    """Each of *classes* that has a field for *part*, with the field's python name.

    *part* is any key a patch accepts for the field (``python_name``).
    """
    return [
        (cls, name) for cls in classes if (name := python_name(cls, part)) is not None
    ]


def _part_name(classes: tuple[type[VcoModel], ...], part: str, walked: Path) -> str:
    """The patch key of the field that *part* names in *classes*.

    Its python name, where every one of *classes* that has a field for *part*
    gives it that name. Where they give it different names (two variants of a
    union read one key into a field of their own each), *part* as written:
    ``merge_patch``, which holds the value as read, then takes the name of
    the variant it is.

    A part that names no field of any of *classes* raises ``PatchError`` at
    ``walked.child(part)``: the message names the classes (joined with
    `` | ``) and lists the fields of all of them, each once.
    """
    names: dict[str, str] = {}
    for cls in classes:
        for key, name in cls._alias_map().items():  # pyright: ignore[reportPrivateUsage]
            _ = names.setdefault(key, name)
    if len({name for _, name in _fields_named(classes, part)}) > 1:
        return part
    try:
        return _field_name(names, _variant_names(classes), part, walked)
    except PatchError as exc:
        fields = ", ".join(
            dict.fromkeys(f for cls in classes for f in cls.model_fields)
        )
        raise PatchError(
            f"{exc.message}; its fields are {fields}", walked.child(part), "address"
        ) from exc


def _field_place(classes: tuple[type[VcoModel], ...], part: str) -> _AddressPlace:
    """What the field that *part* names in *classes* holds.

    The classes without a field for *part* drop out; at least one has it. The
    first that has it says what kind of field it is and names it in a
    refusal. A list of untyped items is ``"untyped"``. A list or a map is
    ``"items"``, with the classes of its items in every class that remains;
    one that can hold one model in place of the list is ``"either"``, with the
    classes of that model too. Any other field is the ``"model"`` it can hold
    in those classes, or a ``"value"`` when it holds none.
    """
    having = _fields_named(classes, part)
    first, name = having[0]
    holder = f"{first.__name__}.{name}"
    kind = list_kind(first.model_fields[name])
    if kind == "untyped":
        return _AddressPlace("untyped", (), (), holder)
    models = _sub_model_classes(having, items=False)
    if kind is None:
        return _AddressPlace("model" if models else "value", models, (), holder)
    items = _sub_model_classes(having, items=True)
    return _AddressPlace("either" if models else "items", models, items, holder)


def _sub_model_classes(
    having: Sequence[tuple[type[VcoModel], str]], *, items: bool
) -> tuple[type[VcoModel], ...]:
    """Every model the fields *having* can hold, in order, each once.

    *having* is each class with the name of its field. With *items* the
    models are those of the items of the list, or of the values of the map,
    each field can hold; without, those the field holds as it is, not in a
    list or a map. Empty when they hold none.
    """
    return tuple(
        dict.fromkeys(
            sub
            for cls, name in having
            for sub in _held_models(cls.model_fields[name], items=items)
        )
    )


def _held_models(field_info: FieldInfo, *, items: bool) -> tuple[type[VcoModel], ...]:
    """The models *field_info* holds as it is, or with *items* in its list or map."""
    if not items:
        return _field_model_types(field_info)
    if list_kind(field_info) == "map":
        return field_models(field_info)
    item_type = list_item_type(field_info)
    return () if item_type is None else item_type.variants


# ---------------------------------------------------------------------------
# merge_patch internals
# ---------------------------------------------------------------------------


def _merge_model(
    model: VcoModel,
    patch: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    *,
    built: bool = False,
) -> None:
    """Merge *patch* into *model*, in place. *path* is where *model* is in the patch.

    Every key must name a field of the model, and no two keys one field; a
    directive (a key that starts with ``_``) is not one. Each raises
    ``PatchError`` (``_patch_fields``). *built* is true for a model the patch
    builds, here from its defaults, and holds for the sub-models in it: it
    decides what ``null`` means (``_null_value``).

    The mask of a secret keeps what *model* holds (``_keeps_secret``): the
    field is left out of the merge, as if the patch had no key for it.
    """
    model_cls = type(model)
    fields = model_cls.model_fields
    names = model_cls._alias_map()  # pyright: ignore[reportPrivateUsage]

    # (python_name, value) for each patch key, but a mask that keeps a secret.
    updates = [
        (name, value)
        for _, name, value in _patch_fields(names, model_cls.__name__, patch, path)
        if not _keeps_secret(model, name, value, path.child(name), built=built)
    ]
    # Copy the touched fields first so OverrideFlag logic can see what changed.
    before: JsonObject = {
        name: copy.deepcopy(_field_value(model, name)) for name, _ in updates
    }
    _localize_groups(model, updates)

    for name, value in updates:
        _merge_field(
            model, name, fields[name], value, resolvers, path.child(name), built=built
        )

    _auto_set_override_flags(model, before)


_MASK: Final = (
    'the mask "***" in a patch keeps the stored secret, and {why}. The mask would '
    "be written to the VCO as the secret. Give the secret in full, or {instead}."
)
"""What refuses the mask of a secret where nothing is stored to keep."""

_MASK_UNSET: Final = ("this {owner} holds no {name}", "leave {name} out of the patch")
_MASK_BUILT: Final = (
    (
        "this {owner} is one the patch builds (an add, an item of a list value, or "
        "an object where none was stored), so nothing is stored for it"
    ),
    (
        "leave {name} out to build it without one; to keep the secret of an item "
        "that is stored, change that item under its selector in place of a list value"
    ),
)
"""Why a mask has nothing to keep, and what to write instead of it."""


def _is_mask(field_info: FieldInfo, value: object) -> bool:
    """Whether *value* is the mask of a secret: ``HIDDEN`` whole, on a ``Secret`` field.

    The same text in a field that is no secret, or as an item of a secret
    that is a list, is a value like any other.
    """
    return isinstance(value, str) and value == HIDDEN and is_secret(field_info)


def _masked_secret(
    texts: tuple[str, str], owner: str, name: str, path: Path
) -> PatchError:
    """The refusal of a mask at *path*, the field *name* of *owner*, as *texts* say."""
    why, instead = (text.format(owner=owner, name=name) for text in texts)
    return PatchError(_MASK.format(why=why, instead=instead), path, "masked_secret")


def _keeps_secret(
    model: VcoModel, name: str, value: object, path: Path, *, built: bool
) -> bool:
    """Whether *value*, given for field *name* of *model*, is a mask that is kept.

    Everything pyvelo shows has the mask in place of a secret, in a patch's
    own keys, so a patch copied from what was shown gives the mask back. It
    means there what it means in a named document (``restore_secrets``): keep
    the stored value. The patch plans no change for the field.

    ``PatchError`` of kind ``masked_secret`` at *path* where nothing is stored
    to keep: the field is unset, or *model* is one the patch *built*. Merged
    as a value, the mask would be written to the VCO as the secret.
    """
    if not _is_mask(type(model).model_fields[name], value):
        return False
    owner = type(model).__name__
    if built:
        raise _masked_secret(_MASK_BUILT, owner, name, path)
    if unwrap(_field_value(model, name)) is MISSING:
        raise _masked_secret(_MASK_UNSET, owner, name, path)
    return True


def _localize_groups(model: VcoModel, updates: Sequence[tuple[str, object]]) -> None:
    """Make local each coupled group the patch gives values, before any is merged.

    A group of *model*, an edge layer, that is not local, and of which *updates* (python
    name, value) gives some member a value and no member ``null``, is localized with
    those members as ``given``: a member with no value of its own from the profile or
    its default is the patch's to set (``GroupHandle.localize``), so a patch that gives
    every member a value makes such a group local. A ``null`` for a member inherits the
    whole group (``_merge_owned``), so a group given one is left to that.
    """
    if not isinstance(model, Layered):
        return
    for group in groups_of(type(model)).values():
        members = group.members(type(model))
        given = {name: value for name, value in updates if name in members}
        if not given or any(value is None for value in given.values()):
            continue
        if not isinstance(_field_value(model, group.anchor), Owned):
            continue
        # The anchor holds an ``Owned`` on the edge layer only.
        handle = group.handle(cast("Layered[Edge]", model))
        if not handle.is_local:
            handle.localize(given=given)


_DIRECTIVES = ("_op", "_position")


def _field_name(names: Mapping[str, str], owner: str, key: object, path: Path) -> str:
    """The python name of the field that the patch key *key* names.

    *names* maps each accepted key to a python name (``_field_names_by_key``).
    *owner* is the name of the model class, for the message. A directive or a
    key that names no field raises ``PatchError`` at ``path.child(key)``. Both
    ``_merge_model`` and ``_build_input`` check keys here (``_patch_fields``),
    so their messages are the same.
    """
    text = key if isinstance(key, str) else str(key)
    if isinstance(key, str) and key in names:
        return names[key]
    where = path.child(text)
    if text in _DIRECTIVES:
        raise PatchError(
            f"{text} is a directive for a list item only", where, "operation"
        )
    if text.startswith("_"):
        raise PatchError(
            f"unknown directive {text!r}: the only directives are "
            f"{' and '.join(_DIRECTIVES)}, on a list item",
            where,
            "operation",
        )
    message = f"{owner} has no field {text!r}"
    close = list(
        dict.fromkeys(names[k] for k in difflib.get_close_matches(text, names))
    )
    if close:
        message += f"; did you mean {', '.join(close)}?"
    raise PatchError(message, where, "unknown_field")


def _patch_fields(
    names: Mapping[str, str], owner: str, patch: Mapping[object, object], path: Path
) -> list[tuple[object, str, object]]:
    """``(key, python name, value)`` for each key of *patch*.

    Each key is checked by ``_field_name``.

    Two keys that name one field (``rule_id`` and ``ruleId``) raise
    ``PatchError`` at the field's path, naming both.
    """
    fields: list[tuple[object, str, object]] = []
    seen: dict[str, object] = {}
    for key, value in patch.items():
        name = _field_name(names, owner, key, path)
        if name in seen:
            raise PatchError(
                f"{seen[name]!r} and {key!r} both name the field {name}: give it once",
                path.child(name),
                "duplicate",
            )
        seen[name] = key
        fields.append((key, name, value))
    return fields


def _merge_field(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    model: VcoModel,
    name: str,
    field_info: FieldInfo,
    value: object,
    resolvers: IdentityResolver | None,
    path: Path,
    *,
    built: bool = False,
) -> None:
    """Merge one patch value into ``model.<name>``. See ``VcoModel.merge_patch``.

    *path* is where the value is in the patch. Every value assigned is first
    validated (``_validate_field``). *built* is true when *model* is one the
    patch builds (``_merge_model``).
    """
    model_cls = type(model)
    _refuse_profile_only(model, name, field_info, path)

    # An edge layer's Inheritable: the patch changes who owns the value.
    held = _field_value(model, name)
    if isinstance(held, Owned):
        _merge_owned(model, name, field_info, held, value, resolvers, path, built=built)
        return

    # null: what the field takes it as (``_null_value``).
    if value is None:
        value = _null_value(field_info, name, path, built=built)
        setattr(model, name, _validate_field(model_cls, name, value, path))
        return

    # A list, or a map of models: a list value, or item operations. An object
    # on a field that also takes one sub-model is that sub-model, unless the
    # field holds a list.
    spec = _list_spec(field_info)
    if spec is not None and not (
        is_dict(value)
        and _field_model_types(field_info)
        and not isinstance(_field_value(model, name), list)
    ):
        _merge_list(model, name, field_info, spec, value, resolvers, path)
        return

    if is_dict(value):
        current = _field_value(model, name)

        # Sub-model that already exists: merge into it.
        if isinstance(current, VcoModel):
            _merge_model(current, value, resolvers, path, built=built)
            _check_merged(
                current,
                inner_adapter(model_cls, name).validate_python
                if len(_field_model_types(field_info)) > 1
                else type(current).model_validate,
                path,
            )
            if field_kind(field_info).kind is Kind.INHERITABLE:
                # The profile stores what a patch gave, a value equal to the
                # display default too, as an assignment would.
                model.model_fields_set.add(name)
            return

        # Sub-model that is unset: build a new one from the patch.
        model_types = _field_model_types(field_info)
        if model_types and (current is MISSING or current is None):
            if len(model_types) == 1:
                new = _new_model_from_patch(model_types[0], value, resolvers, path)
            else:
                new = _new_variant_from_patch(model_cls, name, value, resolvers, path)
            setattr(model, name, _validate_field(model_cls, name, new, path))
            return

    # Resolvable: a name given where the field expects an ID.
    if isinstance(value, str) and resolvers is not None:
        value = _resolve_name(field_info, value, resolvers, path)

    setattr(model, name, _validate_field(model_cls, name, value, path))


def _refuse_profile_only(
    model: VcoModel, name: str, field_info: FieldInfo, path: Path
) -> None:
    """``PatchError`` if the patch names a ``ProfileOnly`` field of an edge-layer model.

    The edge layer holds none of the field's value, and what a save of the
    edge sent for it would be added to the profile's by the VCO instead of
    replacing it: a patch of the field belongs to the profile's state.
    """
    if (
        isinstance(model, Layered)
        and model.layer() is Edge
        and field_kind(field_info).kind is Kind.PROFILE_ONLY
    ):
        raise PatchError(
            f"{name!r} belongs to the profile: the edge layer holds none, and one "
            "stored there would be added to the profile's by the VCO, not "
            "replace it. Send this patch to the profile's state, and leave "
            f"{name!r} out of an edge's",
            path,
            "invalid_value",
        )


def _merge_owned(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    model: VcoModel,
    name: str,
    field_info: FieldInfo,
    owned: Owned[object],
    value: object,
    resolvers: IdentityResolver | None,
    path: Path,
    *,
    built: bool,
) -> None:
    """Merge one patch value into the ``Owned`` at ``model.<name>``.

    The edge layer holds the ``Owned`` there.

    ``null`` inherits (``Owned.inherit``). A value that is not an object sets
    the value local (``Owned.set_local``). An object is merged into the local
    value; when the field is not local, ``Owned.localize`` makes it so first
    and the object is merged into what that returns: a copy of the profile's
    value, or, on a ``presence_wins`` field where the edge stores an object with
    its flag off, that object, kept. So ``{"a": 7}`` on an inherited
    ``{"a": 4, "b": true}`` gives a local ``{"a": 7, "b": true}``.
    A member of a coupled group is never changed alone: ``null`` inherits the
    whole group, and any other value localizes the group first, when it is
    not local, and then applies as above.

    The flag of a ``ByFlag`` field is not patchable (``_refuse_flag_key``).
    """
    group = group_of(model, name)  # pyright: ignore[reportArgumentType]
    if value is None:
        if group is None:
            owned.inherit()
        else:
            group.handle(model).inherit()  # pyright: ignore[reportArgumentType]
        return
    if group is not None and not group.handle(model).is_local:  # pyright: ignore[reportArgumentType]
        group.handle(model).localize()  # pyright: ignore[reportArgumentType]

    model_cls = type(model)
    spec = _list_spec(field_info)
    model_types = _field_model_types(field_info)
    if spec is not None and not is_dict(value):
        _merge_list(model, name, field_info, spec, value, resolvers, path)
        return
    if is_dict(value) and (spec is not None or model_types):
        _refuse_flag_key(model, name, field_info, value, path)
        local = owned.localize()
        if spec is not None and not (model_types and not isinstance(local, list)):
            _merge_list(model, name, field_info, spec, value, resolvers, path)
            return
        if isinstance(local, VcoModel):
            _merge_model(local, value, resolvers, path, built=built)
            _check_merged(
                local,
                inner_adapter(model_cls, name).validate_python
                if len(model_types) > 1
                else type(local).model_validate,
                path,
            )
            return
        # The profile gives ``null`` here, which cannot be copied: build a value.
        if len(model_types) == 1:
            new = _new_model_from_patch(model_types[0], value, resolvers, path)
        else:
            new = _new_variant_from_patch(model_cls, name, value, resolvers, path)
        owned.set_local(_validate_field(model_cls, name, new, path))
        return

    if isinstance(value, str) and resolvers is not None:
        value = _resolve_name(field_info, value, resolvers, path)
    owned.set_local(_validate_field(model_cls, name, value, path))


def _refuse_flag_key(
    _model: VcoModel,
    name: str,
    field_info: FieldInfo,
    patch: Mapping[object, object],
    path: Path,
) -> None:
    """``PatchError`` if *patch*, an object for a ``ByFlag`` field, names its flag.

    The flag is the one the encoding is made of.

    The flag says whether the edge overrides the profile. A patch that set it
    to false would leave a local value stored but not local, and one that set
    it to true would make the profile's value the edge's own without a copy:
    ownership changes by ``null`` and by a value only. *path* is the field's
    place in the patch, and the message gives the patch that inherits it,
    with the field by its attribute name *name*, as *path* has the fields
    above it. The model the field is on is not read: its caller passes it.
    """
    encoding = field_kind(field_info).encoding
    if not isinstance(encoding, ByFlag):
        return
    for model_type in _field_model_types(field_info):
        names = model_type._alias_map()  # pyright: ignore[reportPrivateUsage]
        for key in patch:
            if isinstance(key, str) and names.get(key) == encoding.flag:
                inherit = json.dumps(_inherit_patch(name, path))
                raise PatchError(
                    f"{key!r} is the flag that says whether the edge overrides the "
                    "profile here, and it follows the value, so a patch cannot "
                    "set it (it would leave a local value that is not local, "
                    "or make the profile's value the edge's without a copy). "
                    f"Leave {key!r} out: an object given overrides it "
                    "(merged into the edge's value, or into a copy of the "
                    "profile's when the edge stores none; set_local() stores "
                    "the whole object it is given instead) and null inherits it "
                    f"(inherit()); to inherit, send {inherit}",
                    path.child(encoding.flag),
                    "invalid_value",
                )


def _inherit_patch(field_key: str, path: Path) -> dict[str, object]:
    """The patch that sends ``null`` to the field at *path*.

    The field's own key is *field_key*, and the patch nests as the parts of *path* do.

    A keyed item is named by its selector and a list position by ``at``.
    """
    patch: dict[str, object] = {field_key: None}
    for part in reversed(path.parts[:-1]):
        if isinstance(part, Key):
            name = key_selector(part.value)
        elif isinstance(part, int):
            name = at(part)
        else:
            name = part
        patch = {name: patch}
    return patch


def _null_value(field_info: FieldInfo, name: str, path: Path, *, built: bool) -> object:
    """What ``null`` means for field *name*.

    It is ``MISSING``, ``None``, or ``PatchError`` at *path*.

    A field whose type takes ``MISSING`` is unset by ``null``, one that takes
    only ``None`` is set to it, and one that takes neither refuses it; when
    such a field holds ``Literal[""]``, its value for none, the refusal says to
    give ``""``. A field that takes both is unset in a change, where leaving
    the key out would change nothing; a ``RefOnly`` field is the exception, as
    ``MISSING`` there says the module was read without refs and ``None`` says
    there is no row, so a clear is ``None``. In an item or sub-model the patch
    *built* it is ``None``: leaving the key out already leaves the field unset,
    and the JSON dump of an item writes ``null`` for ``None``, so a dump builds
    the same item.
    """
    takes_missing = _field_accepts_missing(field_info)
    takes_none = _field_accepts_none(field_info)
    if takes_none and (
        built or not takes_missing or get_ref_only(field_info) is not None
    ):
        return None
    if takes_missing:
        return MISSING
    if _holds_empty_text(field_info):
        raise PatchError(
            f'null is not a value of {name}: it holds "" when there is none, which '
            'is what the VCO writes. To clear it, give "" in place of null',
            path,
            "null",
        )
    reason, remedy = (
        ("the field cannot be unset", "leave the key out")
        if built
        else ("a change cannot unset it", "give a value, or leave the key out")
    )
    raise PatchError(
        f"null is not a value of {name}, and {reason}: {remedy}", path, "null"
    )


def _validate_field(
    model_cls: type[VcoModel], name: str, value: object, path: Path
) -> object:
    """*value* validated for field *name* of *model_cls*, with the patch context.

    See ``validate_patched_value``.
    """
    return validate_patched_value(
        inner_adapter(model_cls, name),
        value,
        field_models(model_cls.model_fields[name]),
        path,
    )


def validate_patched_value(
    adapter: TypeAdapter[object],
    value: object,
    models: Sequence[type[VcoModel]],
    path: Path,
) -> object:
    """*value* validated by *adapter* the way a patched value is.

    This is the one place that does it.

    The patch context (``{"patch": True}``) stops ``DefaultingScalar`` and
    ``DefaultingModel`` from turning a bad value into the default. A value that
    fails raises ``PatchError`` at *path* (a path in python names), from the
    ``ValidationError``, which is its cause without the input; the places in its
    message are in python names of the classes in *models*, the ones the value
    is made of. A boolean given for a value that is not one raises as well
    (``_refuse_bool_as_number``). ``Owned.set_local`` validates through this.
    """
    validated = _validated(adapter.validate_python, value, models, path)
    _refuse_bool_as_number(value, validated, path)
    return validated


def _refuse_bool_as_number(given: object, stored: object, path: Path) -> None:
    """Raise ``PatchError`` at *path* for a boolean *given* where *stored* is none.

    *stored* is what validation made of *given*. Pydantic reads ``True`` as
    the integer 1, so ``{"vlan_id": true}`` would otherwise be VLAN 1. A
    boolean is taken only where the field holds one.
    """
    # Only *given* itself is compared: a boolean nested in a ``dict[str, int]``
    # value or a list of lists is not (no configuration model has such a field).
    if isinstance(given, bool) and not isinstance(stored, bool):
        word = "true" if given else "false"
        raise PatchError(
            f"{word} is a boolean, which this value does not take: "
            f"it would be read as {stored!r}; send a number",
            path,
            "invalid_value",
        )


def python_place(loc: Sequence[int | str], models: Sequence[type[VcoModel]]) -> Path:
    """Where in the value an error is, in python names.

    *loc* is the ``loc`` of one error of a ``ValidationError``, and *models*
    are the classes the validated value is made of.

    A part that names a field of the model classes in hand, by any name
    ``merge_patch`` accepts, becomes its python name, and the walk goes on
    in the classes that field's value is made of. Any other part is kept
    as it is: a list position, a map key, the tag or the name of a union
    member. The part after a map field is the key, and the part after a
    union (or after the position in a list of a union) is its tag, even
    when that text is also the name of a field below: it is kept, and the
    walk goes on in the value's classes.
    """
    parts: list[PathPart] = []
    current = tuple(models)
    keep = 0  # the parts that follow a field and are not fields
    for part in loc:
        if keep:
            keep -= 1
            parts.append(part)
            continue
        if isinstance(part, str) and current:
            found = _field_named(current, part)
            if found is not None:
                owner, name = found
                parts.append(name)
                current = field_models(owner.model_fields[name])
                keep = _parts_before_fields(owner.model_fields[name])
                continue
        parts.append(part)
    return Path(parts)


def _field_named(
    models: Sequence[type[VcoModel]], key: str
) -> tuple[type[VcoModel], str] | None:
    """The first of *models* with a field *key* names, and that field's python name."""
    for model in models:
        if (name := python_name(model, key)) is not None:
            return model, name
    return None


def _parts_before_fields(field_info: FieldInfo) -> int:
    """How many parts of an error's location follow *field_info*'s name.

    They come before the fields of its models.

    A map gives its key; a list gives the position of the item (and, for a
    union of models, the tag of the member); a union of models gives the tag.
    """
    spec = _list_spec(field_info)
    if spec is not None:
        if spec.kind == "map":
            return 1
        if spec.item_type is not None:
            return 1 + (len(spec.item_type.variants) > 1)
        return 0
    return int(len(_field_model_types(field_info)) > 1)


def validation_lines(exc: ValidationError, models: Sequence[type[VcoModel]]) -> str:
    """What a ``ValidationError`` says, one ``place: message`` per error.

    The errors are joined by ``"; "``.

    The place is in python names (``python_place``), where *models* are the
    classes the validated value is made of; it is left out for an error of
    the value itself.
    """
    return placed_lines(
        (python_place(error["loc"], models), error["msg"])
        for error in exc.errors(include_url=False)
    )


def placed_lines(errors: Iterable[tuple[Path, str]]) -> str:
    """One ``place: message`` for each of *errors*, a place with what is wrong there.

    The lines are joined by ``"; "``. An empty place, that of the value
    itself, is left out of its line.
    """
    return "; ".join(f"{place}: {text}" if place else text for place, text in errors)


def _without_input(exc: ValidationError) -> ValidationError:
    """*exc* as an error whose text quotes no input value.

    ``errors()`` still holds them.

    Pydantic reads ``hide_input_in_errors`` from the outermost validator, which
    for a field typed as one model is a validator without the model's config. An
    error chained under a ``PatchError`` is rebuilt here, so no place that
    validates a patch value has to get that right.
    """
    details: list[InitErrorDetails] = []
    for error in exc.errors(include_url=False):
        detail = InitErrorDetails(
            type=error["type"], loc=error["loc"], input=error["input"]
        )
        if "ctx" in error:
            detail["ctx"] = error["ctx"]
        try:
            _ = ValidationError.from_exception_data("", [detail])
        except KeyError:  # an error type of a validator of ours, not pydantic's
            detail["type"] = PydanticCustomError(
                cast("LiteralString", error["type"]),
                cast("LiteralString", error["msg"]),
            )
        details.append(detail)
    return ValidationError.from_exception_data(exc.title, details, hide_input=True)


def _validated[T](
    validate: Callable[..., T],
    value: object,
    models: Sequence[type[VcoModel]],
    path: Path,
) -> T:
    """*value* as ``validate(value, context={"patch": True})`` gives it.

    ``PatchError`` at *path* if it is refused.

    The error's cause is the ``ValidationError`` without its input, and the
    error is raised outside the ``except`` block: raised inside, it would
    also hold the original as its ``__context__``, and that text quotes the
    patch value.
    """
    try:
        return validate(value, context={"patch": True})
    except ValidationError as exc:
        refusal = PatchError(
            "invalid value: " + validation_lines(exc, models), path, "invalid_value"
        )
        refusal.__cause__ = _without_input(exc)
    raise refusal


def _check_merged(model: VcoModel, validate: Callable[..., object], path: Path) -> None:
    """Check *model*, merged into and complete, as the parser would.

    ``PatchError`` at *path* if it does not pass.

    *validate* is the validator of the place that holds *model*: its own
    class, or the union of models that the place is typed as, so that the
    discriminator runs. It is given a shallow copy, so a model validator that
    rewrites values does not change what the patch wrote, and the patch
    context, so that a ``DefaultingModel`` field does not swallow the failure.
    A validator that raises anything but ``ValidationError`` (one that takes
    its input to be a dict, say) raises ``PatchError`` as well.

    The copy is shallow: a validator that assigns an attribute does not change
    *model*, but one that mutates a list in place would (no validator of the
    models does).
    """
    name = type(model).__name__
    try:
        _ = validate(model.model_copy(), context={"patch": True})
    except ValidationError as exc:
        refusal = PatchError(
            f"{name} is not valid after this change: "
            f"{validation_lines(exc, (type(model),))}",
            path,
            "invalid_value",
        )
        refusal.__cause__ = _without_input(exc)
    except Exception as exc:
        raise PatchError(
            f"a validator of {name} failed on the changed model with "
            f"{type(exc).__name__}: {exc}; the model validators of {name} "
            "must accept a model instance, as well as the dict a response "
            "gives them",
            path,
            "invalid_value",
        ) from exc
    else:
        return
    raise refusal


def _new_model_from_patch(
    model_type: type[VcoModel],
    patch: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
) -> VcoModel:
    """Build a sub-model from a patch.

    Prefers default-construct-then-merge, so KeyField and Resolvable handling
    applies to the nested patch, and OverrideFlag fields are set. Models with
    required fields can't be built from defaults, so those are validated whole
    from ``_build_input``.
    """
    try:
        instance = model_type()
    except ValidationError:
        data = _build_input(
            model_type.model_fields, patch, resolvers, path, owner=model_type.__name__
        )
        built = _validated(model_type.model_validate, data, (model_type,), path)
        _refuse_unread_keys(built, data, path)
        return built
    _merge_model(instance, patch, resolvers, path, built=True)
    _check_merged(instance, model_type.model_validate, path)
    return instance


def _new_variant_from_patch(
    model_cls: type[VcoModel],
    name: str,
    patch: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
) -> object:
    """Build the value of field *name*, a union of models, from a patch.

    The field's validator (its discriminator) picks the variant. A key that
    only another variant has raises, as it would on an item of that variant.
    """
    variants = _field_model_types(model_cls.model_fields[name])
    data = _build_input(
        _merged_fields(variants), patch, resolvers, path, owner=_variant_names(variants)
    )
    built = _validate_field(model_cls, name, data, path)
    if isinstance(built, VcoModel):
        _refuse_unread_keys(built, data, path)
    return built


def _refuse_unread_keys(
    model: VcoModel,
    data: Mapping[str, object] | Mapping[object, object],
    path: Path,
) -> None:
    """Raise ``PatchError`` for a key of *data* that *model* has no field for.

    *model* is validated from *data*.

    ``_build_input`` checks a key against every variant of a union, and
    pydantic then drops the keys that only another variant has. This walks
    *data* beside the model built from it, into sub-models and list items, and
    refuses those keys as ``_merge_model`` would on an existing item. A list
    value on the way is checked as ``_merge_list`` checks one
    (``_check_list_value``): a keyed list must not repeat a key.
    """
    model_cls = type(model)
    names = model_cls._alias_map()  # pyright: ignore[reportPrivateUsage]
    for key, value in data.items():
        name = _field_name(names, model_cls.__name__, key, path)
        child = path.child(name)
        current = unwrap(_field_value(model, name))
        _refuse_bool_as_number(value, current, child)
        if is_dict(value) and isinstance(current, VcoModel):
            _refuse_unread_keys(current, value, child)
        elif (
            is_list(value)
            and is_list(current)
            and (spec := _list_spec(model_cls.model_fields[name])) is not None
        ):
            _check_list_value(spec, value, current, child)


def _resolve_name(
    field_info: FieldInfo, name: str, resolvers: IdentityResolver, path: Path
) -> object:
    """Turn the string *name* on a Resolvable field into the field's ID.

    Returns *name* unchanged if the field is not Resolvable. Otherwise, in order:

    1. If *name* is ``""`` and the field holds ``Literal[""]``, ``""`` is the
       field's value for no identity, and is returned with no lookup.
    2. If the field's ID type is not ``str`` and *name* parses as it (a UUID
       string on a UUID field), that value is the ID.
    3. An exact or case-insensitive name match gives what the field's accessor
       reads off the identity.
    4. If the ID type is ``str`` and *name* equals a known ID, *name* is the ID.
    5. Otherwise the ``ResolutionError`` propagates, its message after *path*;
       for a provider field it names the type of service the field takes and
       the services of it (``_of_wanted_type``). A ``ResolverNotLoadedError``
       propagates as it was raised.

    With no resolver registered for the identity type, *name* is returned when
    the ID type is ``str`` or cannot be told (any text may be the ID, and
    validation decides). Otherwise *name* is not an ID and nothing can look it
    up, so ``PatchError`` at *path* says to register the resolver or give the ID.
    """
    resolvable = get_resolvable(field_info)
    if resolvable is None or (name == "" and _holds_empty_text(field_info)):
        return name
    id_type = _id_type(field_info)
    if id_type is not None and id_type is not str:
        try:
            return _id_adapter(id_type).validate_strings(name)
        except ValidationError:
            pass
    try:
        identity = resolvers.lookup(resolvable.identity_type, name)
    except ResolverNotLoadedError:
        raise
    except ResolutionError as error:
        if id_type is str and _is_known_id(
            resolvers,
            resolvable.identity_type,
            resolvable.accessor,
            name,
        ):
            return name
        refused = _of_wanted_type(
            error, field_info, resolvers, resolvable.identity_type
        )
        refused.locate(path)
        if refused is not error:
            raise refused from None
        raise
    except LookupError as err:
        if id_type is None or id_type is str:
            return name
        raise PatchError(
            f"{name!r} is not an ID of this field, and no "
            f"{resolvable.identity_type.__name__} resolver is registered to look "
            "it up as a name: give the ID, or register that resolver",
            path,
            "invalid_value",
        ) from err
    return resolvable.accessor(identity)


def _of_wanted_type(
    error: ResolutionError,
    field_info: FieldInfo,
    resolvers: IdentityResolver,
    identity_type: type[object],
) -> ResolutionError:
    """*error*, or the error that names the type of service the field takes.

    A field that takes one type of service (``service_type_of``) is refused with that
    type and the loaded services of it. An error for a name that a service of that type
    has (several share it) is kept, with ``wanted`` set.
    """
    wanted = _service_type(field_info)
    if wanted is None:
        return error
    fitting = sorted(
        {
            name
            for identity in resolvers.identities(identity_type)
            if getattr(identity, "type_", None) == wanted
            and isinstance(name := getattr(identity, "name", None), str)
        }
    )
    if error.name.lower() in {name.lower() for name in fitting}:
        error.wanted = wanted
        return error
    return resolution_error(identity_type, error.name, fitting, wanted=wanted)


def _is_known_id[T](
    resolvers: IdentityResolver,
    identity_type: type[T],
    accessor: Callable[[T], object],
    value: object,
) -> bool:
    """True if a loaded identity of *identity_type* matches *value*.

    It matches when ``accessor(identity) == value``.
    """
    try:
        _ = resolvers.lookup_by(identity_type, accessor, value)
    except ResolutionError:
        return False
    return True


def _key_name_of(item_type: ListItemType | None, key_path: str) -> type[object] | None:
    """The identity type the field at *key_path* of the list's items names, or None.

    The field names it with ``NameOf``.
    """
    if item_type is None:
        return None
    fields: Mapping[str, FieldInfo] = item_type.fields
    *parents, leaf = key_path.split(".")
    for part in parents:
        parent_info = fields.get(part)
        sub_type = None if parent_info is None else _field_model_type(parent_info)
        if sub_type is None:
            return None
        fields = sub_type.model_fields
    leaf_info = fields.get(leaf)
    identity_type: type[object] | None = (
        None if leaf_info is None else get_name_of(leaf_info)
    )
    return identity_type


def _canonical_name(
    identity_type: type[object], name: str, resolvers: IdentityResolver
) -> str:
    """The name the resolver has for *name*, or *name* if it cannot say.

    An unknown name raises ``ResolutionError``. No resolver for the type, or an
    identity with no ``name``, leaves *name* as it is.
    """
    try:
        identity = resolvers.lookup(identity_type, name)
    except ResolutionError:
        raise
    except LookupError:
        return name
    canonical: object = getattr(identity, "name", None)
    return canonical if isinstance(canonical, str) else name


_CHANGE_IT_IN_LIST = (
    '; to change it, write a plain object under its selector (no "_op")'
)
"""What a refusal of an add of an item already in the list says to do instead."""


def _require_ids_for_name(
    item: VcoModel,
    identity_type: type[object] | None,
    key_path: str,
    key: str,
    where: Path,
) -> None:
    """Refuse *item* if the IDs of its name were not set on it.

    *item* is named *key* and is at *where* in the patch.

    *identity_type* is what the field at *key_path* is the ``NameOf``. The IDs
    are the ``Resolvable`` fields for that type next to it. One that was neither
    in the patch nor filled from the name is still at its default.
    """
    if identity_type is None:
        return
    owner: object = item
    for part in key_path.split(".")[:-1]:
        owner = unwrap(_field_value(owner, part))
    if not isinstance(owner, VcoModel):
        return
    unset = [
        name
        for name, fi in type(owner).model_fields.items()
        if (resolvable := get_resolvable(fi)) is not None
        and resolvable.identity_type is identity_type
        and name not in owner.model_fields_set
    ]
    if unset:
        raise LookupError(
            f"Cannot add {key!r} by name: no loaded {identity_type.__name__} "
            "resolver or profile entry has it, and the patch does not give "
            f"{', '.join(unset)} (at {where}). Give "
            f"{'it' if len(unset) == 1 else 'them'} in the body, or register a "
            f"{identity_type.__name__} resolver (the resolvers given to the "
            "merge; state.resolvers of a ConfigState)"
        )


def _new_item(
    item_type: ListItemType,
    body: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    context: ConfigContext | None,
) -> VcoModel:
    """Build a new list item from the body of an ``add``, validated whole.

    *path* is where the item is in the patch; a body that does not build a
    valid item raises ``PatchError``. *context* is the config level of the
    model that holds the list: the item gets it, so context defaults resolve
    for the right level (EDGE vs PROFILE).
    """
    item_input = _build_input(
        item_type.fields, body, resolvers, path, owner=item_type.name
    )
    item = _validated(item_type.validate, item_input, item_type.variants, path)
    _refuse_unread_keys(item, item_input, path)
    if context is not None:
        _ = item.for_context(context)
        _fill_context_defaults(item)
    return item


def _new_keyed_item(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    spec: _ListSpec,
    item_type: ListItemType,
    body: Mapping[object, object],
    key: KeyValue,
    resolvers: IdentityResolver | None,
    path: Path,
    context: ConfigContext | None,
) -> VcoModel:
    """Build a new keyed-list item from the body of an ``add``, with *key* written in.

    Each part of *key* (one, or the tuple of a composite key) is written at
    its path as given, so ``24`` stays an ``int``, unless the body gives that
    part already, under any name of its field, in the same string form.
    ``_check_add`` has refused a body that gives another key. Raises
    ``LookupError`` (not ``ResolutionError``) if the key is a name
    (``NameOf``) and the IDs that go with it were neither in the patch nor
    found by a resolver: the item would carry the model's defaults, which are
    another item's IDs. See
    ``_new_item``.
    """
    parts = key if isinstance(key, tuple) else (key,)
    for key_path, part in zip(spec.key_paths, parts, strict=True):
        given = _patch_value(item_type.fields, body, key_path)
        if given is None or str(given) != str(part):
            body = _inject_key_path(body, key_path, part)
    item = _new_item(item_type, body, resolvers, path, context)
    if spec.name_identity is not None:
        _require_ids_for_name(
            item, spec.name_identity, spec.key_paths[0], str(key), path
        )
    return item


def _fill_context_defaults(item: VcoModel) -> None:
    """Fill MISSING fields that have a ContextDefault at the item's config level.

    A freshly validated item leaves ``SomeModel | MISSING`` fields as MISSING.
    Some levels need those sub-structures present (e.g. PROFILE segments need
    ospf, syslog, ...). We fill them only when the field's ContextDefaults
    gives a non-MISSING default for the active level. Otherwise MISSING stays,
    so patches at other levels don't pick up unwanted sub-structures.
    """
    level = item.config_context
    if level is None:
        return
    for name, field_info in type(item).model_fields.items():
        if unwrap(_field_value(item, name)) is not MISSING:
            continue
        default = _effective_default(field_info, level)
        if (
            default.source is _DefaultSource.CONTEXT
            and default.value is not MISSING
            and default.value is not PydanticUndefined
        ):
            setattr(item, name, default.value)


def _inject_key_path(
    patch: Mapping[object, object], key_path: str, key_value: KeyScalar
) -> dict[object, object]:
    """Return a copy of *patch* with *key_value* set at the dotted *key_path*.

    For ``"name"``, sets ``patch["name"]`` unless the patch already has it. For
    ``"segment.name"``, makes ``patch["segment"] = {**existing, "name": key_value}``,
    keeping sibling keys and overriding any existing name.
    """
    *parents, leaf = key_path.split(".")
    result = dict(patch)
    if not parents:
        _ = result.setdefault(leaf, key_value)
        return result

    node = result
    for part in parents:
        existing = node.get(part)
        child: dict[object, object] = dict(existing) if is_dict(existing) else {}
        node[part] = child
        node = child
    node[leaf] = key_value
    return result


def _input_keys(field_info: FieldInfo, field_name: str) -> tuple[str, ...]:
    """Patch keys that may hold this field.

    They are the python name, plus the string alias if there is one.
    """
    alias = field_info.validation_alias or field_info.alias
    if isinstance(alias, str) and alias != field_name:
        return (field_name, alias)
    return (field_name,)


def _build_input(
    fields: Mapping[str, FieldInfo],
    patch: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    *,
    owner: str,
) -> JsonObject:
    """Turn a patch that pydantic will validate whole into its input, checked.

    For what the patch builds instead of merging into (a new keyed-list item,
    a sub-model with a required field), so ``model_validate`` sees what
    ``merge_patch`` would have made of it. *fields* are the target model's
    fields, *owner* its class name (for a union, the variants' names joined
    with `` | ``), and *path* is where *patch* is in the patch. This function
    walks *patch* against *fields* and:

    - raises ``PatchError`` for a key that names no field, for a directive
      and for two keys that name one field (``_patch_fields``)
    - raises ``PatchError`` for the mask of a secret: what the patch builds
      has no stored secret to keep (``_keeps_secret`` says what the mask is)
    - turns a ``null`` into what the field takes it as (``_null_value``,
      with ``built=True``)
    - writes a key that pydantic does not read (the camelCase form of a field
      with another alias) as the python name
    - resolves string values on Resolvable fields the way ``merge_patch``
      does (see ``_resolve_name``): an unknown name raises ``ResolutionError``
      and a name with no resolver to look it up raises ``PatchError``,
      unless the text can be the ID
    - builds a list or map field given as an object from its item
      operations, on an empty list: each must be an ``add``
      (``_new_list_input``)
    - recurses into list items and sub-model dicts
    - fills Resolvable fields that are missing, but only from a sibling field
      marked ``NameOf`` for the same identity type that holds a string (a key
      ``segment.name`` written in by ``_inject_key_path`` gives
      ``segment_id`` / ``segment_logical_id``), or, when the item gives an ID
      of the identity and not the name, from the identity that ID finds, which
      also gives the name (``_fill_from_names``). An unknown name or ID raises
      ``ResolutionError`` and a missing resolver skips the fill.

    With no *resolvers*, names are left as they are.
    """
    names = _field_names_by_key(fields)
    built: JsonObject = {}

    for key, field_name, given in _patch_fields(names, owner, patch, path):
        fi = fields[field_name]
        child = path.child(field_name)
        if _is_mask(fi, given):
            raise _masked_secret(_MASK_BUILT, owner, field_name, child)
        value = given
        if value is None:
            value = _null_value(fi, field_name, child, built=True)
        elif isinstance(value, str):
            if resolvers is not None:
                value = _resolve_name(fi, value, resolvers, child)
        elif is_dict(value) and (sub_types := _field_model_types(fi)):
            value = _build_input(
                _merged_fields(sub_types),
                value,
                resolvers,
                child,
                owner=_variant_names(sub_types),
            )
        elif (is_list(value) or is_dict(value)) and (
            spec := _list_spec(fi)
        ) is not None:
            value = _new_list_input(spec, value, resolvers, child)
        input_key = key if key in _input_keys(fi, field_name) else field_name
        built[str(input_key)] = value  # a str: _field_name refuses other keys

    if resolvers is not None:
        _fill_from_names(fields, built, resolvers)
    return built


def _fill_from_names(  # noqa: C901 (existing: split it when it next changes)
    fields: Mapping[str, FieldInfo], resolved: JsonObject, resolvers: IdentityResolver
) -> None:
    """Fill, in *resolved*, what the identity a ``NameOf`` field names says.

    The identity is found by its name or by an ID of it.

    For each ``NameOf`` field: with its name given, the ``Resolvable`` fields
    of that identity type that *resolved* lacks are filled from the identity
    the name finds. With no name given, but an ID of the identity (another
    ``Resolvable`` field of the type), the identity that ID finds gives the
    name and the other fields *resolved* lacks. An unknown name or ID raises
    ``ResolutionError``; with no resolver for the type nothing is filled.
    """
    for field_name, fi in fields.items():
        identity_type: type[object] | None = get_name_of(fi)
        if identity_type is None:
            continue
        name_key = next(
            (key for key in _input_keys(fi, field_name) if key in resolved), None
        )
        name = None if name_key is None else resolved[name_key]
        if name_key is not None and not isinstance(name, str):
            continue
        targets = [
            (target_name, resolvable)
            for target_name, target_fi in fields.items()
            if (resolvable := get_resolvable(target_fi)) is not None
            and resolvable.identity_type is identity_type
            and not any(key in resolved for key in _input_keys(target_fi, target_name))
        ]
        identity: object
        if isinstance(name, str):
            if not targets:
                continue
            try:
                identity = resolvers.lookup(identity_type, name)
            except ResolutionError:
                raise
            except LookupError:
                continue  # no resolver: nothing to fill from
        else:
            found = _identity_of_given_id(fields, resolved, identity_type, resolvers)
            if found is None:
                continue
            identity = found
            given_name: object = getattr(identity, "name", None)
            if isinstance(given_name, str):
                resolved[field_name] = given_name
        for target_name, resolvable in targets:
            resolved[target_name] = resolvable.accessor(identity)


def _identity_of_given_id(
    fields: Mapping[str, FieldInfo],
    resolved: JsonObject,
    identity_type: type[object],
    resolvers: IdentityResolver,
) -> object | None:
    """The identity of *identity_type* that the first ID given in *resolved* finds.

    None if no ID is given.

    An ID is a ``Resolvable`` field of the type that *resolved* gives a value
    other than ``null``, and whose type is not a fixed set of values: a
    ``Literal`` or an enum says what kind of identity it is, not which. A value
    that is not an ID of the field's type is left for the validation of the
    item to refuse. With no resolver registered for the type there is nothing to
    find the identity in, so None; an unknown ID raises ``ResolutionError``
    and a resolver not loaded ``ResolverNotLoadedError``.
    """
    if resolvers.resolver_for(identity_type) is None:
        return None
    for target_name, target_fi in fields.items():
        resolvable = get_resolvable(target_fi)
        if resolvable is None or resolvable.identity_type is not identity_type:
            continue
        key = next(
            (k for k in _input_keys(target_fi, target_name) if k in resolved), None
        )
        id_type = _id_type(target_fi)
        if key is None or id_type is None or _is_value_set(id_type):
            continue
        given = resolved[key]
        if given is None or given is MISSING:
            continue
        try:
            value = read_given_id(target_fi, given)
        except ValidationError:
            continue
        return resolvers.lookup_by(identity_type, resolvable.accessor, value)
    return None


def read_given_id(field_info: FieldInfo, given: object) -> object:
    """*given* as a value of the ID type of the ``Resolvable`` field *field_info*.

    This is how ``merge_patch`` reads an ID that a patch gives in place of a
    name (``_identity_of_given_id``): a ``str`` as text, so ``"7"`` is 7 for
    an ``int`` field, and anything else in pydantic's lax mode, so ``7.0`` is
    7 too. Raises pydantic's ``ValidationError`` (a ``ValueError``) for a
    value that is not one of the type, and ``TypeError`` for a field whose ID
    type is not one type.
    """
    id_type = _id_type(field_info)
    if id_type is None:
        raise TypeError("the field has no single ID type to read an ID as")
    adapter = _id_adapter(id_type)
    return (
        adapter.validate_strings(given)
        if isinstance(given, str)
        else adapter.validate_python(given)
    )


def _is_value_set(tp: object) -> bool:
    """True if *tp* is a ``Literal`` or an enum.

    A field of it holds one of a few values, not an ID.
    """
    return get_origin(tp) is Literal or (
        isinstance(tp, type) and issubclass(tp, enum.Enum)
    )


# ---------------------------------------------------------------------------
# Lists and maps: list values and item operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _ListSpec:
    """How a list field's items, or a map field's values, are named and built."""

    kind: ListKind
    key_paths: tuple[str, ...]
    """The ``KeyField`` paths of a keyed list, one per part of its key.

    Empty for any other list.
    """
    alt_path: str | None
    """The ``KeyField`` alt key path of a keyed list that has one."""
    label_path: str | None
    """The ``KeyField`` label path of a keyed list that has one."""
    item_type: ListItemType | None
    """The model type of the items of a list of models."""
    element: TypeAdapter[object] | None
    """Validates one entry of a list that is not of models."""
    map_type: type[VcoModel] | None
    """The model type of the values of a map, when it has one."""
    map_key: TypeAdapter[object] | None
    """Validates the key of a new entry of a map."""
    name_identity: type[object] | None
    """What the one-part key of a keyed list is the ``NameOf``, if it is one."""
    fixed: FixedList | None
    """The ``FixedList`` of a keyed list of models whose entries are fixed."""
    fold_case: bool
    """Whether the keys of a keyed list are compared ignoring letter case."""


@functools.cache
def _list_spec(field_info: FieldInfo) -> _ListSpec | None:
    """The ``_ListSpec`` of a list or map field (``list_kind``); None for any other."""
    kind = list_kind(field_info)
    if kind is None:
        return None
    key_spec = get_key_spec(field_info) if kind == "keyed" else None
    key_paths = () if key_spec is None else key_spec.paths
    item_type = list_item_type(field_info)
    element: TypeAdapter[object] | None = None
    map_type: type[VcoModel] | None = None
    map_key: TypeAdapter[object] | None = None
    if kind == "map":
        key_type, value_type = _type_args(field_info, dict)
        value_types = [m for m in _members_of(value_type) if _is_vco_model_type(m)]
        map_type = value_types[0] if len(value_types) == 1 else None
        map_key = TypeAdapter[object](
            key_type,  # pyright: ignore[reportArgumentType]
            config=ConfigDict(hide_input_in_errors=True),
        )
    elif item_type is None:
        element = TypeAdapter[object](
            _type_args(field_info, list)[0],  # pyright: ignore[reportArgumentType]
            config=ConfigDict(hide_input_in_errors=True),
        )
    return _ListSpec(
        kind,
        key_paths,
        None if key_spec is None else key_spec.alt,
        None if key_spec is None else key_spec.label,
        item_type,
        element,
        map_type,
        map_key,
        _key_name_of(item_type, key_paths[0]) if len(key_paths) == 1 else None,
        get_fixed_list(field_info) if kind == "keyed" and item_type else None,
        key_spec is not None and key_spec.fold_case,
    )


def _type_args(field_info: FieldInfo, origin: type) -> tuple[object, ...]:
    """The arguments of the ``origin[...]`` member of the field's annotation.

    *origin* is ``list`` or ``dict``.
    """
    for member in _members_of(_held_type(field_info)):
        if get_origin(member) is origin and (args := get_args(member)):
            return args
    return (object, object)


def _members_of(annotation: object) -> list[object]:
    """The members of a union annotation, each without ``Annotated``."""
    return [_unwrap_annotated(m) for m in _union_members(_unwrap_annotated(annotation))]


def _item_key(item: object, paths: tuple[str, ...]) -> KeyForm | None:
    """The key of a keyed-list item at *paths* (``item_key``), in string form.

    None if it has none.
    """
    return key_form(item_key(item, paths))


def _alt_key(spec: _ListSpec, item: object) -> KeyForm | None:
    """The alt key of *item* in a keyed list, in string form.

    None if the list has no alt key or the item none.
    """
    if spec.alt_path is None:
        return None
    return _item_key(item, (spec.alt_path,))


def _show_key(key: KeyValue) -> str:
    """A key for a message: a composite key as ``key_text`` writes it, quoted."""
    return repr(key_text(key) if isinstance(key, tuple) else str(key))


def _show_item(spec: _ListSpec, item: object, index: int) -> str:
    """The item at position *index* of a keyed list, for a message.

    It is named by its key, or by its position when it has none.
    """
    key = item_key(item, spec.key_paths)
    return f"item #{index}" if key is None else f"item {_show_key(key)}"


def _show_selector(selector: object) -> str:
    """A selector for a message.

    A tuple or list of key parts is written as ``key_text`` writes it, quoted.
    """
    if isinstance(selector, (tuple, list)):
        parts = cast("tuple[object, ...] | list[object]", selector)
        return _show_key(tuple(key_part(p) for p in parts))
    return repr(str(selector))


def _key_path_part(key: KeyValue) -> Key:
    """The path part that names an item by *key*.

    It is a one-part key in string form, or a composite key part by part.
    """
    return Key(key if isinstance(key, tuple) else str(key))


def _patch_key(
    fields: Mapping[str, FieldInfo],
    data: Mapping[str, object] | Mapping[object, object],
    paths: tuple[str, ...],
) -> KeyValue | None:
    """The key that *data*, a patch for a new item with *fields*, gives at *paths*.

    None unless it gives every part (``_patch_value``).
    """
    parts: list[KeyScalar] = []
    for path in paths:
        value = _patch_value(fields, data, path)
        if value is None:
            return None
        parts.append(key_part(value))
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else tuple(parts)


def _patch_value(
    fields: Mapping[str, FieldInfo],
    data: Mapping[str, object] | Mapping[object, object],
    path: str,
) -> object:
    """The value that *data* gives at dotted *path*.

    *data* is a patch for a new item with *fields*.

    None if it gives none (or null, or MISSING). Keys of *data* are read as
    ``_build_input`` reads them (python name, alias or camelCase).
    """
    value: object = data
    current: Mapping[str, FieldInfo] | None = fields
    for part in path.split("."):
        if current is None or not is_dict(value):
            return None
        names = _field_names_by_key(current)
        value = next(
            (
                v
                for k, v in value.items()
                if isinstance(k, str) and names.get(k) == part
            ),
            None,
        )
        field_info = current.get(part)
        sub_type = None if field_info is None else _field_model_type(field_info)
        current = None if sub_type is None else sub_type.model_fields
    return None if value is MISSING else value


@dataclass(frozen=True)
class _Selection:
    """What a selector names in a list (``_select``)."""

    positions: list[int]
    """The positions of the items it names, in the list as it was."""
    key: KeyValue | None
    """The key (keyed list) or value (value list) it stands for.

    It is as written, or the resolver's name.

    None where it is none: a selector of a composite key that is not one of
    its parts, ``"#n"``, ``"+"``, any selector of another list.
    """
    position: int | None
    """The ``n`` of a ``"#n"`` selector that was taken as a position."""
    alt: bool = False
    """Whether it names the items by their alt key."""
    label: bool = False
    """Whether it names the items by their label."""
    new: bool = False
    """Whether it is ``"+"`` or ``"+tag"`` in a keyed list: a new item."""
    other_reading: tuple[KeyValue, list[int]] | None = None
    """The key and positions of a second reading of the selector.

    The reading names other items than ``positions`` (``_select``).
    """


def _selector_keys(spec: _ListSpec, selector: object) -> list[KeyValue]:
    """The keys *selector* may stand for in a keyed list, in the order tried.

    For a one-part key, the selector in string form: it is never parsed for
    commas. For a key of n parts: first what it gives as written, a tuple or
    list of n parts, each as ``key_part`` gives it (a ``UUID`` in string form,
    as an item's part is), or a string that ``parse_key_text`` reads as n
    parts; then, for a string, its n parts split at every comma, each part
    text (unless that is the same key in string form). Empty when the
    selector has n parts in no reading.
    """
    if len(spec.key_paths) == 1:
        return [str(selector)]
    n = len(spec.key_paths)
    keys: list[KeyValue] = []
    if isinstance(selector, str):
        try:
            written = parse_key_text(selector)
        except ValueError:
            pass
        else:
            if len(written) == n:
                keys.append(written)
        split = tuple(selector.split(","))
        if len(split) == n and (not keys or key_form(split) != key_form(keys[0])):
            keys.append(split)
    elif isinstance(selector, (tuple, list)):
        values = cast("tuple[object, ...] | list[object]", selector)
        if len(values) == n:
            keys.append(tuple(key_part(v) for v in values))
    return keys


def _refuse_two_readings(
    selection: _Selection,
    spec: _ListSpec,
    items: Sequence[object],
    selector: object,
    where: Path,
) -> None:
    """Raise ``PatchError`` at *where* if *selector* has two readings.

    That is, two readings that name different items.

    A text of a composite key is read as the form ``key_text`` writes and as
    its parts split at every comma; where those are two keys of the list, one
    of them would be taken without a word. The message names both items and
    the quoted form that picks one.
    """
    other = selection.other_reading
    if other is None:
        return
    shown: list[str] = []
    for positions in (selection.positions, other[1]):
        texts = [
            f"#{index} ({key_text(key)})"
            for index in positions
            if (key := item_key(items[index], spec.key_paths)) is not None
        ]
        shown.append(", ".join(texts))
    raise PatchError(
        f"{_show_selector(selector)} is ambiguous: written as a quoted key "
        f"(key_text) it names {shown[0]}, and as parts split at the commas "
        f"it names {shown[1]}. Write the key in its quoted form, as shown "
        "for the item, to choose",
        where,
        "several_items",
    )


def _select(  # noqa: PLR0911 (existing: split it when it next changes)
    items: Sequence[object],
    spec: _ListSpec,
    selector: object,
    resolvers: IdentityResolver | None,
) -> _Selection:
    """Resolve *selector* against *items*; the one place a selector is read.

    In a keyed list, tried in order: an item whose key is one the selector stands for
    (``_selector_keys``), compared in string form part by part (when two readings of the
    selector name different items, the selection says so in ``other_reading`` and the
    caller refuses it, ``_refuse_two_readings``); ``"#n"``, the item at position ``n``;
    ``"+"`` or ``"+tag"``, a new item; an item whose alt key equals the selector in
    string form; an item whose label does; and, where the key is a ``NameOf`` field and
    with *resolvers*, the name the resolver has for the selector, as the key once more.
    An unknown name raises ``ResolutionError``. In a value list, an entry equal to the
    selector in string form, then ``"#n"``. In any other list only ``"#n"`` names an
    item.
    """
    if spec.kind == "value":
        text = str(selector)
        if found := [i for i, item in enumerate(items) if str(item) == text]:
            return _Selection(found, text, None)
    keys = _selector_keys(spec, selector) if spec.kind == "keyed" else []
    matched = [
        (candidate, found)
        for candidate in keys
        if (found := _with_key(items, spec, candidate))
    ]
    if matched:
        (candidate, found), *rest = matched
        other = next(((c, f) for c, f in rest if f != found), None)
        return _Selection(found, candidate, None, other_reading=other)
    key = keys[0] if keys else None
    n = position_of(selector)
    if n is not None:
        return _Selection([n] if n < len(items) else [], None, n)
    if spec.kind != "keyed":
        return _Selection([], str(selector) if spec.kind == "value" else None, None)
    if is_new(selector):
        return _Selection([], None, None, new=True)
    text = str(selector)
    if spec.alt_path is not None and (found := _with_part(items, spec.alt_path, text)):
        return _Selection(found, key, None, alt=True)
    if spec.label_path is not None and (
        found := _with_part(items, spec.label_path, text)
    ):
        return _Selection(found, key, None, label=True)
    if spec.name_identity is not None and resolvers is not None:
        name = _canonical_name(spec.name_identity, text, resolvers)
        return _Selection(_with_key(items, spec, name), name, None)
    return _Selection([], key, None)


def _compared_key(spec: _ListSpec, form: KeyForm | None) -> KeyForm | None:
    """A key in string form as the keyed list of *spec* compares it.

    Case-folded when its ``KeyField`` has *fold_case* (``_folded``), else as
    it is.
    """
    return _folded(form) if spec.fold_case else form


def _with_key(items: Sequence[object], spec: _ListSpec, key: KeyValue) -> list[int]:
    """The positions of the items of a keyed list whose key is *key*, in string form.

    Ignoring letter case when the list's ``KeyField`` has *fold_case*.
    """
    form = _compared_key(spec, key_form(key))
    return [
        i
        for i, item in enumerate(items)
        if _compared_key(spec, _item_key(item, spec.key_paths)) == form
    ]


def _with_part(items: Sequence[object], path: str, text: str) -> list[int]:
    """The positions of the items whose value at *path* is *text* in string form.

    The value at *path* is an alt key or a label.
    """
    return [i for i, item in enumerate(items) if _item_key(item, (path,)) == text]


def select_items(
    model: VcoModel,
    field_name: str,
    selector: object,
    resolvers: IdentityResolver | None = None,
) -> list[int]:
    """The positions of the items that *selector* names in the field *field_name*.

    The field is a list or map field.

    Empty if it names none. In a list a selector is read as in a patch
    (``_select``): the key of an item of a keyed list (for a composite key,
    its parts joined by commas, a tuple of them, or the form ``key_text``
    writes), an entry of a value list,
    ``at(n)``, the alt key or the label of an item of a keyed list that has
    one, and in a keyed list whose key is a ``NameOf`` field, the resolver's
    name for it. An unknown name raises ``ResolutionError``; a text that two
    readings of a composite key take to different items raises ``PatchError``.
    In a map of models the selector is the key of an entry, in string form,
    and the place is that of the entry in the map's order; ``at(n)`` names
    nothing there. Raises ``TypeError`` if the field is neither.
    """
    model_cls = type(model)
    spec = _list_spec(model_cls.model_fields[field_name])
    if spec is None:
        raise TypeError(
            f"{model_cls.__name__}.{field_name} is not a list or a map of models"
        )
    current = _field_value(model, field_name)
    if spec.kind == "map":
        entries: dict[object, object] = current if is_dict(current) else {}
        return [i for i, key in enumerate(entries) if str(key) == str(selector)]
    items: list[object] = current if is_list(current) else []
    selection = _select(items, spec, selector, resolvers)
    _refuse_two_readings(selection, spec, items, selector, Path((field_name,)))
    return selection.positions


def item_selector(model: VcoModel, field_name: str, position: int) -> str | None:
    """The selector that names the item at *position* of *field_name*, and no other.

    *field_name* is a list or map field.

    The inverse of ``select_items``. Candidates, in order: in a keyed list the
    item's label, its key (``selector_of``), its alt key, then ``at(position)``;
    in a value list the entry in string form, then ``at(position)``; in a
    positional or undeclared list ``at(position)``; in a map the entry's key in
    string form. A candidate is used only when ``select_items`` gives exactly
    that position for it: a label or an alt key that reads like another item's
    key names that other item, and so does a text that two readings of a
    composite key take to different items (a ``PatchError`` for ``select_items``,
    so the next candidate is tried). The result is None when no candidate names
    the item alone (``"#1"`` is the key of another item of a keyed list) and for
    an untyped list, which a patch replaces whole.

    Raises ``IndexError`` if there is no item at *position* and ``TypeError``
    if the field is neither a list nor a map of models.
    """
    model_cls = type(model)
    spec = _list_spec(model_cls.model_fields[field_name])
    if spec is None:
        raise TypeError(
            f"{model_cls.__name__}.{field_name} is not a list or a map of models"
        )
    current = _field_value(model, field_name)
    entries: Sequence[object] = (
        list(current) if is_list(current) or is_dict(current) else []
    )
    if not 0 <= position < len(entries):
        raise IndexError(
            f"{model_cls.__name__}.{field_name} has no item at position {position}"
        )
    for candidate in _selector_candidates(spec, entries, position):
        try:
            if select_items(model, field_name, candidate) == [position]:
                return candidate
        except PatchError:
            continue
    return None


def item_selectors(model: VcoModel, field_name: str) -> list[str | None]:
    """``item_selector`` of each item of *field_name*, in the list's order.

    The texts are the same as ``item_selector`` gives one position at a time,
    and the list is read once rather than once for each item: in a keyed
    list a candidate is read (``_select``) against only the items that it
    could name, those whose key, alt key or label is one of its readings,
    found in an index of the list made once (``_ItemIndex``). A candidate
    that reads as a position (``"#n"``) is read against the whole list,
    whose length it depends on. Any other list is named position by
    position, by ``item_selector``.

    Raises ``TypeError`` if the field is neither a list nor a map of models.
    """
    model_cls = type(model)
    spec = _list_spec(model_cls.model_fields[field_name])
    if spec is None:
        raise TypeError(
            f"{model_cls.__name__}.{field_name} is not a list or a map of models"
        )
    current = _field_value(model, field_name)
    if spec.kind != "keyed" or not is_list(current):
        count = len(current) if is_list(current) or is_dict(current) else 0
        return [item_selector(model, field_name, i) for i in range(count)]
    index = _ItemIndex.of(spec, current)
    return [index.selector(position) for position in range(len(current))]


@dataclass(frozen=True, slots=True)
class _ItemIndex:
    """The items of a keyed list by what a selector may name them by.

    Each map is from a value in the string form ``_select`` compares it in to
    the positions of the items that have it.
    """

    spec: _ListSpec
    items: Sequence[object]
    keys: dict[KeyForm, list[int]]
    """By key, as ``_with_key`` compares it (case-folded where the list folds case)."""
    alts: dict[KeyForm, list[int]]
    """By alt key, as ``_with_part`` compares it."""
    labels: dict[KeyForm, list[int]]
    """By label, as ``_with_part`` compares it."""

    @classmethod
    def of(cls, spec: _ListSpec, items: Sequence[object]) -> _ItemIndex:
        """The index of *items*, a keyed list of *spec*."""
        keys: dict[KeyForm, list[int]] = {}
        alts: dict[KeyForm, list[int]] = {}
        labels: dict[KeyForm, list[int]] = {}
        label_path = spec.label_path
        for position, item in enumerate(items):
            key = _compared_key(spec, _item_key(item, spec.key_paths))
            alt = _alt_key(spec, item)
            label = None if label_path is None else _item_key(item, (label_path,))
            for found, value in ((keys, key), (alts, alt), (labels, label)):
                if value is not None:
                    found.setdefault(value, []).append(position)
        return cls(spec, items, keys, alts, labels)

    def selector(self, position: int) -> str | None:
        """``item_selector`` of the item at *position*."""
        for candidate in _selector_candidates(self.spec, self.items, position):
            try:
                if self.select(candidate) == [position]:
                    return candidate
            except PatchError:
                continue
        return None

    def select(self, candidate: str) -> list[int]:
        """What ``select_items`` gives for *candidate*, a text, with no resolvers.

        ``_select`` reads it against the items the index has for its
        readings; those are all the items it can name, unless it reads as a
        position, when the whole list is read.
        """
        spec, items = self.spec, self.items
        where = Path(("items",))
        if position_of(candidate) is not None:
            selection = _select(items, spec, candidate, None)
            _refuse_two_readings(selection, spec, items, candidate, where)
            return selection.positions
        near: set[int] = set()
        for key in _selector_keys(spec, candidate):
            form = _compared_key(spec, key_form(key))
            if form is not None:
                near.update(self.keys.get(form, ()))
        near.update(self.alts.get(candidate, ()))
        near.update(self.labels.get(candidate, ()))
        positions = sorted(near)
        subset = [items[i] for i in positions]
        selection = _select(subset, spec, candidate, None)
        _refuse_two_readings(selection, spec, subset, candidate, where)
        return [positions[i] for i in selection.positions]


def _selector_candidates(  # noqa: C901 (existing: split it when it next changes)
    spec: _ListSpec, entries: Sequence[object], position: int
) -> list[str]:
    """The texts ``item_selector`` tries for the entry at *position*, best first.

    The entry is at *position* of a list, or of the keys of a map.
    """
    item = entries[position]
    match spec.kind:
        case "map":
            return [str(item)]
        case "keyed":
            candidates: list[str] = []
            if spec.label_path is not None:
                label = item_key(item, (spec.label_path,))
                if label is not None:
                    candidates.append(str(label))
            key = item_key(item, spec.key_paths)
            if key is not None:
                candidates.append(selector_of(Key(key)))
            if spec.alt_path is not None:
                alt = item_key(item, (spec.alt_path,))
                if alt is not None:
                    candidates.append(str(alt))
            return [*candidates, selector_of(position)]
        case "value":
            return [str(item), selector_of(position)]
        case "positional" | "undeclared":
            return [selector_of(position)]
        case "untyped":
            return []


def _merge_list(  # noqa: C901, PLR0912, PLR0913, PLR0917 (existing: split it when it next changes)
    model: VcoModel,
    name: str,
    field_info: FieldInfo,
    spec: _ListSpec,
    value: object,
    resolvers: IdentityResolver | None,
    path: Path,
) -> None:
    """Merge one patch value into ``model.<name>``, a list or a map of models.

    A list value replaces a list (``_list_value_input``, then validated and
    ``_check_list_value``). An object is item operations, by selector
    (``_merge_items``) or, for a map, by map key (``_merge_map``). A map takes
    an object only and an untyped list a list only. Any other value is
    validated as the field's value, which refuses it.
    """
    model_cls = type(model)
    held = _field_value(model, name)
    owned = held if isinstance(held, Owned) else None

    def store(new: object) -> None:
        if owned is not None:
            owned.set_local(new)
        else:
            setattr(model, name, new)

    if not is_dict(value):
        if spec.kind == "map":
            raise PatchError(
                f"{name} is a map: give an object of item operations by map key",
                path,
                "operation",
            )
        data = _list_value_input(spec, value, resolvers, path)
        built = _validate_field(model_cls, name, data, path)
        if is_list(data) and is_list(built):
            _check_list_value(spec, data, built, path)
            if spec.fixed is not None and is_list(value):
                _check_fixed_value(spec, value, built, unwrap(held), path)
        store(built)
        return
    if spec.kind == "untyped":
        raise PatchError(
            f"{name} is a list of untyped items: give a list value", path, "operation"
        )

    # An inherited list starts from the profile's.
    current = held if owned is None else owned.localize()
    unset = current is MISSING or current is None
    entries: list[object] | dict[object, object]
    if spec.kind == "map":
        if unset:
            entries = {}
        elif is_dict(current):
            entries = current
        else:
            raise PatchError(
                f"{name} holds {type(current).__name__}, not a map", path, "operation"
            )
        _merge_map(entries, spec, value, resolvers, path, new_only=False)
    else:
        if unset:
            entries = []
        elif is_list(current):
            entries = current
        else:
            raise PatchError(
                f"{name} holds {type(current).__name__}, not a list", path, "operation"
            )
        _merge_items(
            entries, spec, value, resolvers, path, model.config_context, new_only=False
        )
    if unset and entries:
        store(_validate_field(model_cls, name, entries, path))
    elif owned is None and field_kind(field_info).kind is Kind.INHERITABLE:
        # The profile stores what a patch changed in place, a value equal to
        # the display default too, as an assignment would.
        model.model_fields_set.add(name)


def _new_list_input(
    spec: _ListSpec,
    value: list[object] | dict[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
) -> object:
    """The input for a list or map field of an item the patch builds (``_build_input``).

    A list value is built as ``_merge_list`` builds one. An object is item
    operations on an empty list or map, so every one must be an ``add``:
    nothing exists yet to name.
    """
    if spec.kind == "map":
        if not is_dict(value):
            raise PatchError(
                "a map takes an object of item operations by map key", path, "operation"
            )
        entries: dict[object, object] = {}
        _merge_map(entries, spec, value, resolvers, path, new_only=True)
        return entries
    if is_list(value):
        return _list_value_input(spec, value, resolvers, path)
    if spec.kind == "untyped":
        raise PatchError(
            "a list of untyped items takes a list value", path, "operation"
        )
    items: list[object] = []
    _merge_items(items, spec, value, resolvers, path, None, new_only=True)
    return items


def _list_value_input(
    spec: _ListSpec,
    value: object,
    resolvers: IdentityResolver | None,
    path: Path,
) -> object:
    """*value* with each item object checked by ``_build_input``.

    *value* is a list value for a list of models.

    An item's path is ``path[key]`` in a keyed list when the item gives its
    key, ``path[#n]`` otherwise. In a list whose key is a name (``NameOf``),
    an item that gives one takes the resolver's name for it, as an add under
    that name does (an unknown name raises ``ResolutionError``); the IDs of
    the name are then filled by ``_build_input`` and checked by
    ``_check_list_value``. Any other value is returned as it is.
    """
    item_type = spec.item_type
    if item_type is None or not is_list(value):
        return value
    built: list[object] = []
    for index, item in enumerate(value):
        if not is_dict(item):
            built.append(item)
            continue
        named = item
        key = _patch_key(item_type.fields, named, spec.key_paths)
        if key is not None and spec.name_identity is not None and resolvers is not None:
            name = _canonical_name(spec.name_identity, str(key), resolvers)
            if name != key:
                named, key = _inject_key_path(named, spec.key_paths[0], name), name
        part: PathPart = index if key is None else _key_path_part(key)
        built.append(
            _build_input(
                item_type.fields,
                named,
                resolvers,
                path.child(part),
                owner=item_type.name,
            )
        )
    return built


def _check_list_value(
    spec: _ListSpec,
    data: Sequence[object],
    built: Sequence[object],
    path: Path,
) -> None:
    """Check *built* for what validation lets by.

    *built* is a list validated from the list value *data*.

    Each item built from an object refuses keys it has no field for
    (``_refuse_unread_keys``). In a list whose key is a name (``NameOf``),
    such an item that has a name and lacks the IDs of it raises
    ``LookupError``, as an add under that name does
    (``_require_ids_for_name``). A keyed list whose items repeat a key or an
    alt key raises ``PatchError`` at *path*; keys are compared as the list
    compares them (``_compared_key``).
    """
    if len(data) == len(built):
        for index, (item_data, item) in enumerate(zip(data, built, strict=True)):
            _refuse_bool_as_number(item_data, item, path.child(index))
            if is_dict(item_data) and isinstance(item, VcoModel):
                _refuse_unread_keys(
                    item, item_data, path.child(_item_part(spec, item, index))
                )
                key = item_key(item, spec.key_paths)
                if spec.name_identity is not None and key is not None:
                    _require_ids_for_name(
                        item,
                        spec.name_identity,
                        spec.key_paths[0],
                        str(key),
                        path.child(_item_part(spec, item, index)),
                    )
    seen: dict[KeyForm, int] = {}
    seen_alt: dict[KeyForm, int] = {}
    for index, item in enumerate(built):
        key = item_key(item, spec.key_paths)
        form = _compared_key(spec, key_form(key))
        if key is not None and form is not None:
            if form in seen:
                raise PatchError(
                    f"items #{seen[form]} and #{index} have one key, "
                    f"{_show_key(key)}: the items of a keyed list have "
                    "distinct keys",
                    path,
                    "duplicate",
                )
            seen[form] = index
        alt = _alt_key(spec, item)
        if alt is not None:
            if alt in seen_alt:
                first = seen_alt[alt]
                raise PatchError(
                    f"items #{first} and #{index} have one alt key, {alt!r}: "
                    "the items of a keyed list have distinct alt keys; "
                    f"{_show_item(spec, built[first], first)} holds it "
                    f"already, so give item #{index} another alt key",
                    path,
                    "duplicate",
                )
            seen_alt[alt] = index


def _fixed_dump(spec: _ListSpec, item: object) -> JsonObject | None:
    """The JSON dump of model *item* without the editable fields of a ``FixedList``.

    None for any other list.
    """
    if spec.fixed is None or not isinstance(item, VcoModel):
        return None
    return item.model_dump(mode="json", exclude=set(spec.fixed.editable))


def _fixed_of(spec: _ListSpec) -> FixedList:
    """The ``FixedList`` of *spec*, which its caller found to be one."""
    if spec.fixed is None:
        raise AssertionError("the list is not a FixedList")
    return spec.fixed


def _item_type_of(spec: _ListSpec) -> ListItemType:
    """The item type of *spec*, a list of models (a ``FixedList`` is one)."""
    if spec.item_type is None:
        raise AssertionError("the list is not a list of models")
    return spec.item_type


def _fixed_model_dump(spec: _ListSpec, item: object) -> JsonObject:
    """``_fixed_dump`` of *item*, a model of a ``FixedList``."""
    dumped = _fixed_dump(spec, item)
    if dumped is None:
        raise AssertionError("the item is not a model of a FixedList")
    return dumped


def _model_class(item: object) -> type[VcoModel]:
    """The class of *item*, a model of a ``FixedList``."""
    if not isinstance(item, VcoModel):
        raise TypeError("the item is not a model of a FixedList")
    return type(item)


def _fixed_differences(
    before: JsonObject, after: JsonObject, model: type[VcoModel]
) -> str:
    """The names of the fields in which two ``_fixed_dump`` s differ, for a message.

    Each is the attribute name of *model*'s field for the key, the key itself
    where *model* has no field for it.
    """
    return ", ".join(
        python_name(model, k) or k
        for k in {**before, **after}
        if before.get(k) != after.get(k)
    )


def _fixed_field(path: Path) -> str:
    """The field of the list at *path* in the patch, as written."""
    last = path.parts[-1] if path.parts else None
    return last if isinstance(last, str) else "the list"


def _fixed_form(spec: _ListSpec, key: object, path: Path) -> str:
    """The JSON form of an edit a ``FixedList`` takes, for a message.

    The edit is of the entry *key*, one editable field.

    *path* is the list's path in the patch; its last part is the field as written.
    """
    fixed = _fixed_of(spec)
    editable = fixed.editable[0] if fixed.editable else "..."
    return (
        "{"
        f"{json.dumps(_fixed_field(path))}: {{{json.dumps(str(key))}: "
        f"{{{json.dumps(editable)}: {{...}}}}"
        "}}"
    )


def _fixed_rule(spec: _ListSpec, path: Path) -> str:
    """The sentence that says what a ``FixedList`` takes, for a message."""
    return (
        f"{_fixed_field(path)} is a fixed list: its entries, their keys and "
        "their order are fixed, and only "
        f"{' and '.join(_fixed_of(spec).editable)} of an entry may change"
    )


def _check_fixed_op(  # noqa: PLR0913 (existing: split it when it next changes)
    spec: _ListSpec,
    items: Sequence[object],
    op: ItemOp,
    where: Path,
    path: Path,
    *,
    new_only: bool,
) -> None:
    """Refuse an item operation on a ``FixedList`` that is not a change.

    That is an add, delete, move or ``_position``.

    Every operation is refused in a list the patch builds (*new_only*): it
    has no entries to edit yet. *where* is the entry's path, *path* the
    list's.
    """
    fixed = _fixed_of(spec)
    if new_only:
        raise PatchError(
            f"{_fixed_rule(spec, path)}: this patch builds the item, so the "
            "list has no entries to edit yet; give its entries as a list "
            "value, or leave them out",
            where,
            "fixed_list",
        )
    if op.kind == "change" and op.place is None:
        return
    what = op.kind if op.kind != "change" else "_position"
    first = item_key(items[0], spec.key_paths) if items else None
    form = _fixed_form(spec, "<key>" if first is None else first, path)
    raise PatchError(
        f"{_fixed_rule(spec, path)}: a {what} is not an edit of an entry's "
        f"{' or '.join(fixed.editable)}. To edit an entry, send {form}",
        where,
        "fixed_list",
    )


def _check_fixed_change(
    spec: _ListSpec,
    item: object,
    before: JsonObject | None,
    where: Path,
    path: Path,
) -> None:
    """Refuse a change of *item* that altered a fixed part of a ``FixedList``.

    A fixed part is anything but the editable fields.

    *before* is ``_fixed_dump`` of the item before the change; it is compared
    with the dump after it, so a rename, an alt key change and a change of
    any other field are all found. *where* is the entry's path, *path* the
    list's.
    """
    if before is None:
        return
    after = _fixed_model_dump(spec, item)
    if after == before:
        return
    key = item_key(item, spec.key_paths)
    raise PatchError(
        f"{_fixed_rule(spec, path)}: this patch changes "
        f"{_fixed_differences(before, after, _model_class(item))} of "
        f"{_show_item(spec, item, 0)}. "
        "To edit the entry, send the editable fields only, "
        + _fixed_form(spec, "<key>" if key is None else key, path),
        where,
        "fixed_list",
    )


def _check_fixed_value(
    spec: _ListSpec,
    value: Sequence[object],
    built: Sequence[object],
    current: object,
    path: Path,
) -> None:
    """Check *built*, for a ``FixedList``, against the *current* list.

    *built* is validated from the list value *value*.

    Accepted when it has the current keys in the current order and each item
    equals the current one but for the editable fields. An item object that
    gives no alt key (under any of its names) takes the current item's: that
    is decided from *value*, the patch, because a built item may hold an ID
    its model made up. Anything else raises ``PatchError`` at *path* or at
    the item.
    """
    item_type = _item_type_of(spec)
    rule = _fixed_rule(spec, path)
    if not is_list(current):
        raise PatchError(
            f"{rule}: a list value cannot build the list where there was "
            "none. Leave the field out of the patch: its entries come from "
            "the template when the segment is assigned, or are repaired "
            "when the module is written",
            path,
            "fixed_list",
        )
    keys = [item_key(i, spec.key_paths) for i in current]
    if [_item_key(i, spec.key_paths) for i in built] != [key_form(k) for k in keys]:
        shown = ", ".join(repr(str(k)) for k in keys)
        raise PatchError(
            f"{rule}: a list value must give the current entries ({shown}) "
            "in that order, and no other. To edit an entry, send "
            + _fixed_form(spec, "<key>" if not keys else keys[0], path),
            path,
            "fixed_list",
        )
    for index, (data, new, old) in enumerate(zip(value, built, current, strict=True)):
        where = path.child(_item_part(spec, old, index))
        if (
            spec.alt_path is not None
            and is_dict(data)
            and _patch_value(item_type.fields, data, spec.alt_path) is None
        ):
            _copy_path(old, new, spec.alt_path)
        before, after = _fixed_model_dump(spec, old), _fixed_model_dump(spec, new)
        if before != after:
            key = item_key(old, spec.key_paths)
            raise PatchError(
                f"{rule}: the list value changes "
                f"{_fixed_differences(before, after, _model_class(old))} of "
                f"{_show_item(spec, old, index)}. "
                + _alt_key_advice(spec, old, new)
                + "Give each entry as it is, "
                + "with its editable fields changed, or send "
                + _fixed_form(spec, "<key>" if key is None else key, path),
                where,
                "fixed_list",
            )


def _alt_key_advice(spec: _ListSpec, old: object, new: object) -> str:
    """The sentence that says how to keep the alt key, or empty.

    It is not empty when *new* gives another alt key than *old*'s.
    """
    if spec.alt_path is None or _alt_key(spec, old) == _alt_key(spec, new):
        return ""
    return f"Leave `{spec.alt_path}` out of the item and it keeps the current one. "


def _copy_path(source: object, target: object, dotted: str) -> None:
    """Set *target*'s attribute at *dotted* (python names) to *source*'s."""
    *parents, last = dotted.split(".")
    for name in parents:
        source, target = getattr(source, name), getattr(target, name)
    setattr(target, last, getattr(source, last))


def _item_part(spec: _ListSpec, item: object, index: int) -> PathPart:
    """The path part of a list item: its key in a keyed list, else its position."""
    key = item_key(item, spec.key_paths)
    return index if key is None else _key_path_part(key)


@dataclass(eq=False, slots=True)
class _Slot:
    """One item of a list while item operations run on it, told apart by identity."""

    value: object


@dataclass(frozen=True)
class _ItemEntry:
    """One entry of an item-operations object, resolved against the list as it was."""

    kind: Literal["change", "add", "delete", "move"]
    body: dict[object, object]
    place: Place | None
    path: Path
    slot: _Slot | None
    """The item a change, delete or move names."""
    new: object
    """For an add: what it adds, built in step 1.

    It is the item (list of models) or the validated value (value list).
    """
    anchor: _Slot | None
    """The item a ``before`` or ``after`` place names."""


def _merge_items(  # noqa: C901, PLR0912, PLR0913, PLR0917 (existing: split it when it next changes)
    items: list[object],
    spec: _ListSpec,
    ops: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    context: ConfigContext | None,
    *,
    new_only: bool,
) -> None:
    """Apply the item operations *ops* to *items*, a list that is not a map, in place.

    1. Every selector and anchor is resolved against the list as it was, and
       every item an add adds is built (``_resolve_items``); any error raises
       here, before anything changes.
    2. Changes, in the order written. Afterwards a renamed item whose key
       another item has raises, and so does an item whose alt key changed to
       one another item has.
    3. Deletes.
    4. Adds and moves, in the order written; a place is turned into an index
       when its entry is applied. An add with no place goes to the end.

    *context* is the config level of the model that holds the list.
    *new_only* is for a list the patch builds: every entry must be an add.
    """
    slots = [_Slot(item) for item in items]
    entries = _resolve_items(
        slots, spec, ops, resolvers, path, context, new_only=new_only
    )

    paths = spec.key_paths
    renamed: list[tuple[_ItemEntry, _Slot]] = []
    alt_changed: list[tuple[_ItemEntry, _Slot]] = []
    for entry in entries:
        slot = entry.slot
        if entry.kind != "change" or slot is None:
            continue
        item = slot.value
        if not isinstance(item, VcoModel):
            raise TypeError("_resolve_items let a change of a non-model through")
        before = _item_key(item, paths)
        alt_before = _alt_key(spec, item)
        fixed_before = _fixed_dump(spec, item)
        _merge_model(item, entry.body, resolvers, entry.path)
        _check_fixed_change(spec, item, fixed_before, entry.path, path)
        _check_merged(
            item,
            spec.item_type.validate
            if spec.item_type is not None and len(spec.item_type.variants) > 1
            else type(item).model_validate,
            entry.path,
        )
        if _item_key(item, paths) != before:
            renamed.append((entry, slot))
        if _alt_key(spec, item) != alt_before:
            alt_changed.append((entry, slot))
    for entry, slot in renamed:
        key = item_key(slot.value, paths)
        form = key_form(key)
        if key is not None and any(
            s is not slot and _item_key(s.value, paths) == form for s in slots
        ):
            raise PatchError(
                f"another item has the key {_show_key(key)}", entry.path, "exists"
            )
    for entry, slot in alt_changed:
        alt = _alt_key(spec, slot.value)
        if alt is None:
            continue
        for index, other in enumerate(slots):
            if other is not slot and _alt_key(spec, other.value) == alt:
                raise PatchError(
                    f"{_show_item(spec, other.value, index)} already has the "
                    f"alt key {alt!r}: give this item another alt key, or "
                    "change that item instead",
                    entry.path,
                    "exists",
                )

    deleted = {id(e.slot) for e in entries if e.kind == "delete"}
    slots = [s for s in slots if id(s) not in deleted]

    for entry in entries:
        if entry.kind == "add":
            slot = _Slot(_new_list_item(spec, entry, slots))
        elif entry.place is not None and entry.slot is not None:
            slot = entry.slot
            slots.remove(slot)
        else:
            continue
        slots.insert(_place_index(slots, entry.place, entry.anchor), slot)

    items[:] = [s.value for s in slots]


def _resolve_items(  # noqa: C901, PLR0912, PLR0913, PLR0917 (existing: split it when it next changes)
    slots: Sequence[_Slot],
    spec: _ListSpec,
    ops: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    context: ConfigContext | None,
    *,
    new_only: bool,
) -> list[_ItemEntry]:
    """Parse every entry of *ops* and resolve its selector and anchor.

    It also builds what an add adds.

    Step 1 of ``_merge_items``. Raises ``PatchError`` for an operation the
    list does not take, a selector that names no item or several, two
    selectors for one item, two adds of one key or value, an item an add
    cannot build, and an anchor that names no item, several, the item itself
    or an item the patch deletes. Changes nothing.
    """
    items = [s.value for s in slots]
    entries: list[_ItemEntry] = []
    named: dict[int, Path] = {}
    added: dict[KeyForm, Path] = {}
    for selector, value in ops.items():
        selection = _select(items, spec, selector, resolvers)
        where = path.child(_selector_part(selector, selection))
        _refuse_two_readings(selection, spec, items, selector, where)
        op = parse_item_op(value, where)
        if spec.fixed is not None:
            _check_fixed_op(spec, items, op, where, path, new_only=new_only)
        slot: _Slot | None = None
        new: object = None
        if op.kind == "add":
            new = _check_add(
                spec, selector, selection, op, items, resolvers, where, context
            )
            form = _added_form(spec, new)
            if form is not None:
                if form in added:
                    what = "value" if spec.kind == "value" else "key"
                    raise PatchError(
                        f"{added[form]} adds this {what} too: one add per {what}",
                        where,
                        "duplicate",
                    )
                added[form] = where
        elif new_only:
            raise PatchError(
                f"a {op.kind} in a list the patch builds has nothing to name: "
                'write {"_op": "add", ...} entries (add(...)) or a list value',
                where,
                "operation",
            )
        else:
            if op.kind == "change":
                _check_change(spec, where)
            index = _named_item(spec, selector, selection, op, items, where)
            if index in named:
                raise PatchError(
                    f"{named[index]} names this item too: one selector per item",
                    where,
                    "duplicate",
                )
            named[index] = where
            slot = slots[index]
            if op.kind == "change" and not isinstance(slot.value, VcoModel):
                raise PatchError(
                    f"the item is {type(slot.value).__name__}, not a model",
                    where,
                    "operation",
                )
        anchor: _Slot | None = None
        if op.place is not None and op.place.kind in ("before", "after"):
            anchor = slots[
                _anchor_position(
                    items, spec, op.place.anchor, slot, slots, resolvers, where
                )
            ]
        entries.append(
            _ItemEntry(op.kind, _body(op), op.place, where, slot, new, anchor)
        )
    deleted = {id(e.slot) for e in entries if e.kind == "delete"}
    for entry in entries:
        if entry.anchor is not None and id(entry.anchor) in deleted:
            raise PatchError(
                "the anchor is an item this patch deletes", entry.path, "operation"
            )
    return entries


def _selector_part(selector: object, selection: _Selection) -> PathPart:
    """Where the entry under *selector* is in the patch, within its list.

    A position for ``"#n"``; the key as written for a composite key; else the
    selector in string form.
    """
    if selection.position is not None:
        return selection.position
    if isinstance(selection.key, tuple):
        return Key(selection.key)
    if isinstance(selector, (tuple, list)):
        parts = cast("tuple[object, ...] | list[object]", selector)
        return Key(tuple(key_part(p) for p in parts))
    return Key(str(selector))


def _added_form(spec: _ListSpec, new: object) -> KeyForm | None:
    """The key or value, in string form, of what an add adds; or None.

    The key in a keyed list, as the list compares keys (``_compared_key``);
    the value in a value list.
    """
    if spec.kind == "value":
        return str(new)
    if spec.kind == "keyed":
        return _compared_key(spec, _item_key(new, spec.key_paths))
    return None


def _check_add(  # noqa: C901, PLR0913, PLR0917 (existing: split it when it next changes)
    spec: _ListSpec,
    selector: object,
    selection: _Selection,
    op: ItemOp,
    items: Sequence[object],
    resolvers: IdentityResolver | None,
    where: Path,
    context: ConfigContext | None,
) -> object:
    """Refuse an ``add`` the list does not take; return what it adds.

    What it adds is ``_ItemEntry.new``.

    In a list of models, the item, built here; in a value list, the value,
    validated. Under a plain selector a keyed list takes the key only: a key
    of as many parts as the list's, not an alt key or a label. Under ``"+"``
    the key is whatever the item built from the body has, if any.
    """
    if spec.kind in ("positional", "undeclared"):
        if spec.item_type is None:
            raise PatchError(
                "a list of values named by position has no body to build an item "
                "from: give a list value",
                where,
                "operation",
            )
        if not is_new(selector):
            raise PatchError(
                "a new item of a list named by position goes under "
                '"+" or "+tag": write {"+": {"_op": "add", ...}} ("+": add(...))',
                where,
                "selector",
            )
        return _new_item(spec.item_type, _body(op), resolvers, where, context)
    if selection.position is not None:
        raise PatchError(
            '"#n" (at(n)) names an item that exists: an add takes a '
            + ("key" if spec.kind == "keyed" else "value")
            + " as its selector",
            where,
            "selector",
        )
    if spec.kind == "keyed":
        return _check_keyed_add(
            spec, selector, selection, op, items, resolvers, where, context
        )
    if selection.positions:
        raise PatchError(
            f"{selection.key!r} is already in the list{_CHANGE_IT_IN_LIST}",
            where,
            "exists",
        )
    # A value list: the selector is the value.
    if is_new(selector):
        raise PatchError(
            f"{str(selector)!r} names no value: an add in a list of values takes "
            "the value itself as its selector, "
            'as in {"<value>": {"_op": "add"}} (`{"<value>": add()}`); a '
            'value that starts with "+" cannot be added under its own text, '
            "so give the whole list as a list value",
            where,
            "selector",
        )
    if op.body:
        raise PatchError(
            "an add in a list of values takes no fields: "
            'write {"_op": "add"} (add()) under the value',
            where,
            "operation",
        )
    if spec.element is None:
        raise AssertionError("a list of values has no element adapter")
    new = _validated(spec.element.validate_python, selector, (), where)
    if any(str(item) == str(new) for item in items):
        raise PatchError(
            f"{new!r} is already in the list{_CHANGE_IT_IN_LIST}", where, "exists"
        )
    return new


def _key_form_hint(spec: _ListSpec, items: Sequence[object]) -> str:
    """How a composite key is written, with an example.

    The example is the key of the first keyed item of *items*.
    """
    n = len(spec.key_paths)
    form = f"a key is its {n} parts ({', '.join(spec.key_paths)}) joined by commas"
    for item in items:
        key = item_key(item, spec.key_paths)
        if key is not None:
            return f"{form}, as in {key_selector(key)}"
    return form


def _check_keyed_add(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    spec: _ListSpec,
    selector: object,
    selection: _Selection,
    op: ItemOp,
    items: Sequence[object],
    resolvers: IdentityResolver | None,
    where: Path,
    context: ConfigContext | None,
) -> VcoModel:
    """The item an ``add`` in a keyed list adds, built.

    ``PatchError`` if the list does not take it (``_check_add``).

    Under ``"+"`` in a list whose key is a name (``NameOf``), a name in the
    body is taken as an add under that name: the same resolver lookup and the
    same refusals, ``LookupError`` included when the IDs of the name are not
    found (``_new_keyed_item``).
    """
    item_type = spec.item_type
    if item_type is None:
        raise PatchError("this list has no item type to build from", where, "operation")
    from_body = False
    if selection.new and spec.name_identity is not None:
        name = _patch_key(item_type.fields, op.body, spec.key_paths)
        if name is not None:
            text = str(name)
            named = _select(items, spec, text, resolvers)
            selection = (
                named
                if named.key is not None
                else _Selection(_with_key(items, spec, text), text, None)
            )
            from_body = True
    if selection.new:
        item = _new_item(item_type, _body(op), resolvers, where, context)
        key = item_key(item, spec.key_paths)
        if key is not None and _with_key(items, spec, key):
            raise PatchError(
                f"the body gives the key {_show_key(key)}, which is already in the "
                f"list{_CHANGE_IT_IN_LIST}",
                where,
                "exists",
            )
        return item
    key = selection.key
    if key is None:
        raise PatchError(
            f"{_show_selector(selector)} is not a key of this list: "
            + _key_form_hint(spec, items)
            + '; or write the item under "+" with the parts among its fields, as in '
            + '{"+": {"_op": "add", ...}} ("+": add({...}))',
            where,
            "selector",
        )
    if selection.positions and not selection.label:
        where_in = " as an alt key" if selection.alt else ""
        raise PatchError(
            f"{_show_key(key)} is already in the list{where_in}{_CHANGE_IT_IN_LIST}",
            where,
            "exists",
        )
    parts = key if isinstance(key, tuple) else (key,)
    for key_path, part in zip(spec.key_paths, parts, strict=True):
        given = _patch_value(item_type.fields, op.body, key_path)
        # Under "+" the body gave the name; the key is the resolver's for it.
        if not from_body and given is not None and str(given) != str(part):
            raise PatchError(
                f"the body gives {key_path} {str(given)!r}, and the selector "
                f"{_show_key(key)}: an add takes the key from its selector",
                where,
                "operation",
            )
    return _new_keyed_item(spec, item_type, _body(op), key, resolvers, where, context)


def _check_change(spec: _ListSpec, where: Path) -> None:
    """Refuse a change (a plain object) where the items have no fields."""
    if spec.item_type is None:
        raise PatchError(
            "an entry of a list of values has no fields to change: "
            'add it with {"_op": "add"} (add()), '
            'remove it with {"_op": "delete"} (delete()), or give a list value',
            where,
            "operation",
        )


def _named_item(  # noqa: C901, PLR0912, PLR0913, PLR0917 (existing: split it when it next changes)
    spec: _ListSpec,
    selector: object,
    selection: _Selection,
    op: ItemOp,
    items: Sequence[object],
    where: Path,
) -> int:
    """The position of the one item a change, delete or move names.

    Else ``PatchError``.
    """
    if len(selection.positions) == 1:
        return selection.positions[0]
    if selection.positions:
        raise PatchError(
            _names_several(_show_selector(selector), spec, items, selection.positions),
            where,
            "several_items",
        )
    if selection.position is not None:
        raise PatchError(
            f"there is no item at position {selection.position}: "
            f"the list has {len(items)}",
            where,
            "no_item",
        )
    if spec.kind in ("positional", "undeclared"):
        hint = (
            '; a new item is written {"+": {"_op": "add", ...}} ("+": add(...))'
            if is_new(selector)
            else ""
        )
        raise PatchError(
            f"{str(selector)!r} is not a position: the items of this list are "
            f'named by "#n" (at(n)){hint}',
            where,
            "selector",
        )
    if selection.new:
        raise PatchError(
            f"{str(selector)!r} names a new item, which only "
            '{"_op": "add", ...} (add(...)) takes',
            where,
            "operation",
        )
    if spec.kind != "keyed":
        message = f"no item has the entry {selection.key!r}"
    else:
        names = ["key"]
        if spec.alt_path is not None:
            names.append("alt key")
        if spec.label_path is not None:
            names.append("label")
        what = (
            names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
        )
        if len(spec.key_paths) > 1:
            form = _key_form_hint(spec, items)
            shown = _show_selector(selector)
            if selection.key is None:
                message = f"{shown} is not a key of this list: {form}"
                if op.kind == "change":
                    message += (
                        '; to add an item, write {"+": {"_op": "add", ...}} '
                        '("+": add({...})) with its fields'
                    )
                raise PatchError(message, where, "selector")
            message = f"no item has the {what} {shown}; {form}"
        else:
            shown = (
                _show_selector(selector)
                if selection.key is None
                else _show_key(selection.key)
            )
            message = f"no item has the {what} {shown}"
    if op.kind == "change":
        message += '; to add one, write {"_op": "add", ...} under that key (add(...))'
    raise PatchError(message, where, "no_item")


def _names_several(
    subject: str, spec: _ListSpec, items: Sequence[object], positions: Sequence[int]
) -> str:
    """The message for *subject*, which names the items at *positions*.

    *subject* is a selector or anchor.

    It lists each match with its position and its key (a composite key as
    ``key_text`` writes it) or value, and its alt key if it has one.
    """
    shown: list[str] = []
    for index in positions:
        item = items[index]
        if spec.kind != "keyed":
            key = str(item)
        else:
            value = item_key(item, spec.key_paths)
            if value is None:
                key = "no key"
            else:
                key = key_text(value) if isinstance(value, tuple) else str(value)
        alt = _alt_key(spec, item)
        shown.append(
            f"#{index} ({key})" if alt is None else f"#{index} ({key}, alt key {alt})"
        )
    return (
        f"{subject} names {len(positions)} items: {', '.join(shown)}; "
        "select one of them"
    )


def _anchor_position(  # noqa: PLR0913, PLR0917 (existing: split it when it next changes)
    items: Sequence[object],
    spec: _ListSpec,
    anchor: object,
    slot: _Slot | None,
    slots: Sequence[_Slot],
    resolvers: IdentityResolver | None,
    where: Path,
) -> int:
    """The position of the one item *anchor* names, which is not *slot*'s.

    Else ``PatchError``.
    """
    selection = _select(items, spec, anchor, resolvers)
    _refuse_two_readings(selection, spec, items, anchor, where)
    if not selection.positions:
        raise PatchError(f"the anchor {anchor!r} names no item", where, "no_item")
    if len(selection.positions) > 1:
        raise PatchError(
            _names_several(f"the anchor {anchor!r}", spec, items, selection.positions),
            where,
            "several_items",
        )
    index = selection.positions[0]
    if slots[index] is slot:
        raise PatchError(
            "an item cannot be placed before or after itself", where, "operation"
        )
    return index


def _new_list_item(
    spec: _ListSpec, entry: _ItemEntry, slots: Sequence[_Slot]
) -> object:
    """What an ``add`` entry adds, checked against the list now in *slots*.

    What it adds was built in step 1.

    Its key, value or alt key may have come into the list since step 1, by a
    rename or an earlier add: that raises ``PatchError``.
    """
    new = entry.new
    form = _added_form(spec, new)
    if form is not None:
        if spec.kind == "value":
            taken = any(str(s.value) == form for s in slots)
        else:
            taken = bool(_with_key([s.value for s in slots], spec, form))
        if taken:
            shown = item_key(new, spec.key_paths) if spec.kind == "keyed" else None
            raise PatchError(
                f"{_show_key(form if shown is None else shown)} is already in the "
                f"list{_CHANGE_IT_IN_LIST}",
                entry.path,
                "exists",
            )
    alt = _alt_key(spec, new)
    if alt is not None:
        for index, slot in enumerate(slots):
            if _alt_key(spec, slot.value) == alt:
                raise PatchError(
                    f"{alt!r} is already in the list as an alt key, that of "
                    f"{_show_item(spec, slot.value, index)}: give the new "
                    "item another alt key, or change that item instead",
                    entry.path,
                    "exists",
                )
    if spec.kind == "keyed" and isinstance(new, VcoModel):
        new.mark_added()
    return new


def _place_index(
    slots: Sequence[_Slot], place: Place | None, anchor: _Slot | None
) -> int:
    """Where in *slots*, the list now, an item placed at *place* goes."""
    if place is None or place.kind == "last":
        return len(slots)
    if place.kind == "first":
        return 0
    index = next(i for i, s in enumerate(slots) if s is anchor)
    return index if place.kind == "before" else index + 1


def _merge_map(  # noqa: C901, PLR0912, PLR0913 (existing: split it when it next changes)
    entries: dict[object, object],
    spec: _ListSpec,
    ops: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    *,
    new_only: bool,
) -> None:
    """Apply the item operations *ops* to *entries*, a map of models, in place.

    A selector names an existing entry by its key in string form. The key of
    an ``add`` is validated as the map's key type, in string form as JSON
    writes it, and stored as validated (``"2"`` is the key ``2`` of a
    ``dict[int, ...]``). Change, ``add`` and ``delete`` only: a map has no
    order, so ``move``, a ``_position`` and a position selector raise
    ``PatchError``. Every entry is checked first; then changes, deletes and
    adds, in that order. *new_only* is for a map the patch builds: every
    entry must be an add.
    """
    keys = {str(key): key for key in entries}
    resolved: list[tuple[ItemOp, object, Path]] = []
    named: set[str] = set()
    for selector, value in ops.items():
        text = str(selector)
        where = path.child(Key(text))
        op = parse_item_op(value, where)
        if op.place is not None:
            raise PatchError(
                'a map has no order: it takes no "_position" (move(...))',
                where,
                "operation",
            )
        if op.kind == "add":
            if text not in keys and (
                position_of(selector) is not None or is_new(selector)
            ):
                raise PatchError(
                    f"{text!r} names no entry of a map: an add takes the new "
                    "entry's key as its selector, "
                    'as in {"<key>": {"_op": "add", ...}} '
                    '(`{"<key>": add(...)}`)',
                    where,
                    "selector",
                )
            key = _new_map_key(spec, text, where)
            text = str(key)
            if text in keys:
                raise PatchError(
                    f"{text!r} is already in the map; to change it, write a plain "
                    'object under its key (no "_op")',
                    where,
                    "exists",
                )
        elif new_only:
            raise PatchError(
                f"a {op.kind} in a map the patch builds has nothing to name: "
                'write {"_op": "add", ...} entries (add(...))',
                where,
                "operation",
            )
        elif text not in keys:
            if position_of(selector) is not None:
                raise PatchError(
                    "a map has no positions: name an entry by its key",
                    where,
                    "selector",
                )
            raise PatchError(
                f"no entry has the key {text!r}"
                + (
                    '; to add one, write {"_op": "add", ...} under that key (add(...))'
                    if op.kind == "change"
                    else ""
                ),
                where,
                "no_item",
            )
        else:
            key = keys[text]
            current = entries[key]
            if op.kind == "change" and not isinstance(current, VcoModel):
                raise PatchError(
                    f"the entry is {type(current).__name__}, not a model",
                    where,
                    "operation",
                )
        if text in named:
            raise PatchError(
                "another selector names this entry: one per entry", where, "duplicate"
            )
        named.add(text)
        resolved.append((op, key, where))

    for op, key, where in resolved:
        if op.kind == "change":
            current = entries[key]
            if not isinstance(current, VcoModel):
                raise TypeError("a change of a map entry that is not a model")
            _merge_model(current, _body(op), resolvers, where)
            _check_merged(current, type(current).model_validate, where)
    for op, key, _ in resolved:
        if op.kind == "delete":
            del entries[key]
    for op, key, where in resolved:
        if op.kind == "add":
            if spec.map_type is None:
                raise AssertionError("_new_map_key let an add through")
            entries[key] = _new_model_from_patch(
                spec.map_type, _body(op), resolvers, where
            )


def _new_map_key(spec: _ListSpec, text: str, where: Path) -> object:
    """The key an ``add`` in a map writes: *text* validated as the map's key type.

    Raises ``PatchError`` if it is not one, or if the map's values are not of
    one model type, so there is nothing to build the entry as.
    """
    if spec.map_type is None:
        raise PatchError(
            "this map has no one model type to build from", where, "operation"
        )
    if spec.map_key is None:
        raise AssertionError("a map has no key adapter")
    return _validated(spec.map_key.validate_python, text, (), where)


def _body(op: ItemOp) -> dict[object, object]:
    """The body of a parsed item operation, typed as the engine's patches are.

    No copy: ``parse_item_op`` builds the body fresh, so it is not the
    patch's own object.
    """
    return cast("dict[object, object]", op.body)


def _auto_set_override_flags(model: VcoModel, before: Mapping[str, object]) -> None:
    """Set OverrideFlag fields to True if a non-EdgeLocal field changed.

    On an edge layer this makes the layer's own section apply in place of the
    profile's; nothing is copied from the profile. A plan lists each flag so
    turned on (``ModulePlan.overrides_turned_on``).
    """
    fields = type(model).model_fields
    flag_names = [name for name, fi in fields.items() if has_override_flag(fi)]
    if flag_names and _has_non_edge_local_changes(model, before):
        for name in flag_names:
            setattr(model, name, True)


def _has_non_edge_local_changes(model: VcoModel, before: Mapping[str, object]) -> bool:
    """Check whether a field in *before* (field name -> old value) now differs.

    OverrideFlag and EdgeLocal fields don't count, and neither does an
    ``Inheritable`` field: its kind declares who owns it, and a change of
    that ownership (``null``, a value, a copy from the profile) is not an
    edit of a section that the flag guards. When both old and new
    values are the same VcoModel type, the check recurses into them, so a
    change confined to EdgeLocal descendants doesn't count either.
    """
    fields = type(model).model_fields
    for name, held in before.items():
        fi = fields.get(name)
        if (
            fi is None
            or has_override_flag(fi)
            or is_edge_local(fi)
            or field_kind(fi).kind is Kind.INHERITABLE
        ):
            continue
        new = unwrap(_field_value(model, name))
        old = unwrap(held)
        if isinstance(new, VcoModel) and type(new) is type(old):
            if _has_non_edge_local_changes(new, _all_field_values(new, old)):
                return True
        elif new != old:
            return True
    return False


def _all_field_values(model: VcoModel, source: object) -> JsonObject:
    """Read each of *model*'s fields from *source*, an instance of the same class."""
    return {
        name: unwrap(_field_value(source, name)) for name in type(model).model_fields
    }
