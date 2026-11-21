"""The merge engine behind ``VcoModel.merge_patch``.

``apply_patch`` merges a partial dict into a model; everything private here
supports it. ``VcoModel.merge_patch`` in ``base.py`` is a thin call into it.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence

from pydantic import MISSING, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .base import (
    IdentityResolver,
    JsonObject,
    VcoModel,
    _DefaultSource,  # pyright: ignore[reportPrivateUsage]
    _effective_default,  # pyright: ignore[reportPrivateUsage]
    _field_accepts_missing,  # pyright: ignore[reportPrivateUsage]
    _field_model_type,  # pyright: ignore[reportPrivateUsage]
    _field_value,  # pyright: ignore[reportPrivateUsage]
    _id_adapter,  # pyright: ignore[reportPrivateUsage]
    _id_type,  # pyright: ignore[reportPrivateUsage]
    _is_json_object,  # pyright: ignore[reportPrivateUsage]
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


def apply_patch(
    model: VcoModel, patch: Mapping[str, object], resolvers: IdentityResolver | None
) -> VcoModel:
    """Recursively merge a partial dict into *model*, in place. See ``VcoModel.merge_patch``."""
    fields = type(model).model_fields
    alias_map = type(model)._alias_map()  # pyright: ignore[reportPrivateUsage]

    # (python_name, value) for each patch key that names a field here.
    # Directives and unknown keys are dropped.
    updates = [
        (alias_map[key], value)
        for key, value in patch.items()
        if not key.startswith("_") and key in alias_map
    ]
    # Copy the touched fields first so OverrideFlag logic can see what changed.
    before: JsonObject = {
        name: copy.deepcopy(_field_value(model, name)) for name, _ in updates
    }

    for name, value in updates:
        _merge_field(model, name, fields[name], value, resolvers)

    _auto_set_override_flags(model, before)
    return model


# ---------------------------------------------------------------------------
# merge_patch internals
# ---------------------------------------------------------------------------


def _merge_field(
    model: VcoModel,
    name: str,
    field_info: FieldInfo,
    value: object,
    resolvers: IdentityResolver | None,
) -> None:
    """Merge one patch value into ``model.<name>``. See ``VcoModel.merge_patch``."""
    # null deletes: MISSING if the field type allows it, otherwise None.
    if value is None:
        setattr(model, name, MISSING if _field_accepts_missing(field_info) else None)
        return

    if _is_json_object(value):
        current = _field_value(model, name)

        # Sub-model that already exists: merge into it.
        if isinstance(current, VcoModel):
            _ = current.merge_patch(value, resolvers=resolvers)
            return

        # Sub-model that is unset: build a new one from the patch.
        model_type = _field_model_type(field_info)
        if model_type is not None and (current is MISSING or current is None):
            setattr(model, name, _new_model_from_patch(model_type, value, resolvers))
            return

        # KeyField list: dict keys pick list items by key path.
        if get_key_field(field_info) is not None:
            _merge_keyed_list(model, name, field_info, value, resolvers)
            return

        # Plain list[VcoModel] given as a dict: its values become the new list,
        # so the field never ends up holding a dict.
        if _list_item_type(field_info) is not None:
            value = list(value.values())

    # Resolvable: a name given where the field expects an ID.
    if isinstance(value, str) and resolvers is not None:
        value = _resolve_name(field_info, value, resolvers)

    # list[VcoModel]: validate items given as dicts.
    if _is_list(value) and (item_type := _list_item_type(field_info)) is not None:
        value = [item_type.validate(v) if _is_json_object(v) else v for v in value]

    setattr(model, name, value)


def _new_model_from_patch(
    model_type: type[VcoModel], patch: JsonObject, resolvers: IdentityResolver | None
) -> VcoModel:
    """Build a sub-model from a patch.

    Prefers default-construct-then-merge, so KeyField and Resolvable handling
    applies to the nested patch (``model_validate`` would skip it). Models
    with required fields can't be built from defaults, so those fall back to
    ``model_validate``.
    """
    try:
        instance = model_type()
    except ValidationError:
        return model_type.model_validate(patch)
    return instance.merge_patch(patch, resolvers=resolvers)


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


def _strip_directives(patch: Mapping[str, object]) -> JsonObject:
    return {k: v for k, v in patch.items() if not k.startswith("_")}


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
    patch: JsonObject,
    resolvers: IdentityResolver | None,
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
    - A ``_position`` on a matched item moves it.
    """
    key_path = get_key_field(field_info)
    if key_path is None:
        raise ValueError(f"KeyField key_path is None for field '{field_name}'")

    current = _field_value(model, field_name)
    was_unset = current is MISSING or current is None
    if was_unset:
        items: list[object] = []
    elif _is_list(current):
        items = current
    else:
        raise TypeError(
            f"KeyField '{field_name}' holds {type(current).__name__}, not a list"
        )

    item_type = _list_item_type(field_info)
    name_identity = _key_name_of(item_type, key_path)

    for patch_key, item_patch in patch.items():
        key = patch_key
        idx = _find_by_key(items, key_path, key)
        if idx is None and name_identity is not None and resolvers is not None:
            key = _canonical_name(name_identity, key, resolvers)
            idx = _find_by_key(items, key_path, key)

        if item_patch is None:
            if idx is not None:
                del items[idx]
            continue
        if not _is_json_object(item_patch):
            continue

        position = item_patch.get("_position")
        fields_patch = _strip_directives(item_patch)

        if idx is not None:
            existing = items[idx]
            if not isinstance(existing, VcoModel):
                raise TypeError(
                    f"KeyField '{field_name}' item {key!r} is "
                    + f"{type(existing).__name__}, not a VcoModel"
                )
            _ = existing.merge_patch(fields_patch, resolvers=resolvers)
            if position is not None:
                del items[idx]
                items.insert(_resolve_position(items, key_path, position), existing)
            continue

        if item_type is None:
            continue  # can't build an item without a known model type
        new_item = _new_keyed_item(
            model, item_type, fields_patch, key_path, key, resolvers
        )
        items.insert(_resolve_position(items, key_path, position), new_item)

    if was_unset and items:
        setattr(model, field_name, items)


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
    patch: JsonObject,
    key_path: str,
    key: str,
    resolvers: IdentityResolver | None,
) -> VcoModel:
    """Build a new KeyField list item from its patch, with the key value written in.

    Raises ``LookupError`` (not ``ResolutionError``) if the key is a name
    (``NameOf``) and the IDs that go with it were neither in the patch nor found
    by a resolver: the item would carry the model's defaults, which are another
    item's IDs.
    """
    item_patch = _inject_key_path(patch, key_path, key)
    if resolvers is not None:
        item_patch = _pre_resolve(item_type.fields, item_patch, resolvers)
    item = item_type.validate(item_patch)
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
    patch: Mapping[str, object], key_path: str, key_value: str
) -> JsonObject:
    """Return a copy of *patch* with *key_value* set at the dotted *key_path*.

    For ``"name"``, sets ``patch["name"]`` unless the patch already has it.
    For ``"segment.name"``, makes ``patch["segment"] = {**existing, "name": key_value}``,
    keeping sibling keys and overriding any existing name.
    """
    *parents, leaf = key_path.split(".")
    result: JsonObject = dict(patch)
    if not parents:
        _ = result.setdefault(leaf, key_value)
        return result

    node = result
    for part in parents:
        existing = node.get(part)
        child: JsonObject = dict(existing) if _is_json_object(existing) else {}
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


def _pre_resolve(
    fields: Mapping[str, FieldInfo],
    patch: Mapping[str, object],
    resolvers: IdentityResolver,
) -> JsonObject:
    """Resolve names to IDs in a patch that will go to ``model_validate`` instead of ``merge_patch``.

    New keyed-list items are built with ``model_validate``, so Resolvable
    fields (e.g. ``segment_id: "Global Segment"``) would reach pydantic as
    raw strings. This function walks *patch* against *fields* (the target
    model's fields) and:

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
    """
    resolved: JsonObject = dict(patch)

    for field_name, fi in fields.items():
        for key in _input_keys(fi, field_name):
            if key not in resolved:
                continue
            value = resolved[key]
            if isinstance(value, str):
                try:
                    resolved[key] = _resolve_name(fi, value, resolvers)
                except ResolutionError:
                    raise
                except LookupError:
                    pass  # no resolver: leave it for pydantic to accept or reject
            elif _is_json_object(value) and (key_path := get_key_field(fi)):
                resolved[key] = _keyed_patch_to_list(
                    value, key_path, _list_item_type(fi), resolvers
                )
            elif _is_list(value):
                if (item_type := _list_item_type(fi)) is not None:
                    resolved[key] = [
                        _pre_resolve(item_type.fields, item, resolvers)
                        if _is_json_object(item)
                        else item
                        for item in value
                    ]
            elif _is_json_object(value) and (sub_type := _field_model_type(fi)):
                resolved[key] = _pre_resolve(sub_type.model_fields, value, resolvers)

    _fill_from_names(fields, resolved, resolvers)
    return resolved


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
    patch: JsonObject,
    key_path: str,
    item_type: _ListItemType | None,
    resolvers: IdentityResolver,
) -> list[JsonObject]:
    """Turn a KeyField ``{key: item_patch}`` dict into a list of item dicts.

    Items are ordered by their numeric ``_position``. Items without one go last.
    """
    items: list[JsonObject] = []
    for key, item_patch in patch.items():
        if not _is_json_object(item_patch):
            continue
        item = _inject_key_path(item_patch, key_path, key)
        if item_type is not None:
            item = _pre_resolve(item_type.fields, item, resolvers)
        items.append(item)
    items.sort(key=_pop_sort_position)
    return items


def _pop_sort_position(item: JsonObject) -> float:
    """Remove ``_position`` from *item* and return it as a sort key."""
    position = item.pop("_position", None)
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
