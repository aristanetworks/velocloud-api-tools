"""The merge engine behind ``VcoModel.merge_patch``.

``apply_patch`` merges a partial dict into a model; everything private here
supports it. ``VcoModel.merge_patch`` in ``base.py`` is a thin call into it.
"""

from __future__ import annotations

import copy
import difflib
import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, get_args, get_origin

from pydantic import (
    MISSING,
    AfterValidator,
    BeforeValidator,
    Discriminator,
    PlainValidator,
    PydanticUserError,
    TypeAdapter,
    ValidationError,
    WrapValidator,
)
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .base import (
    IdentityResolver,
    JsonObject,
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
    _id_adapter,  # pyright: ignore[reportPrivateUsage]
    _id_type,  # pyright: ignore[reportPrivateUsage]
    _is_dict,  # pyright: ignore[reportPrivateUsage]
    _is_list,  # pyright: ignore[reportPrivateUsage]
    _is_vco_model_type,  # pyright: ignore[reportPrivateUsage]
    _list_item_type,  # pyright: ignore[reportPrivateUsage]
    _ListItemType,  # pyright: ignore[reportPrivateUsage]
    _merged_fields,  # pyright: ignore[reportPrivateUsage]
    _union_members,  # pyright: ignore[reportPrivateUsage]
    _unwrap_annotated,  # pyright: ignore[reportPrivateUsage]
    _variant_names,  # pyright: ignore[reportPrivateUsage]
    list_kind,
)
from .fields import (
    ConfigContext,
    get_key_field,
    get_name_of,
    get_resolvable,
    has_override_flag,
    is_edge_local,
)
from .identity import ResolutionError
from .patch import ItemOp, PatchError, Place, is_new, parse_item_op, position_of
from .rebase import Key, Path, PathPart


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


def _field_name(names: Mapping[str, str], owner: str, key: object, path: Path) -> str:
    """The python name of the field that the patch key *key* names.

    *names* maps each accepted key to a python name (``_field_names_by_key``).
    *owner* is the name of the model class, for the message. A directive or a
    key that names no field raises ``PatchError`` at ``path.child(key)``. Both
    ``_merge_model`` and ``_build_input`` check keys here, so their messages
    are the same.
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
    message = f"{owner} has no field {text!r}"
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

    # A list, or a map of models: a list value, or item operations.
    if _list_spec(field_info) is not None:
        _merge_list(model, name, field_info, value, resolvers, path)
        return

    if _is_dict(value):
        current = _field_value(model, name)

        # Sub-model that already exists: merge into it.
        if isinstance(current, VcoModel):
            _merge_model(current, value, resolvers, path)
            return

        # Sub-model that is unset: build a new one from the patch.
        model_types = _field_model_types(field_info)
        if model_types and (current is MISSING or current is None):
            if len(model_types) == 1:
                built = _new_model_from_patch(model_types[0], value, resolvers, path)
            else:
                built = _new_variant_from_patch(model_cls, name, value, resolvers, path)
            setattr(model, name, _validate_field(model_cls, name, built, path))
            return

    # Resolvable: a name given where the field expects an ID.
    if isinstance(value, str) and resolvers is not None:
        value = _resolve_name(field_info, value, resolvers)

    setattr(model, name, _validate_field(model_cls, name, value, path))


_FIELD_VALIDATOR_MARKERS = {
    "before": BeforeValidator,
    "after": AfterValidator,
    "wrap": WrapValidator,
    "plain": PlainValidator,
}


@functools.cache
def _field_adapter(model_cls: type[VcoModel], name: str) -> TypeAdapter[object]:
    """A validator for field *name* of *model_cls*, as pydantic validates it on parse.

    The field's annotation and metadata, its discriminator, the class's
    ``@field_validator``s for the field (``"*"`` ones too), applied after the
    metadata in the order defined, as pydantic does, and the class's config
    (``use_enum_values`` and the rest). Model validators are not field
    validation and do not run.
    """
    fi = model_cls.model_fields[name]
    metadata: list[object] = list(fi.metadata)
    if isinstance(fi.discriminator, str):
        metadata.append(Discriminator(fi.discriminator))
    elif fi.discriminator is not None:
        metadata.append(fi.discriminator)
    for decorator in model_cls.__pydantic_decorators__.field_validators.values():
        info = decorator.info
        if name in info.fields or "*" in info.fields:
            metadata.append(_FIELD_VALIDATOR_MARKERS[info.mode](decorator.func))
    annotation: object = (
        Annotated[fi.annotation, *metadata] if metadata else fi.annotation
    )
    try:
        return TypeAdapter[object](annotation, config=model_cls.model_config)  # pyright: ignore[reportArgumentType]
    except PydanticUserError as exc:
        # A model type has its own config, and TypeAdapter refuses another.
        if exc.code != "type-adapter-config-unused":
            raise
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
        data = _build_input(
            model_type.model_fields, patch, resolvers, path, owner=model_type.__name__
        )
        try:
            built = model_type.model_validate(data, context={"patch": True})
        except ValidationError as exc:
            raise PatchError(_validation_message(exc), path) from exc
        _refuse_unread_keys(built, data, path)
        return built
    _merge_model(instance, patch, resolvers, path)
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
    """Raise ``PatchError`` for a key of *data* that *model*, validated from it, has no field for.

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
        current = _field_value(model, name)
        if _is_dict(value) and isinstance(current, VcoModel):
            _refuse_unread_keys(current, value, child)
        elif (
            _is_list(value)
            and _is_list(current)
            and (spec := _list_spec(model_cls.model_fields[name])) is not None
        ):
            _check_list_value(spec, value, current, child)


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


def _new_item(
    item_type: _ListItemType,
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
    try:
        item = item_type.validate(item_input, context={"patch": True})
    except ValidationError as exc:
        raise PatchError(_validation_message(exc), path) from exc
    _refuse_unread_keys(item, item_input, path)
    if context is not None:
        _ = item.for_context(context)
        _fill_context_defaults(item)
    return item


def _new_keyed_item(
    spec: _ListSpec,
    item_type: _ListItemType,
    body: Mapping[object, object],
    key: str,
    resolvers: IdentityResolver | None,
    path: Path,
    context: ConfigContext | None,
) -> VcoModel:
    """Build a new keyed-list item from the body of an ``add``, with *key* written in.

    A body that gives another key raises ``PatchError``. Raises
    ``LookupError`` (not ``ResolutionError``) if the key is a name
    (``NameOf``) and the IDs that go with it were neither in the patch nor
    found by a resolver: the item would carry the model's defaults, which are
    another item's IDs. See ``_new_item``.
    """
    assert spec.key_path is not None  # a keyed list
    given = _patch_key(item_type.fields, body, spec.key_path)
    if given is not None and given != key:
        raise PatchError(
            f"the body gives the key {given!r}, and the selector {key!r}: "
            + "add() takes the key from its selector",
            path,
        )
    item = _new_item(
        item_type,
        _inject_key_path(body, spec.key_path, key),
        resolvers,
        path,
        context,
    )
    _require_ids_for_name(item, spec.name_identity, spec.key_path, key)
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

    - raises ``PatchError`` for a key that names no field and for a directive
    - writes a key that pydantic does not read (the camelCase form of a field
      with another alias) as the python name
    - resolves string values on Resolvable fields the way ``merge_patch``
      does (see ``_resolve_name``): an unknown name raises ``ResolutionError``
      and a missing resolver leaves the value for pydantic
    - builds a list or map field given as an object from its item
      operations, on an empty list: each must be an ``add``
      (``_new_list_input``)
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
        field_name = _field_name(names, owner, key, path)
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
        elif (_is_list(value) or _is_dict(value)) and (
            spec := _list_spec(fi)
        ) is not None:
            value = _new_list_input(spec, value, resolvers, child)
        elif _is_dict(value) and (sub_types := _field_model_types(fi)):
            value = _build_input(
                _merged_fields(sub_types),
                value,
                resolvers,
                child,
                owner=_variant_names(sub_types),
            )
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


# ---------------------------------------------------------------------------
# Lists and maps: list values and item operations (spec 5.10.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _ListSpec:
    """How the items of a list field, or the values of a map field, are named and built."""

    kind: ListKind
    key_path: str | None
    """The ``KeyField`` path of a keyed list."""
    item_type: _ListItemType | None
    """The model type of the items of a list of models."""
    element: TypeAdapter[object] | None
    """Validates one entry of a list that is not of models."""
    map_type: type[VcoModel] | None
    """The model type of the values of a map, when it has one."""
    name_identity: type[object] | None
    """What the key of a keyed list is the ``NameOf``, if it is one."""


@functools.cache
def _list_spec(field_info: FieldInfo) -> _ListSpec | None:
    """The ``_ListSpec`` of a list or map field (``list_kind``), or None for any other field."""
    kind = list_kind(field_info)
    if kind is None:
        return None
    key_path = get_key_field(field_info) if kind == "keyed" else None
    item_type = _list_item_type(field_info)
    element: TypeAdapter[object] | None = None
    map_type: type[VcoModel] | None = None
    if kind == "map":
        value_types = [
            m
            for m in _members_of(_type_args(field_info, dict)[1])
            if _is_vco_model_type(m)
        ]
        map_type = value_types[0] if len(value_types) == 1 else None
    elif item_type is None:
        element = TypeAdapter[object](_type_args(field_info, list)[0])  # pyright: ignore[reportArgumentType]
    return _ListSpec(
        kind,
        key_path,
        item_type,
        element,
        map_type,
        None if key_path is None else _key_name_of(item_type, key_path),
    )


def _type_args(field_info: FieldInfo, origin: type) -> tuple[object, ...]:
    """The arguments of the ``origin[...]`` member of the field's annotation (``list`` or ``dict``)."""
    for member in _members_of(field_info.annotation):
        if get_origin(member) is origin and (args := get_args(member)):
            return args
    return (object, object)


def _members_of(annotation: object) -> list[object]:
    """The members of a union annotation, each without ``Annotated``."""
    return [_unwrap_annotated(m) for m in _union_members(_unwrap_annotated(annotation))]


def _item_key(item: object, key_path: str) -> str | None:
    """The key of a keyed-list item, in string form; None if it has none (null or MISSING)."""
    value = item
    for part in key_path.split("."):
        if value is None or value is MISSING:
            return None
        value = _field_value(value, part)
    return None if value is None or value is MISSING else str(value)


def _patch_key(
    fields: Mapping[str, FieldInfo], data: Mapping[object, object], key_path: str
) -> str | None:
    """The key that *data*, a patch for a new item with *fields*, gives at *key_path*, in string form.

    None if it gives none. Keys of *data* are read as ``_build_input`` reads
    them (python name, alias or camelCase).
    """
    value: object = data
    current: Mapping[str, FieldInfo] | None = fields
    for part in key_path.split("."):
        if current is None or not _is_dict(value):
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
    return None if value is None or value is MISSING else str(value)


@dataclass(frozen=True)
class _Selection:
    """What a selector names in a list (``_select``)."""

    positions: list[int]
    """The positions of the items it names, in the list as it was."""
    key: str | None
    """In a keyed or value list, the key or value it stands for: as written, or the resolver's name."""
    position: int | None
    """The ``n`` of a ``"#n"`` selector that was taken as a position."""


def _select(
    items: Sequence[object],
    spec: _ListSpec,
    selector: object,
    resolvers: IdentityResolver | None,
) -> _Selection:
    """Resolve *selector* against *items*; the one place a selector is read.

    Tried in order: in a keyed list, an item whose key equals the selector in
    string form; in a value list, an entry equal to it in string form; then
    ``"#n"``, the item at position ``n``; then, in a keyed list whose key is a
    ``NameOf`` field and with *resolvers*, the name the resolver has for the
    selector, as the key once more. An unknown name raises
    ``ResolutionError``. In any other list only ``"#n"`` names an item.
    """
    text = str(selector)
    by_value = spec.kind in ("keyed", "value")
    if by_value and (found := _matching(items, spec, text)):
        return _Selection(found, text, None)
    n = position_of(selector)
    if n is not None:
        return _Selection([n] if n < len(items) else [], None, n)
    if spec.name_identity is not None and resolvers is not None:
        text = _canonical_name(spec.name_identity, text, resolvers)
        return _Selection(_matching(items, spec, text), text, None)
    return _Selection([], text if by_value else None, None)


def _matching(items: Sequence[object], spec: _ListSpec, text: str) -> list[int]:
    """The positions of the items whose key (keyed list) or value (value list) is *text*."""
    if spec.key_path is not None:
        key_path = spec.key_path
        return [i for i, item in enumerate(items) if _item_key(item, key_path) == text]
    return [i for i, item in enumerate(items) if str(item) == text]


def select_items(
    model: VcoModel,
    field_name: str,
    selector: object,
    resolvers: IdentityResolver | None = None,
) -> list[int]:
    """The positions of the items of the list field *field_name* that *selector* names.

    Empty if it names none. A selector is read as in a patch (spec 5.10.3):
    the key of an item of a keyed list, an entry of a value list, ``at(n)``,
    and in a keyed list whose key is a ``NameOf`` field, the resolver's name
    for it. An unknown name raises ``ResolutionError``. Raises ``TypeError``
    if the field is not a list.
    """
    model_cls = type(model)
    spec = _list_spec(model_cls.model_fields[field_name])
    if spec is None or spec.kind == "map":
        raise TypeError(f"{model_cls.__name__}.{field_name} is not a list")
    current = _field_value(model, field_name)
    items: list[object] = current if _is_list(current) else []
    return _select(items, spec, selector, resolvers).positions


def _merge_list(
    model: VcoModel,
    name: str,
    field_info: FieldInfo,
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
    spec = _list_spec(field_info)
    assert spec is not None  # _merge_field checked
    model_cls = type(model)
    if not _is_dict(value):
        if spec.kind == "map":
            raise PatchError(
                f"{name} is a map: give an object of item operations by map key",
                path,
            )
        data = _list_value_input(spec, value, resolvers, path)
        built = _validate_field(model_cls, name, data, path)
        if _is_list(data) and _is_list(built):
            _check_list_value(spec, data, built, path)
        setattr(model, name, built)
        return
    if spec.kind == "untyped":
        raise PatchError(f"{name} is a list of untyped items: give a list value", path)

    current = _field_value(model, name)
    unset = current is MISSING or current is None
    entries: list[object] | dict[object, object]
    if spec.kind == "map":
        if unset:
            entries = {}
        elif _is_dict(current):
            entries = current
        else:
            raise PatchError(f"{name} holds {type(current).__name__}, not a map", path)
        _merge_map(entries, spec, value, resolvers, path, new_only=False)
    else:
        if unset:
            entries = []
        elif _is_list(current):
            entries = current
        else:
            raise PatchError(f"{name} holds {type(current).__name__}, not a list", path)
        _merge_items(
            entries, spec, value, resolvers, path, model.config_context, new_only=False
        )
    if unset and entries:
        setattr(model, name, _validate_field(model_cls, name, entries, path))


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
        if not _is_dict(value):
            raise PatchError(
                "a map takes an object of item operations by map key", path
            )
        entries: dict[object, object] = {}
        _merge_map(entries, spec, value, resolvers, path, new_only=True)
        return entries
    if _is_list(value):
        return _list_value_input(spec, value, resolvers, path)
    if spec.kind == "untyped":
        raise PatchError("a list of untyped items takes a list value", path)
    items: list[object] = []
    _merge_items(items, spec, value, resolvers, path, None, new_only=True)
    return items


def _list_value_input(
    spec: _ListSpec,
    value: object,
    resolvers: IdentityResolver | None,
    path: Path,
) -> object:
    """*value*, a list value for a list of models, with each item object checked by ``_build_input``.

    An item's path is ``path[key]`` in a keyed list when the item gives its
    key, ``path[#n]`` otherwise. Any other value is returned as it is.
    """
    item_type = spec.item_type
    if item_type is None or not _is_list(value):
        return value
    built: list[object] = []
    for index, item in enumerate(value):
        if _is_dict(item):
            key = (
                None
                if spec.key_path is None
                else _patch_key(item_type.fields, item, spec.key_path)
            )
            part: PathPart = index if key is None else Key(key)
            item = _build_input(
                item_type.fields,
                item,
                resolvers,
                path.child(part),
                owner=item_type.name,
            )
        built.append(item)
    return built


def _check_list_value(
    spec: _ListSpec,
    data: Sequence[object],
    built: Sequence[object],
    path: Path,
) -> None:
    """Check *built*, a list validated from the list value *data*, for what validation lets by.

    Each item built from an object refuses keys it has no field for
    (``_refuse_unread_keys``). A keyed list whose items repeat a key raises
    ``PatchError`` at *path*.
    """
    if len(data) == len(built):
        for index, (item_data, item) in enumerate(zip(data, built)):
            if _is_dict(item_data) and isinstance(item, VcoModel):
                _refuse_unread_keys(
                    item, item_data, path.child(_item_part(spec, item, index))
                )
    if spec.key_path is None:
        return
    seen: dict[str, int] = {}
    for index, item in enumerate(built):
        key = _item_key(item, spec.key_path)
        if key is None:
            continue
        if key in seen:
            raise PatchError(
                f"items #{seen[key]} and #{index} have one key, {key!r}: "
                + "the items of a keyed list have distinct keys",
                path,
            )
        seen[key] = index


def _item_part(spec: _ListSpec, item: object, index: int) -> PathPart:
    """The path part of a list item: its key in a keyed list, else its position."""
    key = None if spec.key_path is None else _item_key(item, spec.key_path)
    return index if key is None else Key(key)


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
    """For an add: the key (keyed list) or the validated value (value list) it writes."""
    anchor: _Slot | None
    """The item a ``before`` or ``after`` place names."""


def _merge_items(
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

    1. Every selector and anchor is resolved against the list as it was
       (``_resolve_items``); any error raises here, before anything changes.
    2. Changes, in the order written. Afterwards a renamed item whose key
       another item has raises.
    3. Deletes.
    4. Adds and moves, in the order written; a place is turned into an index
       when its entry is applied. An add with no place goes to the end.

    *context* is the config level of the model that holds the list.
    *new_only* is for a list the patch builds: every entry must be an add.
    """
    slots = [_Slot(item) for item in items]
    entries = _resolve_items(slots, spec, ops, resolvers, path, new_only=new_only)

    renamed: list[_ItemEntry] = []
    for entry in entries:
        if entry.kind != "change" or entry.slot is None:
            continue
        item = entry.slot.value
        if not isinstance(item, VcoModel):
            raise PatchError(
                f"the item is {type(item).__name__}, not a model", entry.path
            )
        before = None if spec.key_path is None else _item_key(item, spec.key_path)
        _merge_model(item, entry.body, resolvers, entry.path)
        if spec.key_path is not None and _item_key(item, spec.key_path) != before:
            renamed.append(entry)
    for entry in renamed:
        assert entry.slot is not None and spec.key_path is not None
        key = _item_key(entry.slot.value, spec.key_path)
        if key is not None and any(
            s is not entry.slot and _item_key(s.value, spec.key_path) == key
            for s in slots
        ):
            raise PatchError(f"another item has the key {key!r}", entry.path)

    deleted = {id(e.slot) for e in entries if e.kind == "delete"}
    slots = [s for s in slots if id(s) not in deleted]

    for entry in entries:
        if entry.kind == "add":
            slot = _Slot(_new_list_item(spec, entry, slots, resolvers, context))
        elif entry.place is not None and entry.slot is not None:
            slot = entry.slot
            slots.remove(slot)
        else:
            continue
        slots.insert(_place_index(slots, entry.place, entry.anchor), slot)

    items[:] = [s.value for s in slots]


def _resolve_items(
    slots: Sequence[_Slot],
    spec: _ListSpec,
    ops: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    *,
    new_only: bool,
) -> list[_ItemEntry]:
    """Parse every entry of *ops* and resolve its selector and anchor (step 1 of ``_merge_items``).

    Raises ``PatchError`` for an operation the list does not take, a selector
    that names no item or several, two selectors for one item, and an anchor
    that names no item, several, the item itself or an item the patch
    deletes. Changes nothing.
    """
    items = [s.value for s in slots]
    entries: list[_ItemEntry] = []
    named: dict[int, Path] = {}
    for selector, value in ops.items():
        selection = _select(items, spec, selector, resolvers)
        part: PathPart = (
            selection.position if selection.position is not None else Key(str(selector))
        )
        where = path.child(part)
        op = parse_item_op(value, where)
        slot: _Slot | None = None
        new: object = None
        if op.kind == "add":
            new = _check_add(spec, selector, selection, op, items, where)
        elif new_only:
            raise PatchError(
                f"{op.kind} in a list the patch builds: nothing exists yet to "
                + "name; write add() entries or a list value",
                where,
            )
        else:
            if op.kind == "change":
                _check_change(spec, where)
            index = _named_item(spec, selector, selection, op, items, where)
            if index in named:
                raise PatchError(
                    f"{named[index]} names this item too: one selector per item",
                    where,
                )
            named[index] = where
            slot = slots[index]
        anchor: _Slot | None = None
        if op.place is not None and op.place.kind in ("before", "after"):
            anchor = slots[
                _anchor_position(
                    items, spec, op.place.anchor, slot, slots, resolvers, where
                )
            ]
        body = dict[object, object](op.body.items())  # a copy: the patch is not changed
        entries.append(_ItemEntry(op.kind, body, op.place, where, slot, new, anchor))
    deleted = {id(e.slot) for e in entries if e.kind == "delete"}
    for entry in entries:
        if entry.anchor is not None and id(entry.anchor) in deleted:
            raise PatchError("the anchor is an item this patch deletes", entry.path)
    return entries


def _check_add(
    spec: _ListSpec,
    selector: object,
    selection: _Selection,
    op: ItemOp,
    items: Sequence[object],
    where: Path,
) -> object:
    """Refuse an ``add`` the list does not take; return what it writes (``_ItemEntry.new``)."""
    if spec.kind in ("positional", "undeclared"):
        if spec.item_type is None:
            raise PatchError(
                "a list of values named by position has no body to build an item "
                + "from: give a list value",
                where,
            )
        if not is_new(selector):
            raise PatchError(
                'add() in a list named by position takes "+" or "+tag" as its selector',
                where,
            )
        return None
    if selection.position is not None:
        raise PatchError(
            "at(n) names an item that exists: add() takes a "
            + ("key" if spec.kind == "keyed" else "value"),
            where,
        )
    if selection.positions:
        raise PatchError(f"{selection.key!r} is already in the list", where)
    if spec.kind == "keyed":
        return selection.key
    # A value list: the selector is the value.
    if op.body:
        raise PatchError("add() in a list of values takes no body", where)
    assert spec.element is not None
    try:
        new = spec.element.validate_python(selector, context={"patch": True})
    except ValidationError as exc:
        raise PatchError(_validation_message(exc), where) from exc
    if any(str(item) == str(new) for item in items):
        raise PatchError(f"{new!r} is already in the list", where)
    return new


def _check_change(spec: _ListSpec, where: Path) -> None:
    """Refuse a change (a plain object) where the items have no fields."""
    if spec.item_type is None:
        raise PatchError(
            "an entry of a list of values has no fields to change: "
            + "add() or delete() it, or give a list value",
            where,
        )


def _named_item(
    spec: _ListSpec,
    selector: object,
    selection: _Selection,
    op: ItemOp,
    items: Sequence[object],
    where: Path,
) -> int:
    """The position of the one item a change, delete or move names; else ``PatchError``."""
    if len(selection.positions) == 1:
        return selection.positions[0]
    if selection.positions:
        raise PatchError(
            f"{str(selector)!r} names {len(selection.positions)} items: "
            + _listing(spec, items, selection.positions)
            + "; select one of them",
            where,
        )
    if selection.position is not None:
        raise PatchError(
            f"there is no item at position {selection.position}: "
            + f"the list has {len(items)}",
            where,
        )
    if spec.kind in ("positional", "undeclared"):
        hint = "; a new item is written with add()" if is_new(selector) else ""
        raise PatchError(
            f"{str(selector)!r} is not a position: the items of this list are "
            + f"named by at(n){hint}",
            where,
        )
    what = "key" if spec.kind == "keyed" else "entry"
    message = f"no item has the {what} {selection.key!r}"
    if op.kind == "change":
        message += "; to add one, write add(...)"
    raise PatchError(message, where)


def _listing(spec: _ListSpec, items: Sequence[object], positions: Sequence[int]) -> str:
    """Each item at *positions*, as its position and its key or value."""
    shown: list[str] = []
    for index in positions:
        item = items[index]
        key = str(item) if spec.key_path is None else _item_key(item, spec.key_path)
        shown.append(f"#{index} ({key})")
    return ", ".join(shown)


def _anchor_position(
    items: Sequence[object],
    spec: _ListSpec,
    anchor: object,
    slot: _Slot | None,
    slots: Sequence[_Slot],
    resolvers: IdentityResolver | None,
    where: Path,
) -> int:
    """The position of the one item *anchor* names, which is not *slot*'s; else ``PatchError``."""
    selection = _select(items, spec, anchor, resolvers)
    if not selection.positions:
        raise PatchError(f"the anchor {anchor!r} names no item", where)
    if len(selection.positions) > 1:
        raise PatchError(
            f"the anchor {anchor!r} names {len(selection.positions)} items: "
            + _listing(spec, items, selection.positions)
            + "; select one of them",
            where,
        )
    index = selection.positions[0]
    if slots[index] is slot:
        raise PatchError("an item cannot be placed before or after itself", where)
    return index


def _new_list_item(
    spec: _ListSpec,
    entry: _ItemEntry,
    slots: Sequence[_Slot],
    resolvers: IdentityResolver | None,
    context: ConfigContext | None,
) -> object:
    """Build the item an ``add`` entry adds to the list now in *slots*."""
    item_type = spec.item_type
    if spec.kind == "value":
        new = entry.new
    elif item_type is None:
        raise PatchError("this list has no item type to build from", entry.path)
    elif spec.kind == "keyed":
        assert isinstance(entry.new, str)
        new = _new_keyed_item(
            spec, item_type, entry.body, entry.new, resolvers, entry.path, context
        )
    else:
        return _new_item(item_type, entry.body, resolvers, entry.path, context)
    # The key or value may have come into the list since step 1, by a rename.
    if _matching([s.value for s in slots], spec, str(entry.new)):
        raise PatchError(f"{str(entry.new)!r} is already in the list", entry.path)
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


def _merge_map(
    entries: dict[object, object],
    spec: _ListSpec,
    ops: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    *,
    new_only: bool,
) -> None:
    """Apply the item operations *ops* to *entries*, a map of models, in place.

    A selector is a map key, compared in string form. Change, ``add`` and
    ``delete`` only: a map has no order, so ``move``, a ``_position`` and a
    position selector raise ``PatchError``. Every entry is checked first;
    then changes, deletes and adds, in that order. *new_only* is for a map the
    patch builds: every entry must be an add.
    """
    keys = {str(key): key for key in entries}
    resolved: list[tuple[ItemOp, str, Path]] = []
    for selector, value in ops.items():
        text = str(selector)
        where = path.child(Key(text))
        op = parse_item_op(value, where)
        if op.place is not None:
            raise PatchError("a map has no order: no move() and no _position", where)
        if any(text == other for _, other, _ in resolved):
            raise PatchError("another selector names this entry: one per entry", where)
        if op.kind == "add":
            if text in keys:
                raise PatchError(f"{text!r} is already in the map", where)
        elif new_only:
            raise PatchError(
                f"{op.kind} in a map the patch builds: nothing exists yet to name; "
                + "write add() entries",
                where,
            )
        elif text not in keys:
            raise PatchError(
                "a map has no positions: name an entry by its key"
                if position_of(selector) is not None
                else f"no entry has the key {text!r}"
                + ("; to add one, write add(...)" if op.kind == "change" else ""),
                where,
            )
        resolved.append((op, text, where))

    for op, text, where in resolved:
        if op.kind == "change":
            current = entries[keys[text]]
            if not isinstance(current, VcoModel):
                raise PatchError(
                    f"the entry is {type(current).__name__}, not a model", where
                )
            _merge_model(
                current, dict[object, object](op.body.items()), resolvers, where
            )
    for op, text, _ in resolved:
        if op.kind == "delete":
            del entries[keys[text]]
    for op, text, where in resolved:
        if op.kind == "add":
            if spec.map_type is None:
                raise PatchError("this map has no one model type to build from", where)
            entries[text] = _new_model_from_patch(
                spec.map_type, dict[object, object](op.body.items()), resolvers, where
            )


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
