"""The merge engine behind ``VcoModel.merge_patch``.

``apply_patch`` merges a partial dict into a model; everything private here
supports it. ``VcoModel.merge_patch`` in ``base.py`` is a thin call into it.
"""

from __future__ import annotations

import copy
import difflib
import functools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, cast, get_args, get_origin

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
    get_key_spec,
    get_name_of,
    get_resolvable,
    has_override_flag,
    is_edge_local,
)
from .identity import ResolutionError
from .patch import (
    ItemOp,
    PatchError,
    Place,
    checked_patch,
    is_new,
    parse_item_op,
    position_of,
)
from .rebase import (
    Key,
    KeyForm,
    KeyScalar,
    KeyValue,
    Path,
    PathPart,
    item_key,
    key_form,
    key_part,
    key_text,
    parse_key_text,
)


def apply_patch(
    model: VcoModel, patch: Mapping[str, object], resolvers: IdentityResolver | None
) -> VcoModel:
    """Recursively merge a partial dict into *model*, in place. See ``VcoModel.merge_patch``.

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


def nest_patch(
    model: type[VcoModel],
    patch: object,
    *,
    segment: str | None = None,
    path: str | None = None,
) -> dict[str, object]:
    """The patch for *model* that an addressed call stands for.

    Without *segment* and *path*, *patch* itself, which must be a ``dict``
    (``TypeError`` otherwise). Otherwise *patch* is any JSON value and is
    nested under the address: ``path="a.b"`` gives ``{"a": {"b": patch}}``,
    ``segment="S"`` gives ``{"segments": {"S": patch}}``, and both give
    ``{"segments": {"S": {"a": {"b": patch}}}}``. What is returned is a
    checked copy of the patch (``checked_patch``): it holds JSON values only,
    and an error is reported under the address.

    A part of *path* names a field by any name ``merge_patch`` accepts; the
    nested patch uses the python name. With *segment* the parts start at the
    item class of the ``segments`` field, without at *model*. Every part but
    the last must be a field that holds a single sub-model. Only the classes
    are consulted, never an instance.

    A bad address raises ``PatchError`` at the path of the part that failed,
    in python names, under ``segments[S]`` when there is a *segment*: a part
    that names no field (the message gives the class, the part and the field
    names), a part before the last that is not a single sub-model, an empty
    *path* or part, and *segment* for a class with no ``segments`` field.
    """
    if segment is None and path is None:
        if not isinstance(patch, dict):
            raise TypeError(
                f"a patch with no address must be a dict, not {type(patch).__name__}: "
                "give an object, or name the place a value goes with segment or path"
            )
        return cast("dict[str, object]", checked_patch(patch))
    cls = model
    walked = Path(())
    if segment is not None:
        walked = walked.child("segments")
        field = cls.model_fields.get("segments")
        if field is None:
            raise PatchError(f"{cls.__name__} has no segments field", walked)
        item_type = _list_item_type(field)
        if item_type is None:
            raise PatchError(f"{cls.__name__}.segments is not a list of models", walked)
        cls, walked = item_type.variants[0], walked.child(Key(segment))
    names: list[str] = []
    parts = path.split(".") if path is not None else []
    for index, part in enumerate(parts):
        where = walked.child(part)
        if not part:
            raise PatchError(f"the path {path!r} has an empty part", where)
        try:
            name = _field_name(cls._alias_map(), cls.__name__, part, walked)  # pyright: ignore[reportPrivateUsage]
        except PatchError as exc:
            fields = ", ".join(cls.model_fields)
            raise PatchError(f"{exc.message}; its fields are {fields}", where) from exc
        names.append(name)
        if index < len(parts) - 1:
            sub = _field_model_type(cls.model_fields[name])
            if sub is None:
                raise PatchError(
                    f"{cls.__name__}.{name} does not hold a single sub-model, "
                    + "so the path cannot continue past it",
                    where,
                )
            cls = sub
        walked = where
    # Where the patch sits once nested, in python names, for its errors and its depth.
    where_nested = Path(
        (*(("segments", Key(segment)) if segment is not None else ()), *names)
    )
    nested = checked_patch(patch, where_nested)
    for name in reversed(names):
        nested = {name: nested}
    if segment is None:
        return cast("dict[str, object]", nested)
    return {"segments": {segment: nested}}


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
    """
    model_cls = type(model)
    fields = model_cls.model_fields
    names = model_cls._alias_map()  # pyright: ignore[reportPrivateUsage]

    # (python_name, value) for each patch key.
    updates = [
        (name, value)
        for _, name, value in _patch_fields(names, model_cls.__name__, patch, path)
    ]
    # Copy the touched fields first so OverrideFlag logic can see what changed.
    before: JsonObject = {
        name: copy.deepcopy(_field_value(model, name)) for name, _ in updates
    }

    for name, value in updates:
        _merge_field(
            model, name, fields[name], value, resolvers, path.child(name), built=built
        )

    _auto_set_override_flags(model, before)


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


def _patch_fields(
    names: Mapping[str, str], owner: str, patch: Mapping[object, object], path: Path
) -> list[tuple[object, str, object]]:
    """``(key, python name, value)`` for each key of *patch*, each checked by ``_field_name``.

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
            )
        seen[name] = key
        fields.append((key, name, value))
    return fields


def _merge_field(
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

    # null: what the field takes it as (``_null_value``).
    if value is None:
        value = _null_value(field_info, name, path, built=built)
        setattr(model, name, _validate_field(model_cls, name, value, path))
        return

    # A list, or a map of models: a list value, or item operations. An object
    # on a field that also takes one sub-model is that sub-model, unless the
    # field holds a list.
    if _list_spec(field_info) is not None and not (
        _is_dict(value)
        and _field_model_types(field_info)
        and not isinstance(_field_value(model, name), list)
    ):
        _merge_list(model, name, field_info, value, resolvers, path)
        return

    if _is_dict(value):
        current = _field_value(model, name)

        # Sub-model that already exists: merge into it.
        if isinstance(current, VcoModel):
            _merge_model(current, value, resolvers, path, built=built)
            _check_merged(
                current,
                _field_adapter(model_cls, name).validate_python
                if len(_field_model_types(field_info)) > 1
                else type(current).model_validate,
                path,
            )
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


def _null_value(field_info: FieldInfo, name: str, path: Path, *, built: bool) -> object:
    """What ``null`` means for field *name*: ``MISSING``, ``None``, or ``PatchError`` at *path*.

    A field whose type takes ``MISSING`` is unset by ``null``, one that takes
    only ``None`` is set to it, and one that takes neither refuses it. A field
    that takes both is unset in a change, where leaving the key out would
    change nothing. In an item or sub-model the patch *built* it is ``None``:
    leaving the key out already leaves the field unset, and the JSON dump of
    an item writes ``null`` for ``None``, so a dump builds the same item.
    """
    takes_missing = _field_accepts_missing(field_info)
    takes_none = _field_accepts_none(field_info)
    if takes_none and (built or not takes_missing):
        return None
    if takes_missing:
        return MISSING
    reason, remedy = (
        ("the field cannot be unset", "leave the key out")
        if built
        else ("a change cannot unset it", "give a value, or leave the key out")
    )
    raise PatchError(f"null is not a value of {name}, and {reason}: {remedy}", path)


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
    validation and do not run here. When a model instance is the value, as
    ``_merge_field`` gives one, the class's ``after`` and ``wrap`` model
    validators do run on it.
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
    fails raises ``PatchError`` at *path*, from the ``ValidationError``. A
    boolean given for a value that is not one raises as well
    (``_refuse_bool_as_number``).
    """
    try:
        validated = _field_adapter(model_cls, name).validate_python(
            value, context={"patch": True}
        )
    except ValidationError as exc:
        raise _invalid_value(
            exc, _value_models(model_cls.model_fields[name]), path
        ) from exc
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
            + f"it would be read as {stored!r}; send a number",
            path,
        )


def _value_models(field_info: FieldInfo) -> tuple[type[VcoModel], ...]:
    """The model classes a field's value is made of, without repeats.

    The field's own (every variant of a union), then the items of its list,
    then the values of its map.
    """
    found = list(_field_model_types(field_info))
    if (item_type := _list_item_type(field_info)) is not None:
        found.extend(item_type.variants)
    if (spec := _list_spec(field_info)) is not None and spec.map_type is not None:
        found.append(spec.map_type)
    return tuple(dict.fromkeys(found))


def _python_place(loc: Sequence[int | str], models: Sequence[type[VcoModel]]) -> Path:
    """Where in the value an error is, in python names.

    A part that names a field of the model classes in hand, by any name
    ``merge_patch`` accepts, becomes its python name, and the walk goes on
    in the classes that field's value is made of. Any other part is kept
    as it is: a list position, a map key, the tag or the name of a union
    member.
    """
    parts: list[PathPart] = []
    current = tuple(models)
    for part in loc:
        if isinstance(part, str) and current:
            fields = _merged_fields(current)
            name = _field_names_by_key(fields).get(part)
            if name is not None:
                parts.append(name)
                current = _value_models(fields[name])
                continue
        parts.append(part)
    return Path(parts)


def _validation_lines(exc: ValidationError, models: Sequence[type[VcoModel]]) -> str:
    """What a ``ValidationError`` says, one ``place: message`` per error, joined by ``"; "``.

    The place is in python names (``_python_place``), where *models* are the
    classes the validated value is made of; it is left out for an error of
    the value itself.
    """
    lines: list[str] = []
    for error in exc.errors(include_url=False):
        place = _python_place(error["loc"], models)
        lines.append(f"{place}: {error['msg']}" if place else error["msg"])
    return "; ".join(lines)


def _invalid_value(
    exc: ValidationError, models: Sequence[type[VcoModel]], path: Path
) -> PatchError:
    """The ``PatchError`` at *path* for a value that fails validation; raise it ``from exc``."""
    return PatchError("invalid value: " + _validation_lines(exc, models), path)


def _check_merged(model: VcoModel, validate: Callable[..., object], path: Path) -> None:
    """Check *model*, merged into and complete, as the parser would; ``PatchError`` at *path* if not.

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
        raise PatchError(
            f"{name} is not valid after this change: {_validation_lines(exc, (type(model),))}",
            path,
        ) from exc
    except Exception as exc:
        raise PatchError(
            f"a validator of {name} failed on the changed model with "
            + f"{type(exc).__name__}: {exc}; the model validators of {name} "
            + "must accept a model instance, as well as the dict a response "
            + "gives them",
            path,
        ) from exc


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
            raise _invalid_value(exc, (model_type,), path) from exc
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
        _refuse_bool_as_number(value, current, child)
        if _is_dict(value) and isinstance(current, VcoModel):
            _refuse_unread_keys(current, value, child)
        elif (
            _is_list(value)
            and _is_list(current)
            and (spec := _list_spec(model_cls.model_fields[name])) is not None
        ):
            _check_list_value(spec, value, current, child)


def _resolve_name(
    field_info: FieldInfo, name: str, resolvers: IdentityResolver, path: Path
) -> object:
    """Turn the string *name* on a Resolvable field into the field's ID.

    Returns *name* unchanged if the field is not Resolvable. Otherwise, in order:

    1. If the field's ID type is not ``str`` and *name* parses as it (a UUID
       string on a UUID field), that value is the ID.
    2. An exact or case-insensitive name match gives the identity's ``id_attr``.
    3. If the ID type is ``str`` and *name* equals a known ID, *name* is the ID.
    4. Otherwise the ``ResolutionError`` propagates.

    With no resolver registered for the identity type, *name* is returned when
    the ID type is ``str`` or cannot be told (any text may be the ID, and
    validation decides). Otherwise *name* is not an ID and nothing can look it
    up, so ``PatchError`` at *path* says to register the resolver or give the ID.
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
    except LookupError as err:
        if id_type is None or id_type is str:
            return name
        raise PatchError(
            f"{name!r} is not an ID of this field, and no "
            f"{resolvable.identity_type.__name__} resolver is registered to look "
            "it up as a name: give the ID, or register that resolver",
            path,
        ) from err
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
        raise _invalid_value(exc, item_type.variants, path) from exc
    _refuse_unread_keys(item, item_input, path)
    if context is not None:
        _ = item.for_context(context)
        _fill_context_defaults(item)
    return item


def _new_keyed_item(
    spec: _ListSpec,
    item_type: _ListItemType,
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
        _require_ids_for_name(item, spec.name_identity, spec.key_paths[0], str(key))
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
    patch: Mapping[object, object], key_path: str, key_value: KeyScalar
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

    - raises ``PatchError`` for a key that names no field, for a directive
      and for two keys that name one field (``_patch_fields``)
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
      ``segment_id`` / ``segment_logical_id``). An unknown name raises
      ``ResolutionError`` and a missing resolver skips the fill.

    With no *resolvers*, names are left as they are.
    """
    names = _field_names_by_key(fields)
    built: JsonObject = {}

    for key, field_name, value in _patch_fields(names, owner, patch, path):
        fi = fields[field_name]
        child = path.child(field_name)
        if value is None:
            value = _null_value(fi, field_name, child, built=True)
        elif isinstance(value, str):
            if resolvers is not None:
                value = _resolve_name(fi, value, resolvers, child)
        elif _is_dict(value) and (sub_types := _field_model_types(fi)):
            value = _build_input(
                _merged_fields(sub_types),
                value,
                resolvers,
                child,
                owner=_variant_names(sub_types),
            )
        elif (_is_list(value) or _is_dict(value)) and (
            spec := _list_spec(fi)
        ) is not None:
            value = _new_list_input(spec, value, resolvers, child)
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
# Lists and maps: list values and item operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _ListSpec:
    """How the items of a list field, or the values of a map field, are named and built."""

    kind: ListKind
    key_paths: tuple[str, ...]
    """The ``KeyField`` paths of a keyed list, one per part of its key; empty for any other list."""
    alt_path: str | None
    """The ``KeyField`` alt key path of a keyed list that has one."""
    label_path: str | None
    """The ``KeyField`` label path of a keyed list that has one."""
    item_type: _ListItemType | None
    """The model type of the items of a list of models."""
    element: TypeAdapter[object] | None
    """Validates one entry of a list that is not of models."""
    map_type: type[VcoModel] | None
    """The model type of the values of a map, when it has one."""
    map_key: TypeAdapter[object] | None
    """Validates the key of a new entry of a map."""
    name_identity: type[object] | None
    """What the one-part key of a keyed list is the ``NameOf``, if it is one."""


@functools.cache
def _list_spec(field_info: FieldInfo) -> _ListSpec | None:
    """The ``_ListSpec`` of a list or map field (``list_kind``), or None for any other field."""
    kind = list_kind(field_info)
    if kind is None:
        return None
    key_spec = get_key_spec(field_info) if kind == "keyed" else None
    key_paths = () if key_spec is None else key_spec.paths
    item_type = _list_item_type(field_info)
    element: TypeAdapter[object] | None = None
    map_type: type[VcoModel] | None = None
    map_key: TypeAdapter[object] | None = None
    if kind == "map":
        key_type, value_type = _type_args(field_info, dict)
        value_types = [m for m in _members_of(value_type) if _is_vco_model_type(m)]
        map_type = value_types[0] if len(value_types) == 1 else None
        map_key = TypeAdapter[object](key_type)  # pyright: ignore[reportArgumentType]
    elif item_type is None:
        element = TypeAdapter[object](_type_args(field_info, list)[0])  # pyright: ignore[reportArgumentType]
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


def _item_key(item: object, paths: tuple[str, ...]) -> KeyForm | None:
    """The key of a keyed-list item at *paths* (``item_key``), in string form; None if it has none."""
    return key_form(item_key(item, paths))


def _alt_key(spec: _ListSpec, item: object) -> KeyForm | None:
    """The alt key of *item* in a keyed list, in string form; None if the list has no alt key or the item none."""
    if spec.alt_path is None:
        return None
    return _item_key(item, (spec.alt_path,))


def _show_key(key: KeyValue) -> str:
    """A key for a message: a composite key as ``key_text`` writes it, quoted."""
    return repr(key_text(key) if isinstance(key, tuple) else str(key))


def _show_item(spec: _ListSpec, item: object, index: int) -> str:
    """The item at position *index* of a keyed list for a message: by its key, or by its position when it has none."""
    key = item_key(item, spec.key_paths)
    return f"item #{index}" if key is None else f"item {_show_key(key)}"


def _show_selector(selector: object) -> str:
    """A selector for a message: a tuple or list of key parts as ``key_text`` writes it, quoted."""
    if isinstance(selector, (tuple, list)):
        parts = cast("tuple[object, ...] | list[object]", selector)
        return _show_key(tuple(key_part(p) for p in parts))
    return repr(str(selector))


def _key_path_part(key: KeyValue) -> Key:
    """The path part that names an item by *key*: a one-part key in string form, a composite key part by part."""
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
    """The value that *data*, a patch for a new item with *fields*, gives at dotted *path*.

    None if it gives none (or null, or MISSING). Keys of *data* are read as
    ``_build_input`` reads them (python name, alias or camelCase).
    """
    value: object = data
    current: Mapping[str, FieldInfo] | None = fields
    for part in path.split("."):
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
    return None if value is MISSING else value


@dataclass(frozen=True)
class _Selection:
    """What a selector names in a list (``_select``)."""

    positions: list[int]
    """The positions of the items it names, in the list as it was."""
    key: KeyValue | None
    """The key (keyed list) or value (value list) it stands for: as written, or the resolver's name.

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


def _plain_key_text(key: KeyValue) -> str:
    """The simplest text that selects an item by *key*.

    A one-part key in string form. A composite key: its parts in string form
    joined by commas, unless a part holds a comma or a double quote, which
    only the form ``key_text`` writes can carry.
    """
    if not isinstance(key, tuple):
        return str(key)
    texts = [str(part) for part in key]
    if any("," in text or '"' in text for text in texts):
        return key_text(key)
    return ",".join(texts)


def _select(
    items: Sequence[object],
    spec: _ListSpec,
    selector: object,
    resolvers: IdentityResolver | None,
) -> _Selection:
    """Resolve *selector* against *items*; the one place a selector is read.

    In a keyed list, tried in order: an item whose key is one
    the selector stands for (``_selector_keys``), compared in string form
    part by part; ``"#n"``, the item at position ``n``; ``"+"`` or ``"+tag"``,
    a new item; an item whose alt key equals the selector in string form;
    an item whose label does; and, where the key is a ``NameOf`` field and
    with *resolvers*, the name the resolver has for the selector, as the key
    once more. An unknown name raises ``ResolutionError``. In a value list, an
    entry equal to the selector in string form, then ``"#n"``. In any other
    list only ``"#n"`` names an item.
    """
    if spec.kind == "value":
        text = str(selector)
        if found := [i for i, item in enumerate(items) if str(item) == text]:
            return _Selection(found, text, None)
    keys = _selector_keys(spec, selector) if spec.kind == "keyed" else []
    for candidate in keys:
        if found := _with_key(items, spec, candidate):
            return _Selection(found, candidate, None)
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


def _with_key(items: Sequence[object], spec: _ListSpec, key: KeyValue) -> list[int]:
    """The positions of the items of a keyed list whose key is *key*, in string form."""
    form = key_form(key)
    return [
        i for i, item in enumerate(items) if _item_key(item, spec.key_paths) == form
    ]


def _with_part(items: Sequence[object], path: str, text: str) -> list[int]:
    """The positions of the items whose value at *path* (an alt key, a label) is *text* in string form."""
    return [i for i, item in enumerate(items) if _item_key(item, (path,)) == text]


def select_items(
    model: VcoModel,
    field_name: str,
    selector: object,
    resolvers: IdentityResolver | None = None,
) -> list[int]:
    """The positions of the items of the list field *field_name* that *selector* names.

    Empty if it names none. A selector is read as in a patch
    (``_select``): the key of an item of a keyed list (for a composite key,
    its parts joined by commas, a tuple of them, or the form ``key_text``
    writes), an entry of a value list,
    ``at(n)``, the alt key or the label of an item of a keyed list that has
    one, and in a keyed list whose key is a ``NameOf`` field, the resolver's
    name for it. An unknown name raises ``ResolutionError``. Raises
    ``TypeError`` if the field is not a list.
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
    key, ``path[#n]`` otherwise. In a list whose key is a name (``NameOf``),
    an item that gives one takes the resolver's name for it, as an add under
    that name does (an unknown name raises ``ResolutionError``); the IDs of
    the name are then filled by ``_build_input`` and checked by
    ``_check_list_value``. Any other value is returned as it is.
    """
    item_type = spec.item_type
    if item_type is None or not _is_list(value):
        return value
    built: list[object] = []
    for index, item in enumerate(value):
        if _is_dict(item):
            key = _patch_key(item_type.fields, item, spec.key_paths)
            if (
                key is not None
                and spec.name_identity is not None
                and resolvers is not None
            ):
                name = _canonical_name(spec.name_identity, str(key), resolvers)
                if name != key:
                    item, key = _inject_key_path(item, spec.key_paths[0], name), name
            part: PathPart = index if key is None else _key_path_part(key)
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
    (``_refuse_unread_keys``). In a list whose key is a name (``NameOf``),
    such an item that has a name and lacks the IDs of it raises
    ``LookupError``, as an add under that name does
    (``_require_ids_for_name``). A keyed list whose items repeat a key or an
    alt key raises ``PatchError`` at *path*.
    """
    if len(data) == len(built):
        for index, (item_data, item) in enumerate(zip(data, built)):
            _refuse_bool_as_number(item_data, item, path.child(index))
            if _is_dict(item_data) and isinstance(item, VcoModel):
                _refuse_unread_keys(
                    item, item_data, path.child(_item_part(spec, item, index))
                )
                key = item_key(item, spec.key_paths)
                if spec.name_identity is not None and key is not None:
                    _require_ids_for_name(
                        item, spec.name_identity, spec.key_paths[0], str(key)
                    )
    seen: dict[KeyForm, int] = {}
    seen_alt: dict[KeyForm, int] = {}
    for index, item in enumerate(built):
        key = item_key(item, spec.key_paths)
        form = key_form(key)
        if key is not None and form is not None:
            if form in seen:
                raise PatchError(
                    f"items #{seen[form]} and #{index} have one key, "
                    + f"{_show_key(key)}: the items of a keyed list have "
                    + "distinct keys",
                    path,
                )
            seen[form] = index
        alt = _alt_key(spec, item)
        if alt is not None:
            if alt in seen_alt:
                first = seen_alt[alt]
                raise PatchError(
                    f"items #{first} and #{index} have one alt key, {alt!r}: "
                    + "the items of a keyed list have distinct alt keys; "
                    + f"{_show_item(spec, built[first], first)} holds it "
                    + f"already, so give item #{index} another alt key",
                    path,
                )
            seen_alt[alt] = index


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
    """For an add: what it adds, built in step 1: the item (list of models) or the validated value (value list)."""
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
    renamed: list[_ItemEntry] = []
    alt_changed: list[_ItemEntry] = []
    for entry in entries:
        if entry.kind != "change" or entry.slot is None:
            continue
        item = entry.slot.value
        assert isinstance(item, VcoModel)  # _resolve_items checked it
        before = _item_key(item, paths)
        alt_before = _alt_key(spec, item)
        _merge_model(item, entry.body, resolvers, entry.path)
        _check_merged(
            item,
            spec.item_type.validate
            if spec.item_type is not None and len(spec.item_type.variants) > 1
            else type(item).model_validate,
            entry.path,
        )
        if _item_key(item, paths) != before:
            renamed.append(entry)
        if _alt_key(spec, item) != alt_before:
            alt_changed.append(entry)
    for entry in renamed:
        assert entry.slot is not None
        key = item_key(entry.slot.value, paths)
        form = key_form(key)
        if key is not None and any(
            s is not entry.slot and _item_key(s.value, paths) == form for s in slots
        ):
            raise PatchError(f"another item has the key {_show_key(key)}", entry.path)
    for entry in alt_changed:
        assert entry.slot is not None
        alt = _alt_key(spec, entry.slot.value)
        if alt is None:
            continue
        for index, other in enumerate(slots):
            if other is not entry.slot and _alt_key(spec, other.value) == alt:
                raise PatchError(
                    f"{_show_item(spec, other.value, index)} already has the "
                    + f"alt key {alt!r}: give this item another alt key, or "
                    + "change that item instead",
                    entry.path,
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


def _resolve_items(
    slots: Sequence[_Slot],
    spec: _ListSpec,
    ops: Mapping[object, object],
    resolvers: IdentityResolver | None,
    path: Path,
    context: ConfigContext | None,
    *,
    new_only: bool,
) -> list[_ItemEntry]:
    """Parse every entry of *ops*, resolve its selector and anchor, and build what an add adds.

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
        op = parse_item_op(value, where)
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
                    )
                added[form] = where
        elif new_only:
            raise PatchError(
                f"a {op.kind} in a list the patch builds has nothing to name: "
                + 'write {"_op": "add", ...} entries (add(...)) or a list value',
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
            if op.kind == "change" and not isinstance(slot.value, VcoModel):
                raise PatchError(
                    f"the item is {type(slot.value).__name__}, not a model", where
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
            raise PatchError("the anchor is an item this patch deletes", entry.path)
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
    """The key (keyed list) or value (value list) in string form of what an add adds, or None."""
    if spec.kind == "value":
        return str(new)
    if spec.kind == "keyed":
        return _item_key(new, spec.key_paths)
    return None


def _check_add(
    spec: _ListSpec,
    selector: object,
    selection: _Selection,
    op: ItemOp,
    items: Sequence[object],
    resolvers: IdentityResolver | None,
    where: Path,
    context: ConfigContext | None,
) -> object:
    """Refuse an ``add`` the list does not take; return what it adds (``_ItemEntry.new``).

    In a list of models, the item, built here; in a value list, the value,
    validated. Under a plain selector a keyed list takes the key only: a key
    of as many parts as the list's, not an alt key or a label. Under ``"+"``
    the key is whatever the item built from the body has, if any.
    """
    if spec.kind in ("positional", "undeclared"):
        if spec.item_type is None:
            raise PatchError(
                "a list of values named by position has no body to build an item "
                + "from: give a list value",
                where,
            )
        if not is_new(selector):
            raise PatchError(
                "a new item of a list named by position goes under "
                + '"+" or "+tag": write {"+": {"_op": "add", ...}} ("+": add(...))',
                where,
            )
        return _new_item(spec.item_type, _body(op), resolvers, where, context)
    if selection.position is not None:
        raise PatchError(
            '"#n" (at(n)) names an item that exists: an add takes a '
            + ("key" if spec.kind == "keyed" else "value")
            + " as its selector",
            where,
        )
    if spec.kind == "keyed":
        return _check_keyed_add(
            spec, selector, selection, op, items, resolvers, where, context
        )
    if selection.positions:
        raise PatchError(f"{selection.key!r} is already in the list", where)
    # A value list: the selector is the value.
    if is_new(selector):
        raise PatchError(
            f"{str(selector)!r} names no value: an add in a list of values takes "
            + "the value itself as its selector, "
            + 'as in {"<value>": {"_op": "add"}} (`{"<value>": add()}`); a '
            + 'value that starts with "+" cannot be added under its own text, '
            + "so give the whole list as a list value",
            where,
        )
    if op.body:
        raise PatchError(
            "an add in a list of values takes no fields: "
            + 'write {"_op": "add"} (add()) under the value',
            where,
        )
    assert spec.element is not None
    try:
        new = spec.element.validate_python(selector, context={"patch": True})
    except ValidationError as exc:
        raise _invalid_value(exc, (), where) from exc
    if any(str(item) == str(new) for item in items):
        raise PatchError(f"{new!r} is already in the list", where)
    return new


def _key_form_hint(spec: _ListSpec, items: Sequence[object]) -> str:
    """How a composite key is written, with the key of the first keyed item of *items* as the example."""
    n = len(spec.key_paths)
    form = f"a key is its {n} parts ({', '.join(spec.key_paths)}) joined by commas"
    for item in items:
        key = item_key(item, spec.key_paths)
        if key is not None:
            return f"{form}, as in {_plain_key_text(key)}"
    return form


def _check_keyed_add(
    spec: _ListSpec,
    selector: object,
    selection: _Selection,
    op: ItemOp,
    items: Sequence[object],
    resolvers: IdentityResolver | None,
    where: Path,
    context: ConfigContext | None,
) -> VcoModel:
    """The item an ``add`` in a keyed list adds, built; ``PatchError`` if the list does not take it (``_check_add``).

    Under ``"+"`` in a list whose key is a name (``NameOf``), a name in the
    body is taken as an add under that name: the same resolver lookup and the
    same refusals, ``LookupError`` included when the IDs of the name are not
    found (``_new_keyed_item``).
    """
    item_type = spec.item_type
    if item_type is None:
        raise PatchError("this list has no item type to build from", where)
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
                f"the body gives the key {_show_key(key)}, which is already in the list",
                where,
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
        )
    if selection.positions and not selection.label:
        where_in = " as an alt key" if selection.alt else ""
        raise PatchError(f"{_show_key(key)} is already in the list{where_in}", where)
    parts = key if isinstance(key, tuple) else (key,)
    for key_path, part in zip(spec.key_paths, parts, strict=True):
        given = _patch_value(item_type.fields, op.body, key_path)
        # Under "+" the body gave the name; the key is the resolver's for it.
        if not from_body and given is not None and str(given) != str(part):
            raise PatchError(
                f"the body gives {key_path} {str(given)!r}, and the selector "
                + f"{_show_key(key)}: an add takes the key from its selector",
                where,
            )
    return _new_keyed_item(spec, item_type, _body(op), key, resolvers, where, context)


def _check_change(spec: _ListSpec, where: Path) -> None:
    """Refuse a change (a plain object) where the items have no fields."""
    if spec.item_type is None:
        raise PatchError(
            "an entry of a list of values has no fields to change: "
            + 'add it with {"_op": "add"} (add()), '
            + 'remove it with {"_op": "delete"} (delete()), or give a list value',
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
            _names_several(_show_selector(selector), spec, items, selection.positions),
            where,
        )
    if selection.position is not None:
        raise PatchError(
            f"there is no item at position {selection.position}: "
            + f"the list has {len(items)}",
            where,
        )
    if spec.kind in ("positional", "undeclared"):
        hint = (
            '; a new item is written {"+": {"_op": "add", ...}} ("+": add(...))'
            if is_new(selector)
            else ""
        )
        raise PatchError(
            f"{str(selector)!r} is not a position: the items of this list are "
            + f'named by "#n" (at(n)){hint}',
            where,
        )
    if selection.new:
        raise PatchError(
            f"{str(selector)!r} names a new item, which only "
            + '{"_op": "add", ...} (add(...)) takes',
            where,
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
                        + '("+": add({...})) with its fields'
                    )
                raise PatchError(message, where)
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
    raise PatchError(message, where)


def _names_several(
    subject: str, spec: _ListSpec, items: Sequence[object], positions: Sequence[int]
) -> str:
    """The message for *subject*, a selector or anchor that names the items at *positions*.

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
        + "select one of them"
    )


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
            _names_several(f"the anchor {anchor!r}", spec, items, selection.positions),
            where,
        )
    index = selection.positions[0]
    if slots[index] is slot:
        raise PatchError("an item cannot be placed before or after itself", where)
    return index


def _new_list_item(
    spec: _ListSpec, entry: _ItemEntry, slots: Sequence[_Slot]
) -> object:
    """What an ``add`` entry adds (built in step 1), checked against the list now in *slots*.

    Its key, value or alt key may have come into the list since step 1, by a
    rename or an earlier add: that raises ``PatchError``.
    """
    new = entry.new
    form = _added_form(spec, new)
    if form is not None:
        if spec.kind == "value":
            taken = any(str(s.value) == form for s in slots)
        else:
            taken = any(_item_key(s.value, spec.key_paths) == form for s in slots)
        if taken:
            shown = item_key(new, spec.key_paths) if spec.kind == "keyed" else None
            raise PatchError(
                f"{_show_key(form if shown is None else shown)} is already in the list",
                entry.path,
            )
    alt = _alt_key(spec, new)
    if alt is not None:
        for index, slot in enumerate(slots):
            if _alt_key(spec, slot.value) == alt:
                raise PatchError(
                    f"{alt!r} is already in the list as an alt key, that of "
                    + f"{_show_item(spec, slot.value, index)}: give the new "
                    + "item another alt key, or change that item instead",
                    entry.path,
                )
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
                'a map has no order: it takes no "_position" (move(...))', where
            )
        if op.kind == "add":
            if text not in keys and (
                position_of(selector) is not None or is_new(selector)
            ):
                raise PatchError(
                    f"{text!r} names no entry of a map: an add takes the new "
                    + "entry's key as its selector, "
                    + 'as in {"<key>": {"_op": "add", ...}} '
                    + '(`{"<key>": add(...)}`)',
                    where,
                )
            key = _new_map_key(spec, text, where)
            text = str(key)
            if text in keys:
                raise PatchError(f"{text!r} is already in the map", where)
        elif new_only:
            raise PatchError(
                f"a {op.kind} in a map the patch builds has nothing to name: "
                + 'write {"_op": "add", ...} entries (add(...))',
                where,
            )
        elif text not in keys:
            raise PatchError(
                "a map has no positions: name an entry by its key"
                if position_of(selector) is not None
                else f"no entry has the key {text!r}"
                + (
                    '; to add one, write {"_op": "add", ...} under that key (add(...))'
                    if op.kind == "change"
                    else ""
                ),
                where,
            )
        else:
            key = keys[text]
            current = entries[key]
            if op.kind == "change" and not isinstance(current, VcoModel):
                raise PatchError(
                    f"the entry is {type(current).__name__}, not a model", where
                )
        if text in named:
            raise PatchError("another selector names this entry: one per entry", where)
        named.add(text)
        resolved.append((op, key, where))

    for op, key, where in resolved:
        if op.kind == "change":
            current = entries[key]
            assert isinstance(current, VcoModel)  # checked above
            _merge_model(current, _body(op), resolvers, where)
            _check_merged(current, type(current).model_validate, where)
    for op, key, _ in resolved:
        if op.kind == "delete":
            del entries[key]
    for op, key, where in resolved:
        if op.kind == "add":
            assert spec.map_type is not None  # _new_map_key checked it
            entries[key] = _new_model_from_patch(
                spec.map_type, _body(op), resolvers, where
            )


def _new_map_key(spec: _ListSpec, text: str, where: Path) -> object:
    """The key an ``add`` in a map writes: *text* validated as the map's key type.

    Raises ``PatchError`` if it is not one, or if the map's values are not of
    one model type, so there is nothing to build the entry as.
    """
    if spec.map_type is None:
        raise PatchError("this map has no one model type to build from", where)
    assert spec.map_key is not None  # a map
    try:
        return spec.map_key.validate_python(text, context={"patch": True})
    except ValidationError as exc:
        raise _invalid_value(exc, (), where) from exc


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
