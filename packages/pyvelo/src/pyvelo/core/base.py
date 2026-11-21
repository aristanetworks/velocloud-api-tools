"""VcoModel base class, serialization helpers, and API types.

Provides:
- VcoModel base class with compact and context-aware serialization
- ``VcoModel.merge_patch``: recursive merge of a partial dict into a model
- ContextView / ContextFieldInfo for field metadata introspection
- JsonRpc request/response/error models
- Pagination, API filters, and utility types

Terminology:

config level
    The active :class:`ConfigContext` (EDGE or PROFILE). It comes from a
    pydantic ``context={"config_level": ...}`` or from the model's own
    ``config_context`` (set by ``for_context`` / ``for_edge`` / ``for_profile``).
    Field markers (``EdgeOnly``, ``ContextDefault``, ...) make a field absent
    or change its default at a given level.

compact form
    A dump (``model_dump(context={"compact": True})``) that leaves out fields
    that are MISSING, absent at the config level, or equal to their default.

patch
    A JSON-like ``dict[str, object]`` merged into a model by ``merge_patch``.
    Keys that start with ``_`` are merge directives (e.g. ``_position``).
"""

from __future__ import annotations

import copy
import datetime
import functools
import math
import types
import warnings
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, IntEnum, auto
from typing import (
    Annotated,
    ClassVar,
    Literal,
    Protocol,
    Self,
    TypeIs,
    Union,  # pyright: ignore[reportDeprecated]
    get_args,
    get_origin,
    override,
)

import annotated_types
from pydantic import (
    MISSING,
    AliasChoices,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    PrivateAttr,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    Tag,
    TypeAdapter,
    ValidationError,
    model_serializer,
)
from pydantic.alias_generators import to_camel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .fields import (
    CONTEXT_NOT_SET,
    Alias,
    ConfigContext,
    NormalizerFn,
    apply_vco_markers,
    get_context_defaults,
    get_key_field,
    get_normalizer,
    get_resolvable,
    has_override_flag,
    is_edge_local,
    resolve_ctx_default,
)

type JsonObject = dict[str, object]
"""A JSON object: a merge patch, a compact dump, a pydantic context, ..."""


class IdentityResolver(Protocol):
    """What ``merge_patch`` needs to turn names into IDs.

    Satisfied by ``pyvelo.config.resolver.ResolverRegistry``.
    """

    def resolve_sync[T](self, key: type[T], name: str, /) -> T: ...


# ---------------------------------------------------------------------------
# Runtime narrowing helpers
# ---------------------------------------------------------------------------
# Patches, pydantic contexts and field values reach this module untyped. A
# bare ``isinstance(x, dict)`` narrows to ``dict[Unknown, Unknown]``; these
# helpers narrow to ``object`` elements instead, so code that uses the
# elements still has to check their types.


def _is_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _is_dict(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_json_object(value: object) -> TypeIs[JsonObject]:
    """True for a dict. Patch dicts come from JSON, so their keys are taken to be ``str``."""
    return isinstance(value, dict)


def _is_vco_model_type(tp: object) -> TypeIs[type[VcoModel]]:
    return isinstance(tp, type) and issubclass(tp, VcoModel)


def _field_value(obj: object, name: str) -> object:
    """``getattr`` typed ``object`` instead of ``Any``."""
    value: object = getattr(obj, name)
    return value


def _level_from_context(context: object) -> ConfigContext | None:
    """Read ``config_level`` from a pydantic validation or serialization context."""
    level = context.get("config_level") if _is_mapping(context) else None
    return level if isinstance(level, ConfigContext) else None


def _field_alias(field_info: FieldInfo, python_name: str) -> str:
    """Get the field's serialization key. Falls back to the camelCase alias_generator."""
    if field_info.serialization_alias is not None:
        return field_info.serialization_alias
    if field_info.alias is not None:
        return field_info.alias
    return to_camel(python_name)


# ---------------------------------------------------------------------------
# Defaults at a config level
# ---------------------------------------------------------------------------


class _DefaultSource(Enum):
    ABSENT = auto()
    """The field does not exist at this config level."""
    CONTEXT = auto()
    """A ContextDefault for this level overrides the field's default."""
    FIELD = auto()
    """The field's own default applies."""


@dataclass(frozen=True, slots=True)
class _EffectiveDefault:
    source: _DefaultSource
    value: object
    """``MISSING`` for ABSENT, the resolved context default for CONTEXT, or
    ``FieldInfo.default`` for FIELD (``PydanticUndefined`` if the field has none,
    which includes fields that only have a default_factory)."""


def _context_default_entry(
    field_info: FieldInfo, level: ConfigContext | None
) -> object:
    """Get the field's raw ContextDefaults entry for *level*. Factories are not called.

    Returns ``MISSING`` (absent at this level), a value or factory, or
    ``CONTEXT_NOT_SET`` when there is no level, no marker, or no entry for it.
    """
    if level is None:
        return CONTEXT_NOT_SET
    ctx_defaults = get_context_defaults(field_info)
    if ctx_defaults is None:
        return CONTEXT_NOT_SET
    return ctx_defaults.for_level(level)


def _effective_default(
    field_info: FieldInfo, level: ConfigContext | None
) -> _EffectiveDefault:
    """Work out what the field defaults to at *level*."""
    entry = _context_default_entry(field_info, level)
    if entry is MISSING:
        return _EffectiveDefault(_DefaultSource.ABSENT, MISSING)
    if entry is not CONTEXT_NOT_SET:
        return _EffectiveDefault(_DefaultSource.CONTEXT, resolve_ctx_default(entry))
    field_default: object = field_info.default
    return _EffectiveDefault(_DefaultSource.FIELD, field_default)


def _is_context_absent(
    value: object, field_info: FieldInfo, level: ConfigContext | None
) -> bool:
    """True if the value is MISSING or the field does not exist at *level*."""
    return value is MISSING or _context_default_entry(field_info, level) is MISSING


def _field_default(field_info: FieldInfo) -> object:
    """Get the field's static default or a new default_factory value.

    Returns ``PydanticUndefined`` if the field has neither, or if its
    factory needs validated data.
    """
    static_default: object = field_info.default
    if static_default is not PydanticUndefined:
        return static_default
    if (
        field_info.default_factory is None
        or field_info.default_factory_takes_validated_data
    ):
        return PydanticUndefined
    produced: object = field_info.get_default(call_default_factory=True)
    return produced


def _equal_after_normalizing(
    a: object, b: object, norm: NormalizerFn | None, level: ConfigContext | None
) -> bool:
    """Compare two values, running the normalizer on both first if there is one.

    Values that cannot be normalized or compared count as not equal.
    """
    try:
        if norm is not None:
            return norm(a, level) == norm(b, level)
        return a == b
    except (TypeError, ValueError):
        return False


def _matches_field_default(value: object, field_info: FieldInfo) -> bool:
    """Check if value equals the field's own default (static or factory-produced).

    No config level is active on this path, so the normalizer gets ``None``.
    """
    default = _field_default(field_info)
    if default is PydanticUndefined:
        return False
    if _equal_after_normalizing(value, default, get_normalizer(field_info), None):
        return True
    # VcoModels that store the same state in different forms can compare
    # unequal. Their compact dumps remove those differences.
    if isinstance(value, VcoModel) and isinstance(default, VcoModel):
        return _compact_dump(
            value, by_alias=False, inherited_level=None
        ) == _compact_dump(default, by_alias=False, inherited_level=None)
    return False


def _should_omit_field(
    value: object, field_info: FieldInfo, level: ConfigContext | None
) -> bool:
    """Decide whether compact serialization leaves a field out.

    The field is left out if, checked in this order:
    1. the value is MISSING
    2. the field is absent at *level*
    3. the value equals the effective default at *level* (normalizer applied
       when present)
    """
    if value is MISSING:
        return True
    default = _effective_default(field_info, level)
    match default.source:
        case _DefaultSource.ABSENT:
            return True
        case _DefaultSource.CONTEXT:
            norm = get_normalizer(field_info)
            return _equal_after_normalizing(value, default.value, norm, level)
        case _DefaultSource.FIELD:
            return _matches_field_default(value, field_info)


# ---------------------------------------------------------------------------
# Compact serialization
# ---------------------------------------------------------------------------


def _compact_dump(
    model: VcoModel, *, by_alias: bool, inherited_level: ConfigContext | None
) -> JsonObject:
    """Dump *model* in compact form.

    The config level is *inherited_level* (from the caller's context or an
    enclosing model) if given, else the model's own. Nested models use the
    same level.
    """
    level = inherited_level if inherited_level is not None else model.config_context

    result: JsonObject = {}
    for name, field_info in type(model).model_fields.items():
        value = _field_value(model, name)
        if _should_omit_field(value, field_info, level):
            continue
        key = _field_alias(field_info, name) if by_alias else name
        result[key] = _compact_value(value, by_alias=by_alias, level=level)

    extra: Mapping[str, object] = model.model_extra or {}
    for key, value in extra.items():
        result[key] = _compact_value(value, by_alias=by_alias, level=level)
    return result


def _compact_value(
    value: object, *, by_alias: bool, level: ConfigContext | None
) -> object:
    """Compact a field value, recursing into VcoModels, lists and dicts."""
    if isinstance(value, VcoModel):
        return _compact_dump(value, by_alias=by_alias, inherited_level=level)
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=by_alias)
    if _is_list(value):
        return [_compact_value(v, by_alias=by_alias, level=level) for v in value]
    if _is_dict(value):
        return {
            k: _compact_value(v, by_alias=by_alias, level=level)
            for k, v in value.items()
        }
    return value


def _propagate_context(value: object, level: ConfigContext) -> None:
    """Set the config level on every VcoModel inside *value*, at any depth."""
    if isinstance(value, VcoModel):
        _ = value.for_context(level)
    elif _is_list(value):
        for item in value:
            _propagate_context(item, level)
    elif _is_dict(value):
        for item in value.values():
            _propagate_context(item, level)


# ---------------------------------------------------------------------------
# VcoModel
# ---------------------------------------------------------------------------


class VcoModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=to_camel,
        use_enum_values=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    _config_context: ConfigContext | None = PrivateAttr(default=None)

    @override
    def model_post_init(self, validation_context: object, /) -> None:
        level = _level_from_context(validation_context)
        if level is not None:
            self._config_context = level

    @override
    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        super().__pydantic_on_complete__()
        cls._alias_map.cache_clear()
        apply_vco_markers(cls)

    @property
    def config_context(self) -> ConfigContext | None:
        """The config level set on this instance, if any."""
        return self._config_context

    def for_context(self, ctx: ConfigContext) -> Self:
        """Set config context on this model and all nested VcoModel instances."""
        self._config_context = ctx
        for name in type(self).model_fields:
            _propagate_context(_field_value(self, name), ctx)
        extra: Mapping[str, object] = self.model_extra or {}
        for value in extra.values():
            _propagate_context(value, ctx)
        return self

    def for_edge(self) -> Self:
        """Shorthand for ``for_context(ConfigContext.EDGE)``."""
        return self.for_context(ConfigContext.EDGE)

    def for_profile(self) -> Self:
        """Shorthand for ``for_context(ConfigContext.PROFILE)``."""
        return self.for_context(ConfigContext.PROFILE)

    @model_serializer(mode="wrap")
    def _serialize(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> JsonObject:
        context: object = info.context
        context_level = _level_from_context(context)
        # info.by_alias is typed bool, but pydantic passes None when the caller
        # didn't set by_alias. The two modes treat None differently (see below).
        if _is_mapping(context) and "compact" in context:
            # Compact dumps use python names unless by_alias=True is passed.
            return _compact_dump(
                self,
                by_alias=info.by_alias is True,
                inherited_level=context_level,
            )

        # merge_patch sets fields without validation, so a field can hold a
        # value of an unexpected type. Hide pydantic's
        # PydanticSerializationUnexpectedValue warnings for those.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            dumped: JsonObject = handler(self)

        # Full dump: drop fields that are absent at the active level, but keep
        # every other value, defaults included. Unlike compact mode, nested
        # models don't inherit this model's level; pydantic serializes them
        # with the same context, and each one falls back to its own level.
        level = context_level if context_level is not None else self.config_context
        if level is None:
            return dumped

        # Full dumps follow model_config (serialize_by_alias=True) unless
        # by_alias=False is passed.
        by_alias = info.by_alias is not False
        for name, field_info in type(self).model_fields.items():
            if _is_context_absent(_field_value(self, name), field_info, level):
                key = _field_alias(field_info, name) if by_alias else name
                _ = dumped.pop(key, None)
        return dumped

    @classmethod
    def compact_load(cls, data: Mapping[str, object]) -> Self:
        """Hydrate a model from compact form, filling defaults."""
        return cls.model_validate(data)

    @classmethod
    def context(cls, config_context: ConfigContext) -> ContextView:
        """Get a context-specific view of field metadata."""
        return ContextView(cls, config_context)

    @classmethod
    @functools.cache
    def _alias_map(cls) -> dict[str, str]:
        """Map each accepted input key to its python field name. Cached per class.

        Accepted keys: the python name, its aliases, and its camelCase form.
        """
        alias_map: dict[str, str] = {}
        for python_name, fi in cls.model_fields.items():
            alias_map[python_name] = python_name
            if fi.serialization_alias:
                alias_map[fi.serialization_alias] = python_name
            if isinstance(fi.validation_alias, str):
                alias_map[fi.validation_alias] = python_name
            alias_map[to_camel(python_name)] = python_name
        return alias_map

    def merge_patch(
        self,
        patch: Mapping[str, object],
        *,
        resolvers: IdentityResolver | None = None,
    ) -> Self:
        """Recursively merge a partial dict into this model instance.

        - Dict values merge recursively into sub-models.
        - KeyField arrays merge by key (add/update). null values delete.
        - OverrideFlag fields auto-set to True when siblings change.
        - Resolvable fields resolve names to IDs via resolvers.
        - MISSING / absent keys are left unchanged.
        - Keys prefixed with '_' are treated as merge directives and stripped.

        Mutates self in-place and returns self for chaining.
        """
        fields = type(self).model_fields
        alias_map = type(self)._alias_map()

        # (python_name, value) for each patch key that names a field here.
        # Directives and unknown keys are dropped.
        updates = [
            (alias_map[key], value)
            for key, value in patch.items()
            if not key.startswith("_") and key in alias_map
        ]
        # Copy the touched fields first so OverrideFlag logic can see what changed.
        before: JsonObject = {
            name: copy.deepcopy(_field_value(self, name)) for name, _ in updates
        }

        for name, value in updates:
            _merge_field(self, name, fields[name], value, resolvers)

        _auto_set_override_flags(self, before)
        return self


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
    """Resolve *name* to an ID through the field's Resolvable marker.

    Returns *name* unchanged if the field is not Resolvable. Raises
    ``LookupError`` if the resolver doesn't know the name.
    """
    resolvable = get_resolvable(field_info)
    if resolvable is None:
        return name
    identity = resolvers.resolve_sync(resolvable.identity_type, name)
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
    existing items.
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

    for key, item_patch in patch.items():
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


def _new_keyed_item(
    parent: VcoModel,
    item_type: _ListItemType,
    patch: JsonObject,
    key_path: str,
    key: str,
    resolvers: IdentityResolver | None,
) -> VcoModel:
    """Build a new KeyField list item from its patch, with the key value written in."""
    item_patch = _inject_key_path(patch, key_path, key)
    if resolvers is not None:
        item_patch = _pre_resolve(item_type.fields, item_patch, resolvers)
    item = item_type.validate(item_patch)
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

    - resolves string values on Resolvable fields (unknown names are left as is)
    - turns KeyField dict patches into ordered item lists
    - recurses into list items and sub-model dicts
    - fills Resolvable fields that are missing, using the patch's own string
      values as names to look up (e.g. a key ``name`` written in by
      ``_inject_key_path`` gives ``segment_id`` / ``segment_logical_id``)
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
                except LookupError:
                    pass  # leave it for pydantic to accept or reject
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

    candidate_names = [v for v in resolved.values() if isinstance(v, str)]
    if not candidate_names:
        return resolved
    for field_name, fi in fields.items():
        if get_resolvable(fi) is None:
            continue
        if any(key in resolved for key in _input_keys(fi, field_name)):
            continue
        for candidate in candidate_names:
            try:
                resolved[field_name] = _resolve_name(fi, candidate, resolvers)
            except LookupError:
                continue
            break

    return resolved


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


# ---------------------------------------------------------------------------
# Annotation introspection
# ---------------------------------------------------------------------------

_UNION_ORIGINS = (Union, types.UnionType)  # pyright: ignore[reportDeprecated]


def _union_members(annotation: object) -> tuple[object, ...]:
    """Get the members of a Union annotation, or ``(annotation,)`` if it isn't one."""
    # On Python < 3.14, ``typing.Union[...]`` and PEP 604 ``X | Y`` have
    # distinct origins. ``T | MISSING`` produces a ``typing.Union`` because
    # MISSING is a sentinel instance rather than a class.
    if get_origin(annotation) in _UNION_ORIGINS:
        return get_args(annotation)
    return (annotation,)


def _unwrap_annotated(tp: object) -> object:
    """Strip ``Annotated[...]`` wrappers, returning the underlying type."""
    while get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    return tp


def _field_accepts_missing(fi: FieldInfo) -> bool:
    """Check if a field's type annotation includes MISSING."""
    return MISSING in _union_members(fi.annotation)


def _field_model_type(fi: FieldInfo) -> type[VcoModel] | None:
    """Get the VcoModel class from an annotation like ``T`` or ``T | MISSING | None``."""
    return next(
        (m for m in _union_members(fi.annotation) if _is_vco_model_type(m)), None
    )


@dataclass(frozen=True)
class _ListItemType:
    """The VcoModel element type of a ``list[...]`` field.

    It is either a single model class or a discriminated union of them. In
    the union case ``validate`` lets pydantic's discriminator pick the
    variant. ``fields`` comes from the first variant. Variants are expected to
    share the Resolvable / KeyField fields that ``_pre_resolve`` looks at.
    """

    annotation: object
    """The element annotation as written, kept so the discriminator applies."""
    variants: tuple[type[VcoModel], ...]

    @functools.cached_property
    def _union_adapter(self) -> TypeAdapter[VcoModel]:
        return TypeAdapter[VcoModel](self.annotation)  # pyright: ignore[reportArgumentType]

    def validate(self, value: object) -> VcoModel:
        if len(self.variants) == 1:
            return self.variants[0].model_validate(value)
        return self._union_adapter.validate_python(value)

    @property
    def fields(self) -> dict[str, FieldInfo]:
        return self.variants[0].model_fields


def _list_item_type(fi: FieldInfo) -> _ListItemType | None:
    """Get the VcoModel element type of a ``list[T]`` field, or None.

    Handles plain ``list[Model]`` and
    ``list[Annotated[A | B, Discriminator(...)]]``.
    """
    for member in _union_members(fi.annotation):
        if get_origin(member) is not list or not (args := get_args(member)):
            continue
        element: object = args[0]
        inner = _unwrap_annotated(element)
        if _is_vco_model_type(inner):
            return _ListItemType(inner, (inner,))

        variants = tuple(
            v
            for v in map(_unwrap_annotated, _union_members(inner))
            if _is_vco_model_type(v)
        )
        if len(variants) >= 2:
            return _ListItemType(element, variants)
        if len(variants) == 1:
            return _ListItemType(variants[0], variants)
    return None


# ---------------------------------------------------------------------------
# Context introspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextFieldInfo:
    """Context-specific metadata for a single field."""

    name: str
    is_present: bool
    default: object
    """The context default, the field's own static default (``PydanticUndefined``
    if it has none), or ``MISSING`` when the field is absent at this level."""
    field_info: FieldInfo


class ContextView:
    """Attribute-access proxy for context-specific field metadata."""

    def __init__(self, model_cls: type[VcoModel], config_context: ConfigContext):
        self._model_cls: type[VcoModel] = model_cls
        self._context: ConfigContext = config_context
        self._cache: dict[str, ContextFieldInfo] = {}

    def _resolve(self, name: str) -> ContextFieldInfo:
        if name in self._cache:
            return self._cache[name]
        fields = self._model_cls.model_fields
        if name not in fields:
            raise AttributeError(f"{self._model_cls.__name__} has no field '{name}'")
        fi = fields[name]
        default = _effective_default(fi, self._context)
        info = ContextFieldInfo(
            name=name,
            is_present=default.source is not _DefaultSource.ABSENT,
            default=default.value,
            field_info=fi,
        )
        self._cache[name] = info
        return info

    def __getattr__(self, name: str) -> ContextFieldInfo:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._resolve(name)

    def __iter__(self) -> Iterator[tuple[str, ContextFieldInfo]]:
        for name in self._model_cls.model_fields:
            yield name, self._resolve(name)


# ---------------------------------------------------------------------------
# Paginated model
# ---------------------------------------------------------------------------


class PaginatedVcoModel(VcoModel):
    next_page_link: str | MISSING = Field(default=MISSING)  # ty: ignore[invalid-type-form]

    _max_pages: int = PrivateAttr(default=0)
    _page_has_more: bool = PrivateAttr(default=False)
    _page_next_link: str | None = PrivateAttr(default=None)


# ---------------------------------------------------------------------------
# JSON-RPC types
# ---------------------------------------------------------------------------


class JsonRpcRequest[ParamsT](VcoModel):
    jsonrpc: Literal["2.0"] = Field(default="2.0")
    id_: Annotated[int | str | None, Alias("id")] = Field(
        default_factory=lambda: int(1000 * datetime.datetime.now().timestamp())
    )
    method: str
    params: ParamsT | None = Field(default=None)


class JsonRpcError(VcoModel):
    code: int
    message: str
    data: dict[str, object] | str | list[object] | None = Field(default=None)


class JsonRpcResponse[ResultT](VcoModel):
    jsonrpc: Literal["2.0"] = Field(default="2.0")
    id_: Annotated[int | str | None, Alias("id")] = None
    result: ResultT | None = Field(default=None)
    error: JsonRpcError | None = Field(default=None)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginationMetadata(VcoModel):
    limit: int | None = None
    more: bool
    next_page_link: str | None = Field(default=None)
    prev_page_link: str | None = Field(default=None)


class PaginatedResult[ItemT: BaseModel](VcoModel):
    metadata: Annotated[PaginationMetadata, Alias("metaData")]
    data: list[ItemT]


class RowsModifiedConfirmation(VcoModel):
    error: str | None = None
    rows: int | None = None


class InsertionConfirmation(VcoModel):
    id_: Annotated[int, Alias("id")]


# ---------------------------------------------------------------------------
# API filters
# ---------------------------------------------------------------------------


class ApiFilter(VcoModel):
    field: str = Field(validation_alias=AliasChoices("field", "fieldName"))
    operator: str = Field(validation_alias=AliasChoices("operator", "op"))
    value: str | float | list[str | float]


type ApiFiltersList = Annotated[list[ApiFilter], annotated_types.Len(min_length=1)]


class ApiFiltersOr(VcoModel):
    or_: Annotated[ApiFiltersList, Alias("or")]


class ApiFiltersAnd(VcoModel):
    and_: Annotated[ApiFiltersList, Alias("and")]


_BARE_FILTER_FIELD_KEYS = ("field", "fieldName")
_BARE_FILTER_OP_KEYS = ("operator", "op")
_GROUP_KEYS = ("and", "and_", "or", "or_")


def _looks_like_bare_filter(v: Mapping[object, object]) -> bool:
    """True if dict has a field-name key and an operator key but no group key."""
    return (
        any(k in v for k in _BARE_FILTER_FIELD_KEYS)
        and any(k in v for k in _BARE_FILTER_OP_KEYS)
        and not any(k in v for k in _GROUP_KEYS)
    )


def _api_filters_discriminator(v: object) -> str | None:
    if _is_dict(v):
        if "and_" in v or "and" in v:
            return "and"
        if "or_" in v or "or" in v:
            return "or"
        if any(k in v for k in _BARE_FILTER_FIELD_KEYS):
            return "filter"
    if isinstance(v, ApiFilter):
        return "filter"
    if isinstance(v, ApiFiltersAnd):
        return "and"
    if isinstance(v, ApiFiltersOr):
        return "or"
    return None


type ApiFilters = Annotated[
    (
        Annotated[ApiFilter, Tag("filter")]
        | Annotated[ApiFiltersAnd, Tag("and")]
        | Annotated[ApiFiltersOr, Tag("or")]
    ),
    Discriminator(_api_filters_discriminator),
]


def _wrap_bare_filter(v: object) -> object:
    """Wrap a bare single filter into an ApiFiltersAnd group.

    Some VCO endpoints reject bare single-filter payloads — they require an
    and/or group even for a single condition.  Also normalises JSON-Schema
    keywords that LLMs confuse with filter operator keys.
    """
    if isinstance(v, ApiFilter):
        return ApiFiltersAnd(and_=[v])
    if _is_dict(v):
        if "anyOf" in v or "oneOf" in v:
            return {"or": v.get("anyOf") or v.get("oneOf")}
        if "allOf" in v:
            return {"and": v["allOf"]}
        if _looks_like_bare_filter(v):
            return {"and": [v]}
    return v


type ApiGroupFilters = Annotated[
    ApiFiltersAnd | ApiFiltersOr,
    BeforeValidator(_wrap_bare_filter),
]


# ---------------------------------------------------------------------------
# Utility types
# ---------------------------------------------------------------------------


class Tinyint(IntEnum):
    ZERO = 0
    ONE = 1


_EPOCH = datetime.datetime.fromtimestamp(0, datetime.UTC)


def check_time_is_never(timestamp: object) -> object:
    """Map VCO's "never" timestamp (``0000-00-00 00:00:00``) to the Unix epoch."""
    if isinstance(timestamp, str) and timestamp.startswith("0000-00-00 00:00:00"):
        return _EPOCH

    # VCO's shared js_date_formats schema also accepts the JavaScript/HTTP
    # representation, for example "Thu Sep 10 2026 21:40:51 GMT+0000 (UTC)".
    # Convert it to the same aware datetime representation used for ISO input.
    if isinstance(timestamp, str):
        date_part, separator, timezone_label = timestamp.rpartition(" (")
        timezone_name = timezone_label[:-1] if timezone_label.endswith(")") else ""
        if separator and timezone_name.replace("_", "").isalnum():
            try:
                return datetime.datetime.strptime(
                    date_part, "%a %b %d %Y %H:%M:%S GMT%z"
                )
            except ValueError:
                pass

    return timestamp


type VcoTimestamp = Annotated[AwareDatetime, BeforeValidator(check_time_is_never)]


class ApiInterval(VcoModel):
    start: VcoTimestamp
    end: VcoTimestamp | MISSING = MISSING  # ty: ignore[invalid-type-form]


def missing_or[T, D](value: T | MISSING, default: D) -> T | D:  # ty: ignore[invalid-type-form]
    """Return *value* unless it is MISSING, in which case return *default*."""
    return default if value is MISSING else value


value_or = missing_or
"""Alias of :func:`missing_or`."""
