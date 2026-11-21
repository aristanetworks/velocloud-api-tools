"""The merge engine behind ``VcoModel.merge_patch``.

``apply_patch`` merges a partial dict into a model; everything private here
supports it. ``VcoModel.merge_patch`` in ``base.py`` is a thin call into it.
"""

from __future__ import annotations

import copy
import difflib
import functools
import math
from collections.abc import Mapping, Sequence
from typing import Annotated

from pydantic import MISSING, Discriminator, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .base import (
    IdentityResolver,
    JsonObject,
    VcoModel,
    _DefaultSource,  # pyright: ignore[reportPrivateUsage]
    _effective_default,  # pyright: ignore[reportPrivateUsage]
    _field_accepts_missing,  # pyright: ignore[reportPrivateUsage]
    _field_accepts_none,  # pyright: ignore[reportPrivateUsage]
    _field_model_type,  # pyright: ignore[reportPrivateUsage]
    _field_names_by_key,  # pyright: ignore[reportPrivateUsage]
    _field_value,  # pyright: ignore[reportPrivateUsage]
    _id_adapter,  # pyright: ignore[reportPrivateUsage]
    _id_type,  # pyright: ignore[reportPrivateUsage]
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
    _list_item_type,  # pyright: ignore[reportPrivateUsage]
    _ListItemType,  # pyright: ignore[reportPrivateUsage]
)
from .fields import (
    get_key_field,
    get_name_of,
    get_resolvable,
    has_override_flag,
    is_edge_local,
)
from .identity import ResolutionError
from .patch import PatchError
from .rebase import Key, Path


def apply_patch(
    model: VcoModel, patch: Mapping[str, object], resolvers: IdentityResolver | None
) -> VcoModel:
    """Recursively merge a partial dict into *model*, in place. See ``VcoModel.merge_patch``.

    All or nothing: the patch is merged into a deep copy of *model* first, so
    a patch that raises does so before *model* is touched.
    """
    items = dict[object, object](patch.items())  # Mapping keys are invariant
    _merge_model(model.model_copy(deep=True), items, resolvers, Path(()))
    _merge_model(model, items, resolvers, Path(()))
    return model


# ---------------------------------------------------------------------------
# merge_patch internals
# ---------------------------------------------------------------------------


def _merge_model(
    model: VcoModel,
    patch: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
) -> None:
    """Merge *patch* into *model*, in place. *path* is where *model* is in the patch.

    Every key must name a field of the model; a directive (a key that starts
    with ``_``) is not one. Either raises ``PatchError``.
    """
    model_cls = type(model)
    fields = model_cls.model_fields
    names = model_cls._alias_map()  # pyright: ignore[reportPrivateUsage]

    # (python_name, value) for each patch key.
    updates = [
        (_field_name(names, model_cls.__name__, key, path), value)
        for key, value in patch.items()
    ]
    # Copy the touched fields first so OverrideFlag logic can see what changed.
    before: JsonObject = {
        name: copy.deepcopy(_field_value(model, name)) for name, _ in updates
    }

    for name, value in updates:
        _merge_field(model, name, fields[name], value, resolvers, path.child(name))

    _auto_set_override_flags(model, before)


_DIRECTIVES = ("_op", "_position")


def _field_name(
    names: Mapping[str, str], owner: str | None, key: object, path: Path
) -> str:
    """The python name of the field that the patch key *key* names.

    *names* maps each accepted key to a python name (``_field_names_by_key``).
    *owner* is the name of the model class, for the message. A directive or a
    key that names no field raises ``PatchError`` at ``path.child(key)``.
    """
    text = key if isinstance(key, str) else str(key)
    if isinstance(key, str) and key in names:
        return names[key]
    where = path.child(text)
    if text in _DIRECTIVES:
        raise PatchError(f"{text} is a directive for a list item only", where)
    if text.startswith("_"):
        raise PatchError(
            f"unknown directive {text!r}: the only directives are "
            + f"{' and '.join(_DIRECTIVES)}, on a list item",
            where,
        )
    message = f"{owner} has no field {text!r}" if owner else f"no field {text!r}"
    close = list(
        dict.fromkeys(names[k] for k in difflib.get_close_matches(text, names))
    )
    if close:
        message += f"; did you mean {', '.join(close)}?"
    raise PatchError(message, where)


def _merge_field(
    model: VcoModel,
    name: str,
    field_info: FieldInfo,
    value: object,
    resolvers: IdentityResolver | None,
    path: Path,
) -> None:
    """Merge one patch value into ``model.<name>``. See ``VcoModel.merge_patch``.

    *path* is where the value is in the patch. Every value assigned is first
    validated (``_validate_field``).
    """
    model_cls = type(model)

    # null unsets: MISSING if the field type allows it, else None if it allows that.
    if value is None:
        if _field_accepts_missing(field_info):
            value = MISSING
        elif not _field_accepts_none(field_info):
            raise PatchError(
                f"null cannot unset {name}: its type takes neither MISSING nor None",
                path,
            )
        setattr(model, name, _validate_field(model_cls, name, value, path))
        return

    if _is_dict(value):
        current = _field_value(model, name)

        # Sub-model that already exists: merge into it.
        if isinstance(current, VcoModel):
            _merge_model(current, value, resolvers, path)
            return

        # Sub-model that is unset: build a new one from the patch.
        model_type = _field_model_type(field_info)
        if model_type is not None and (current is MISSING or current is None):
            built = _new_model_from_patch(model_type, value, resolvers, path)
            setattr(model, name, _validate_field(model_cls, name, built, path))
            return

        # KeyField list: dict keys pick list items by key path.
        if get_key_field(field_info) is not None:
            _merge_keyed_list(model, name, field_info, value, resolvers, path)
            return

        # Plain list[VcoModel] given as a dict: its values become the new list,
        # so the field never ends up holding a dict.
        if _list_item_type(field_info) is not None:
            value = list(value.values())

    # Resolvable: a name given where the field expects an ID.
    if isinstance(value, str) and resolvers is not None:
        value = _resolve_name(field_info, value, resolvers)

    setattr(model, name, _validate_field(model_cls, name, value, path))


@functools.cache
def _field_adapter(model_cls: type[VcoModel], name: str) -> TypeAdapter[object]:
    """A validator for field *name* of *model_cls*, as pydantic validates it on parse."""
    fi = model_cls.model_fields[name]
    metadata: list[object] = list(fi.metadata)
    if isinstance(fi.discriminator, str):
        metadata.append(Discriminator(fi.discriminator))
    elif fi.discriminator is not None:
        metadata.append(fi.discriminator)
    annotation: object = (
        Annotated[fi.annotation, *metadata] if metadata else fi.annotation
    )
    return TypeAdapter[object](annotation)  # pyright: ignore[reportArgumentType]


def _validate_field(
    model_cls: type[VcoModel], name: str, value: object, path: Path
) -> object:
    """*value* validated for field *name* of *model_cls*, with the patch context.

    The patch context (``{"patch": True}``) stops ``DefaultingScalar`` and
    ``DefaultingModel`` from turning a bad value into the default. A value that
    fails raises ``PatchError`` at *path*, from the ``ValidationError``.
    """
    try:
        return _field_adapter(model_cls, name).validate_python(
            value, context={"patch": True}
        )
    except ValidationError as exc:
        raise PatchError(_validation_message(exc), path) from exc


def _validation_message(exc: ValidationError) -> str:
    """What a ``ValidationError`` says, one line per error, for a ``PatchError``."""
    lines: list[str] = []
    for error in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in error["loc"])
        lines.append(f"{loc}: {error['msg']}" if loc else error["msg"])
    return "invalid value: " + "; ".join(lines)


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
        data = _build_input(model_type.model_fields, patch, resolvers, path)
        try:
            return model_type.model_validate(data, context={"patch": True})
        except ValidationError as exc:
            raise PatchError(_validation_message(exc), path) from exc
    _merge_model(instance, patch, resolvers, path)
    return instance


def _resolve_name(
    field_info: FieldInfo, name: str, resolvers: IdentityResolver
) -> object:
    """Turn the string *name* on a Resolvable field into the field's ID.

    Returns *name* unchanged if the field is not Resolvable. Otherwise, in order:

    1. If the field's ID type is not ``str`` and *name* parses as it (a UUID
       string on a UUID field), that value is the ID.
    2. An exact or case-insensitive name match gives the identity's ``id_attr``.
    3. If the ID type is ``str`` and *name* equals a known ID, *name* is the ID.
    4. Otherwise the ``ResolutionError`` propagates.

    A missing resolver raises a ``LookupError`` that is not a ``ResolutionError``.
    """
    resolvable = get_resolvable(field_info)
    if resolvable is None:
        return name
    id_type = _id_type(field_info)
    if id_type is not None and id_type is not str:
        try:
            return _id_adapter(id_type).validate_strings(name)
        except ValidationError:
            pass
    try:
        identity = resolvers.resolve_sync(resolvable.identity_type, name)
    except ResolutionError:
        if id_type is str and resolvers.has_id_sync(
            resolvable.identity_type, resolvable.id_attr, name
        ):
            return name
        raise
    return _field_value(identity, resolvable.id_attr)


def _resolve_key_path(obj: object, key_path: str) -> object:
    """Navigate a dotted path on an object (e.g. 'segment.name')."""
    current = obj
    for part in key_path.split("."):
        current = _field_value(current, part)
    return current


def _find_by_key(items: Sequence[object], key_path: str, key: str) -> int | None:
    """Get the index of the first item whose value at *key_path* equals *key* (as strings)."""
    for idx, item in enumerate(items):
        if str(_resolve_key_path(item, key_path)) == key:
            return idx
    return None


def _resolve_position(items: Sequence[object], key_path: str, position: object) -> int:
    """Turn a ``_position`` directive into a list index.

    Accepts an int (clamped to the list), ``"before:<key>"`` or
    ``"after:<key>"``. Anything else, or an anchor key that isn't found,
    means append.
    """
    if isinstance(position, int):
        return max(0, min(position, len(items)))
    if isinstance(position, str):
        anchor, sep, key = position.partition(":")
        if sep and anchor in ("before", "after"):
            idx = _find_by_key(items, key_path, key)
            if idx is not None:
                return idx if anchor == "before" else idx + 1
    return len(items)


def _merge_keyed_list(
    model: VcoModel,
    field_name: str,
    field_info: FieldInfo,
    patch: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
) -> None:
    """Merge a ``{key: item_patch}`` dict into a KeyField list, changing the list in place.

    Each dict key is compared with the value at the field's key path on the
    existing items. When the key path ends at a ``NameOf`` field and there is a
    resolver, a key that matches no item is first resolved, and the name the
    resolver has for it (it matches ignoring case) takes its place: ``guest``
    edits the ``Guest`` item, or adds ``Guest``. An unknown name raises
    ``ResolutionError``.
    - Matched + dict value: recursive merge into matched item.
    - Matched + None: delete item from list.
    - Unmatched + dict value: build a new item and insert it (appended unless
      it has a ``_position``).
    - A ``_position`` on a matched item moves it. Any other directive raises.

    *path* is where the list is in the patch; an item's is ``path[key]``.
    """
    key_path = get_key_field(field_info)
    if key_path is None:
        raise PatchError(f"{field_name} is not a KeyField list", path)

    current = _field_value(model, field_name)
    was_unset = current is MISSING or current is None
    if was_unset:
        items: list[object] = []
    elif _is_list(current):
        items = current
    else:
        raise PatchError(
            f"KeyField {field_name} holds {type(current).__name__}, not a list", path
        )

    item_type = _list_item_type(field_info)
    name_identity = _key_name_of(item_type, key_path)

    for patch_key, item_patch in patch.items():
        key = str(patch_key)
        item_path = path.child(Key(key))
        idx = _find_by_key(items, key_path, key)
        if idx is None and name_identity is not None and resolvers is not None:
            key = _canonical_name(name_identity, key, resolvers)
            idx = _find_by_key(items, key_path, key)

        if item_patch is None:
            if idx is not None:
                del items[idx]
            continue
        if not _is_dict(item_patch):
            continue

        position = item_patch.get("_position")
        fields_patch = {k: v for k, v in item_patch.items() if k != "_position"}

        if idx is not None:
            existing = items[idx]
            if not isinstance(existing, VcoModel):
                raise PatchError(
                    f"KeyField {field_name} item {key!r} is "
                    + f"{type(existing).__name__}, not a VcoModel",
                    item_path,
                )
            _merge_model(existing, fields_patch, resolvers, item_path)
            if position is not None:
                del items[idx]
                items.insert(_resolve_position(items, key_path, position), existing)
            continue

        if item_type is None:
            continue  # can't build an item without a known model type
        new_item = _new_keyed_item(
            model, item_type, fields_patch, key_path, key, resolvers, item_path
        )
        items.insert(_resolve_position(items, key_path, position), new_item)

    if was_unset and items:
        setattr(
            model, field_name, _validate_field(type(model), field_name, items, path)
        )


def _key_name_of(item_type: _ListItemType | None, key_path: str) -> type[object] | None:
    """The identity type that the field at *key_path* of the list's items names (``NameOf``), or None."""
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
        identity = resolvers.resolve_sync(identity_type, name)
    except ResolutionError:
        raise
    except LookupError:
        return name
    canonical: object = getattr(identity, "name", None)
    return canonical if isinstance(canonical, str) else name


def _require_ids_for_name(
    item: VcoModel, identity_type: type[object] | None, key_path: str, key: str
) -> None:
    """Refuse *item*, named *key*, if the IDs of that name were not set on it.

    *identity_type* is what the field at *key_path* is the ``NameOf``. The IDs
    are the ``Resolvable`` fields for that type next to it. One that was neither
    in the patch nor filled from the name is still at its default.
    """
    if identity_type is None:
        return
    owner: object = item
    for part in key_path.split(".")[:-1]:
        owner = _field_value(owner, part)
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
            + "resolver or profile entry has it, and the patch does not give "
            + f"{', '.join(unset)}"
        )


def _new_keyed_item(
    parent: VcoModel,
    item_type: _ListItemType,
    patch: Mapping[object, object],
    key_path: str,
    key: str,
    resolvers: IdentityResolver | None,
    path: Path,
) -> VcoModel:
    """Build a new KeyField list item from its patch, with the key value written in.

    *path* is where the item is in the patch. A patch that does not build a
    valid item raises ``PatchError``. Raises ``LookupError`` (not
    ``ResolutionError``) if the key is a name (``NameOf``) and the IDs that go
    with it were neither in the patch nor found by a resolver: the item would
    carry the model's defaults, which are another item's IDs.
    """
    item_patch = _inject_key_path(patch, key_path, key)
    item_input = _build_input(item_type.fields, item_patch, resolvers, path)
    try:
        item = item_type.validate(item_input, context={"patch": True})
    except ValidationError as exc:
        raise PatchError(_validation_message(exc), path) from exc
    _require_ids_for_name(item, _key_name_of(item_type, key_path), key_path, key)
    # Give the item the parent's config level so context defaults resolve
    # for the right level (EDGE vs PROFILE).
    if parent.config_context is not None:
        _ = item.for_context(parent.config_context)
    _fill_context_defaults(item)
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
        if _field_value(item, name) is not MISSING:
            continue
        default = _effective_default(field_info, level)
        if (
            default.source is _DefaultSource.CONTEXT
            and default.value is not MISSING
            and default.value is not PydanticUndefined
        ):
            setattr(item, name, default.value)


def _inject_key_path(
    patch: Mapping[object, object], key_path: str, key_value: str
) -> dict[object, object]:
    """Return a copy of *patch* with *key_value* set at the dotted *key_path*.

    For ``"name"``, sets ``patch["name"]`` unless the patch already has it.
    For ``"segment.name"``, makes ``patch["segment"] = {**existing, "name": key_value}``,
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
        child: dict[object, object] = dict(existing) if _is_dict(existing) else {}
        node[part] = child
        node = child
    node[leaf] = key_value
    return result


def _input_keys(field_info: FieldInfo, field_name: str) -> tuple[str, ...]:
    """Patch keys that may hold this field: the python name, plus the string alias if there is one."""
    alias = field_info.validation_alias or field_info.alias
    if isinstance(alias, str) and alias != field_name:
        return (field_name, alias)
    return (field_name,)


def _build_input(
    fields: Mapping[str, FieldInfo],
    patch: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
) -> JsonObject:
    """Turn a patch that pydantic will validate whole into its input, checked.

    For what the patch builds instead of merging into (a new keyed-list item,
    a sub-model with a required field), so ``model_validate`` sees what
    ``merge_patch`` would have made of it. *fields* are the target model's
    fields and *path* is where *patch* is in the patch. This function walks
    *patch* against *fields* and:

    - raises ``PatchError`` for a key that names no field and for a directive
    - writes a key that pydantic does not read (the camelCase form of a field
      with another alias) as the python name
    - resolves string values on Resolvable fields the way ``merge_patch``
      does (see ``_resolve_name``): an unknown name raises ``ResolutionError``
      and a missing resolver leaves the value for pydantic
    - turns KeyField dict patches into ordered item lists
    - recurses into list items and sub-model dicts
    - fills Resolvable fields that are missing, but only from a sibling field
      marked ``NameOf`` for the same identity type that holds a string (a key
      ``segment.name`` written in by ``_inject_key_path`` gives
      ``segment_id`` / ``segment_logical_id``). An unknown name raises
      ``ResolutionError`` and a missing resolver skips the fill.

    With no *resolvers*, names are left as they are.
    """
    names = _field_names_by_key(fields)
    built: JsonObject = {}

    for key, value in patch.items():
        field_name = _field_name(names, None, key, path)
        fi = fields[field_name]
        child = path.child(field_name)
        if isinstance(value, str):
            if resolvers is not None:
                try:
                    value = _resolve_name(fi, value, resolvers)
                except ResolutionError:
                    raise
                except LookupError:
                    pass  # no resolver: leave it for pydantic to accept or reject
        elif _is_dict(value) and (key_path := get_key_field(fi)):
            value = _keyed_patch_to_list(
                value, key_path, _list_item_type(fi), resolvers, child
            )
        elif _is_list(value):
            if (item_type := _list_item_type(fi)) is not None:
                value = [
                    _build_input(item_type.fields, item, resolvers, child.child(i))
                    if _is_dict(item)
                    else item
                    for i, item in enumerate(value)
                ]
        elif _is_dict(value) and (sub_type := _field_model_type(fi)):
            value = _build_input(sub_type.model_fields, value, resolvers, child)
        input_key = key if key in _input_keys(fi, field_name) else field_name
        built[str(input_key)] = value  # a str: _field_name refuses other keys

    if resolvers is not None:
        _fill_from_names(fields, built, resolvers)
    return built


def _fill_from_names(
    fields: Mapping[str, FieldInfo], resolved: JsonObject, resolvers: IdentityResolver
) -> None:
    """Fill, in *resolved*, the Resolvable fields it lacks from a ``NameOf`` sibling it has."""
    for field_name, fi in fields.items():
        identity_type: type[object] | None = get_name_of(fi)
        if identity_type is None:
            continue
        name = next(
            (resolved[key] for key in _input_keys(fi, field_name) if key in resolved),
            None,
        )
        if not isinstance(name, str):
            continue
        targets = [
            (target_name, resolvable)
            for target_name, target_fi in fields.items()
            if (resolvable := get_resolvable(target_fi)) is not None
            and resolvable.identity_type is identity_type
            and not any(key in resolved for key in _input_keys(target_fi, target_name))
        ]
        if not targets:
            continue
        try:
            identity = resolvers.resolve_sync(identity_type, name)
        except ResolutionError:
            raise
        except LookupError:
            continue  # no resolver: nothing to fill from
        for target_name, resolvable in targets:
            resolved[target_name] = _field_value(identity, resolvable.id_attr)


def _keyed_patch_to_list(
    patch: Mapping[object, object],
    key_path: str,
    item_type: _ListItemType | None,
    resolvers: IdentityResolver | None,
    path: Path,
) -> list[object]:
    """Turn a KeyField ``{key: item_patch}`` dict into a list of item dicts.

    Items are ordered by their numeric ``_position``. Items without one go last.
    *path* is where the list is in the patch.
    """
    items: list[tuple[float, object]] = []
    for patch_key, item_patch in patch.items():
        if not _is_dict(item_patch):
            continue
        key = str(patch_key)
        fields_patch = {k: v for k, v in item_patch.items() if k != "_position"}
        item = _inject_key_path(fields_patch, key_path, key)
        built: object = (
            item
            if item_type is None
            else _build_input(item_type.fields, item, resolvers, path.child(Key(key)))
        )
        items.append((_sort_position(item_patch.get("_position")), built))
    items.sort(key=lambda entry: entry[0])
    return [item for _, item in items]


def _sort_position(position: object) -> float:
    """A ``_position`` as a sort key: a number as it is, anything else last."""
    return position if isinstance(position, int | float) else math.inf


def _auto_set_override_flags(model: VcoModel, before: Mapping[str, object]) -> None:
    """Set OverrideFlag fields to True if a non-EdgeLocal field changed."""
    fields = type(model).model_fields
    flag_names = [name for name, fi in fields.items() if has_override_flag(fi)]
    if flag_names and _has_non_edge_local_changes(model, before):
        for name in flag_names:
            setattr(model, name, True)


def _has_non_edge_local_changes(model: VcoModel, before: Mapping[str, object]) -> bool:
    """Check whether a field in *before* (field name -> old value) now differs.

    OverrideFlag and EdgeLocal fields don't count. When both old and new
    values are the same VcoModel type, the check recurses into them, so a
    change confined to EdgeLocal descendants doesn't count either.
    """
    fields = type(model).model_fields
    for name, old in before.items():
        fi = fields.get(name)
        if fi is None or has_override_flag(fi) or is_edge_local(fi):
            continue
        new = _field_value(model, name)
        if isinstance(new, VcoModel) and type(new) is type(old):
            if _has_non_edge_local_changes(new, _all_field_values(new, old)):
                return True
        elif new != old:
            return True
    return False


def _all_field_values(model: VcoModel, source: object) -> JsonObject:
    """Read every one of *model*'s fields from *source*, an instance of the same class."""
    return {name: _field_value(source, name) for name in type(model).model_fields}
