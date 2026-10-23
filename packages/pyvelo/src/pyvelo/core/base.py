"""VcoModel base class, serialization helpers, and API types.

Provides:
- VcoModel base class with compact and context-aware serialization
- ContextView / ContextFieldInfo for field metadata introspection
- JsonRpc request/response/error models
- Pagination, API filters, and utility types
"""

from __future__ import annotations

import copy
import datetime
import functools
import types
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import (
    Annotated,
    Any,
    Literal,
    Self,
    get_args,
    get_origin,
    override,
)
from warnings import deprecated

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
    Tag,
    TypeAdapter,
    ValidationError,
    model_serializer,
)
from pydantic.alias_generators import to_camel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .fields import (
    _CONTEXT_NOT_SET,
    Alias,
    ConfigContext,
    _apply_vco_markers,
    _get_context_defaults,
    _get_normalizer,
    _resolve_ctx_default,
)

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _matches_field_default(value: Any, field_info: FieldInfo) -> bool:
    """Check if value equals the field's default (static or factory-produced).

    When a normalizer is present, both the value and the default are
    normalized before comparison (with ctx=None since this path has
    no active config context).

    For VcoModel instances, raw equality may fail when fields hold different
    representations of the same state. In that case we fall back to comparing
    compact_dump() outputs which normalise away variants.
    """
    norm = _get_normalizer(field_info)
    if field_info.default is not PydanticUndefined:
        try:
            v = norm(value, None) if norm else value
            d = norm(field_info.default, None) if norm else field_info.default
            return v == d
        except TypeError, ValueError:
            return False
    if field_info.default_factory is not None:
        try:
            default = field_info.default_factory()  # pyright: ignore[reportCallIssue]  # ty: ignore[missing-argument]
            v = norm(value, None) if norm else value
            d = norm(default, None) if norm else default
            if v == d:
                return True
            if isinstance(value, VcoModel) and isinstance(default, VcoModel):
                ctx = {"compact": True}
                return value._compact_serialize(
                    by_alias=False, context=ctx
                ) == default._compact_serialize(by_alias=False, context=ctx)
            return False
        except TypeError, ValueError:
            return False
    return False


def _field_alias(field_info: FieldInfo, python_name: str) -> str:
    """Get serialization alias for a field, falling back to alias_generator."""
    if field_info.serialization_alias is not None:
        return field_info.serialization_alias
    if field_info.alias is not None:
        return field_info.alias
    return to_camel(python_name)


def _resolve_config_level(
    model: VcoModel, context: dict[str, Any] | None
) -> tuple[ConfigContext | None, dict[str, Any] | None]:
    """Determine the active config level from context or model state."""
    config_level: ConfigContext | None = None
    if context:
        config_level = context.get("config_level")
    if config_level is None:
        config_level = model._config_context
        if config_level is not None:
            context = (context or {}) | {"config_level": config_level}
    return config_level, context


def _is_context_absent(
    value: Any,
    field_info: FieldInfo,
    config_level: ConfigContext | None,
) -> bool:
    """Check if a field should be excluded because it's MISSING or absent at the config level.

    Returns True when:
    1. value is MISSING
    2. config_level present AND ContextDefaults says MISSING for level
    """
    if value is MISSING:
        return True
    if config_level is not None:
        ctx_defaults = _get_context_defaults(field_info)
        if ctx_defaults is not None:
            ctx_default = ctx_defaults.for_level(config_level)
            if ctx_default is MISSING:
                return True
    return False


class _CtxDefaultStatus(StrEnum):
    ABSENT = "absent"
    CONTEXT_DEFAULT = "context"
    FIELD_DEFAULT = "field"


def _classify_context_default(
    field_info: FieldInfo, config_level: ConfigContext | None
) -> tuple[_CtxDefaultStatus, Any]:
    """Classify how a field's default should be resolved in a given context.

    Returns (status, resolved_default) where:
    - ABSENT / MISSING: field not present at this level
    - CONTEXT_DEFAULT / value: context-specific default exists
    - FIELD_DEFAULT / field default or PydanticUndefined: use field's own default
    """
    ctx_defaults = _get_context_defaults(field_info)
    if config_level is not None and ctx_defaults is not None:
        ctx_val = ctx_defaults.for_level(config_level)
        if ctx_val is MISSING:
            return _CtxDefaultStatus.ABSENT, MISSING
        if ctx_val is not _CONTEXT_NOT_SET:
            return _CtxDefaultStatus.CONTEXT_DEFAULT, _resolve_ctx_default(ctx_val)
    default = (
        field_info.default
        if field_info.default is not PydanticUndefined
        else PydanticUndefined
    )
    return _CtxDefaultStatus.FIELD_DEFAULT, default


def _should_omit_field(
    value: Any,
    field_info: FieldInfo,
    config_level: ConfigContext | None,
) -> bool:
    """Decide whether a field should be omitted during compact serialization.

    Omission rules (in order):
    1. value is MISSING -> omit
    2. config_level present AND ContextDefaults says MISSING for level -> omit
    3. Value matches effective default (context-specific or field default) -> omit
       (normalizer applied when present)
    """
    # Rules 1 & 2
    if _is_context_absent(value, field_info, config_level):
        return True

    # 3. Default matching
    status, resolved_default = _classify_context_default(field_info, config_level)
    if status is _CtxDefaultStatus.CONTEXT_DEFAULT:
        norm = _get_normalizer(field_info)
        try:
            if norm:
                if norm(value, config_level) == norm(resolved_default, config_level):
                    return True
            elif value == resolved_default:
                return True
        except TypeError, ValueError:
            pass
    else:
        if _matches_field_default(value, field_info):
            return True

    return False


def _compact_value_ctx(
    value: Any, by_alias: bool, context: dict[str, Any] | None
) -> Any:
    """Recursively compact a value for serialization (context-aware)."""
    if isinstance(value, VcoModel):
        return value._compact_serialize(by_alias=by_alias, context=context)
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=by_alias)
    if isinstance(value, list):
        return [_compact_value_ctx(item, by_alias, context) for item in value]
    if isinstance(value, dict):
        return {k: _compact_value_ctx(v, by_alias, context) for k, v in value.items()}
    return value


def _propagate_context(value: Any, ctx: ConfigContext) -> None:
    """Recursively set _config_context on nested VcoModel instances."""
    if isinstance(value, VcoModel):
        value.for_context(ctx)
    elif isinstance(value, list):
        for item in value:
            _propagate_context(item, ctx)
    elif isinstance(value, dict):
        for v in value.values():
            _propagate_context(v, ctx)


# ---------------------------------------------------------------------------
# VcoModel
# ---------------------------------------------------------------------------


class VcoModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        use_enum_values=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    _config_context: ConfigContext | None = PrivateAttr(default=None)

    @override
    def model_post_init(self, __context: Any) -> None:
        if isinstance(__context, dict):
            config_level = __context.get("config_level")
            if config_level is not None:
                self._config_context = config_level

    @override
    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        super().__pydantic_on_complete__()
        cls._alias_map.cache_clear()
        _apply_vco_markers(cls)

    def for_context(self, ctx: ConfigContext) -> Self:
        """Set config context on this model and all nested VcoModel instances."""
        self._config_context = ctx
        for name in type(self).model_fields:
            value = getattr(self, name)
            if value is MISSING:
                continue
            _propagate_context(value, ctx)
        if hasattr(self, "model_extra") and self.model_extra:
            for v in self.model_extra.values():
                _propagate_context(v, ctx)
        return self

    def for_edge(self) -> Self:
        """Shorthand for ``for_context(ConfigContext.EDGE)``."""
        return self.for_context(ConfigContext.EDGE)

    def for_profile(self) -> Self:
        """Shorthand for ``for_context(ConfigContext.PROFILE)``."""
        return self.for_context(ConfigContext.PROFILE)

    def _compact_serialize(
        self, *, by_alias: bool, context: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Core compact serialization with optional context awareness."""
        config_level, context = _resolve_config_level(self, context)

        result: dict[str, Any] = {}
        for name, field_info in type(self).model_fields.items():
            value = getattr(self, name)
            if _should_omit_field(value, field_info, config_level):
                continue
            key = _field_alias(field_info, name) if by_alias else name
            result[key] = _compact_value_ctx(value, by_alias, context)

        if hasattr(self, "model_extra") and self.model_extra:
            for k, v in self.model_extra.items():
                result[k] = _compact_value_ctx(v, by_alias, context)
        return result

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any, info: SerializationInfo) -> dict[str, Any]:
        ctx = info.context
        if ctx and "compact" in ctx:
            by_alias = info.by_alias if info.by_alias is not None else False
            return self._compact_serialize(by_alias=by_alias, context=ctx)

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = handler(self)

        # Context-aware field exclusion (without compact default omission).
        # Fields whose context_defaults mark them MISSING at the active level
        # are removed, but all other values (including defaults) are kept.
        config_level, _ = _resolve_config_level(self, ctx)
        if config_level is None:
            return result

        # serialize_by_alias=True in model_config, so default is True
        effective_by_alias = info.by_alias if info.by_alias is not None else True
        for name, field_info in type(self).model_fields.items():
            value = getattr(self, name)
            if _is_context_absent(value, field_info, config_level):
                key = _field_alias(field_info, name) if effective_by_alias else name
                result.pop(key, None)

        return result

    @deprecated(
        "compact_dump() is deprecated. Use model_dump(by_alias=False, context={'compact': True}) instead."
    )
    def compact_dump(self) -> dict[str, Any]:
        return self._compact_serialize(by_alias=False, context={"compact": True})

    @classmethod
    def compact_load(cls, data: dict[str, Any]) -> Self:
        """Hydrate a model from compact form, filling defaults."""
        return cls.model_validate(data)

    @classmethod
    def context(cls, config_context: ConfigContext) -> ContextView:
        """Get a context-specific view of field metadata."""
        return ContextView(cls, config_context)

    @classmethod
    @functools.cache
    def _alias_map(cls) -> dict[str, str]:
        """Cached alias -> python_name mapping for camelCase input support."""
        alias_map: dict[str, str] = {}
        for python_name, fi in cls.model_fields.items():
            alias_map[python_name] = python_name
            if fi.serialization_alias:
                alias_map[fi.serialization_alias] = python_name
            if fi.validation_alias and isinstance(fi.validation_alias, str):
                alias_map[fi.validation_alias] = python_name
            alias_map[to_camel(python_name)] = python_name
        return alias_map

    def merge_patch(
        self,
        patch: dict[str, Any],
        *,
        resolvers: Any | None = None,
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
        from .fields import get_key_field

        fields = type(self).model_fields
        alias_map = type(self)._alias_map()

        # Resolve patch keys to python field names for snapshot
        patch_field_names: set[str] = set()
        for key in patch:
            if key.startswith("_"):
                continue
            resolved = alias_map.get(key)
            if resolved and resolved in fields:
                patch_field_names.add(resolved)

        # Snapshot values before merge for change detection
        snapshot: dict[str, Any] = {}
        for name in patch_field_names:
            snapshot[name] = copy.deepcopy(getattr(self, name))

        for key, value in patch.items():
            # Skip merge directives
            if key.startswith("_"):
                continue

            # Resolve alias to python field name
            python_name = alias_map.get(key)
            if python_name is None:
                continue  # unknown field, skip

            if python_name not in fields:
                continue

            fi = fields[python_name]

            # null -> MISSING for MISSING-typed fields, None otherwise
            if value is None:
                if _field_accepts_missing(fi):
                    setattr(self, python_name, MISSING)
                else:
                    setattr(self, python_name, None)
                continue

            current = getattr(self, python_name)

            # Dict value + VcoModel field -> recursive merge
            if isinstance(value, dict) and isinstance(current, VcoModel):
                current.merge_patch(value, resolvers=resolvers)
                continue

            # Dict value + VcoModel field type, but current is MISSING/None ->
            # construct a fresh instance and merge the patch into it.
            # This preserves KeyField handling (which model_validate
            # wouldn't do).  If the default constructor fails (e.g.
            # required fields), fall back to model_validate.
            if isinstance(value, dict) and (current is MISSING or current is None):
                model_type = _get_field_vcomodel_type(fi)
                if model_type is not None:
                    try:
                        new_instance = model_type()
                    except ValidationError:
                        setattr(self, python_name, model_type.model_validate(value))
                        continue
                    new_instance.merge_patch(value, resolvers=resolvers)
                    setattr(self, python_name, new_instance)
                    continue

            # Dict value + KeyField list -> keyed merge
            if isinstance(value, dict) and get_key_field(fi) is not None:
                _merge_keyed_list(self, python_name, fi, value, resolvers=resolvers)
                continue

            # Dict value + plain list[VcoModel] field (no KeyField) ->
            # coerce dict values to a list so we don't assign a dict to
            # a list field (which would corrupt the data shape and break
            # roundtrip deserialization).
            if isinstance(value, dict) and _get_list_item_type(fi) is not None:
                item_type = _get_list_item_type(fi)
                assert item_type is not None  # for type narrowing
                value = [
                    item_type.model_validate(v) if isinstance(v, dict) else v
                    for v in value.values()
                ]

            # Resolvable: string on an ID field -> resolve name to ID
            if isinstance(value, str) and resolvers is not None:
                resolved = _try_resolve(fi, value, resolvers)
                if resolved is not _UNRESOLVED:
                    value = resolved

            # List of dicts + list[VcoModel] field -> validate items
            if isinstance(value, list):
                item_type = _get_list_item_type(fi)
                if item_type is not None:
                    value = [
                        (
                            item_type.model_validate(item)
                            if isinstance(item, dict)
                            else item
                        )
                        for item in value
                    ]

            # Scalar assignment
            setattr(self, python_name, value)

        # Post-merge: auto-set override flags
        _auto_set_override_flags(self, snapshot, patch_field_names)

        return self


# ---------------------------------------------------------------------------
# Context introspection
# ---------------------------------------------------------------------------


def _iter_union_args(annotation: Any) -> tuple[Any, ...]:
    """Extract type args from a Union/UnionType annotation, or return (annotation,)."""
    if get_origin(annotation) is types.UnionType:
        return get_args(annotation)
    return (annotation,)


def _field_accepts_missing(fi: FieldInfo) -> bool:
    """Check if a field's type annotation includes MISSING."""
    return MISSING in _iter_union_args(fi.annotation)


def _get_field_vcomodel_type(fi: FieldInfo) -> type[VcoModel] | None:
    """Extract the VcoModel subclass from a field annotation like ``T | MISSING``."""
    for arg in _iter_union_args(fi.annotation):
        if isinstance(arg, type) and issubclass(arg, VcoModel):
            return arg
    return None


def _resolve_key_path(obj: Any, key_path: str) -> Any:
    """Navigate a dotted path on an object (e.g. 'segment.name')."""
    for part in key_path.split("."):
        obj = getattr(obj, part)
    return obj


def _merge_keyed_list(
    model: VcoModel,
    field_name: str,
    field_info: FieldInfo,
    patch: dict[str, Any],
    *,
    resolvers: Any | None = None,
) -> None:
    """Merge a dict patch into a KeyField list.

    Dict keys are matched against the key_path value on existing list items.
    - Matched + dict value: recursive merge into matched item.
    - Matched + None: delete item from list.
    - Unmatched + dict value: construct new item, append (or at _position).
    """
    from .fields import get_key_field

    key_path = get_key_field(field_info)
    if key_path is None:
        raise ValueError(f"KeyField key_path is None for field '{field_name}'")

    current_list: list[Any] | MISSING = getattr(model, field_name)
    missing_current_list = current_list is MISSING
    if missing_current_list:
        current_list = []

    # Get the list item type from the field annotation
    item_type = _get_list_item_type(field_info)

    for key_value, item_patch in patch.items():
        # Find existing item by key
        existing_idx = None
        for idx, item in enumerate(current_list):
            if str(_resolve_key_path(item, key_path)) == str(key_value):
                existing_idx = idx
                break

        if item_patch is None:
            # Delete
            if existing_idx is not None:
                current_list.pop(existing_idx)
            continue

        if not isinstance(item_patch, dict):
            continue

        # Extract merge directives
        position = item_patch.pop("_position", None)
        # Strip other directives
        item_patch = {k: v for k, v in item_patch.items() if not k.startswith("_")}

        if existing_idx is not None:
            # Update existing item
            existing_item = current_list[existing_idx]
            existing_item.merge_patch(item_patch, resolvers=resolvers)

            # Handle position change
            if position is not None:
                current_list.pop(existing_idx)
                insert_idx = _resolve_position(current_list, key_path, position)
                current_list.insert(insert_idx, existing_item)
        else:
            # Create new item — inject the key value into the patch
            if item_type is not None:
                item_patch = _inject_key_path(item_patch, key_path, key_value)
                if resolvers is not None:
                    item_patch = _pre_resolve(item_type, item_patch, resolvers)
                new_item = item_type.model_validate(item_patch)
                # Propagate parent's config context so context-aware fills
                # can see the active level (EDGE vs PROFILE).
                if model._config_context is not None:
                    new_item.for_context(model._config_context)
                # Fill MISSING fields whose ContextDefaults specify a
                # non-MISSING default at the active config level (e.g.
                # profile segments expect ospf/syslog present).
                _fill_missing_submodels(new_item)
            else:
                continue
            insert_idx = _resolve_position(current_list, key_path, position)
            current_list.insert(insert_idx, new_item)

    if len(current_list) > 0 and missing_current_list:
        setattr(model, field_name, current_list)


def _fill_missing_submodels(item: VcoModel) -> None:
    """Fill MISSING fields whose ContextDefaults specify a default at the active level.

    When a new keyed-list item is created via model_validate, fields typed
    as ``SomeModel | MISSING`` default to MISSING.  Some contexts (e.g.
    PROFILE-level segments) require certain sub-structures present (ospf,
    syslog, ...).  We honour those requirements only when the field's
    ContextDefaults explicitly declares a non-MISSING default for the
    active config level — otherwise the declared MISSING is preserved so
    EDGE-only patches don't leak unwanted sub-structures.
    """
    config_level = item._config_context
    if config_level is None:
        return
    for field_name, fi in type(item).model_fields.items():
        if getattr(item, field_name) is not MISSING:
            continue
        status, default = _classify_context_default(fi, config_level)
        if status is not _CtxDefaultStatus.CONTEXT_DEFAULT:
            continue
        if default is MISSING or default is PydanticUndefined:
            continue
        setattr(item, field_name, default)


def _inject_key_path(
    patch: dict[str, Any], key_path: str, key_value: str
) -> dict[str, Any]:
    """Inject the key value into a patch dict at the key path location.

    For simple keys like ``"name"``, sets ``patch["name"] = key_value``.
    For dotted paths like ``"segment.name"``, builds nested dicts:
    ``patch["segment"] = {"name": key_value, **existing}``.
    """
    parts = key_path.split(".")
    result = dict(patch)
    if len(parts) == 1:
        result.setdefault(parts[0], key_value)
    else:
        # Build nested dict from inside out
        inner = {parts[-1]: key_value}
        # Merge with any existing nested dict in the patch
        for part in reversed(parts[:-1]):
            existing = result.get(part, {})
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(inner)
                inner = merged
            result[part] = inner
    return result


_UNRESOLVED = object()


def _try_resolve(
    fi: FieldInfo, value: str, resolvers: Any, *, suppress_errors: bool = False
) -> Any:
    """Resolve a string value to an ID via the field's Resolvable marker.

    Returns the resolved ID, or ``_UNRESOLVED`` if the field has no
    Resolvable marker.  When *suppress_errors* is True, LookupError
    from the resolver is caught and ``_UNRESOLVED`` is returned;
    otherwise the exception propagates.
    """
    from .fields import get_resolvable

    resolvable = get_resolvable(fi)
    if resolvable is None:
        return _UNRESOLVED
    try:
        identity = resolvers.resolve_sync(resolvable.identity_type, value)
        return getattr(identity, resolvable.id_attr)
    except LookupError:
        if suppress_errors:
            return _UNRESOLVED
        raise


def _pre_resolve(
    item_type: type[VcoModel] | _DiscriminatedListItem,
    patch: dict[str, Any],
    resolvers: Any,
) -> dict[str, Any]:
    """Resolve Resolvable string values in *patch* before model_validate.

    When creating new keyed-list items, merge_patch isn't called so
    Resolvable fields (e.g. segment_id: "Global Segment") would hit
    Pydantic validation as raw strings.  This pre-resolves them.

    Also recurses into nested dicts that correspond to VcoModel sub-fields.
    """
    from .fields import get_key_field

    resolved = dict(patch)
    for field_name, fi in item_type.model_fields.items():
        alias = fi.validation_alias or fi.alias or field_name
        for key in (field_name, alias):
            str_key = str(key)
            if str_key not in resolved:
                continue
            value = resolved[str_key]
            # Resolvable: string on an ID field -> resolve name to ID
            if isinstance(value, str):
                result = _try_resolve(fi, value, resolvers, suppress_errors=True)
                if result is not _UNRESOLVED:
                    resolved[str_key] = result
            # Dict on a KeyField list -> convert to list of items
            elif (
                isinstance(value, dict) and (key_path := get_key_field(fi)) is not None
            ):
                list_item_type = _get_list_item_type(fi)
                items = []
                for item_key, item_val in value.items():
                    if not isinstance(item_val, dict):
                        continue
                    item_dict = _inject_key_path(dict(item_val), key_path, item_key)
                    if list_item_type is not None:
                        item_dict = _pre_resolve(list_item_type, item_dict, resolvers)
                    items.append(item_dict)
                # Sort by _position if present, then strip it
                items.sort(key=lambda d: d.pop("_position", float("inf")))
                resolved[str_key] = items
            # List of dicts + list[VcoModel] field -> pre-resolve each item
            elif isinstance(value, list):
                list_item_type = _get_list_item_type(fi)
                if list_item_type is not None:
                    resolved[str_key] = [
                        (
                            _pre_resolve(list_item_type, item, resolvers)
                            if isinstance(item, dict)
                            else item
                        )
                        for item in value
                    ]
            # Recurse into nested VcoModel sub-fields
            elif isinstance(value, dict):
                sub_type = _get_field_model_type(fi)
                if sub_type is not None:
                    resolved[str_key] = _pre_resolve(sub_type, value, resolvers)

    # Auto-populate missing Resolvable fields using string values already
    # in the dict as lookup keys.  E.g. SegmentMetadata has "name" injected
    # by _inject_key_path; use it to resolve segment_id & segment_logical_id.
    if resolvers is not None:
        string_values = [v for v in resolved.values() if isinstance(v, str)]
        if string_values:
            for field_name, fi in item_type.model_fields.items():
                alias = fi.validation_alias or fi.alias or field_name
                if field_name in resolved or alias in resolved:
                    continue
                for sv in string_values:
                    result = _try_resolve(fi, sv, resolvers, suppress_errors=True)
                    if result is not _UNRESOLVED:
                        resolved[field_name] = result
                        break

    return resolved


def _get_field_model_type(fi: FieldInfo) -> type[VcoModel] | None:
    """Extract the VcoModel subclass from a field's type annotation, or None."""
    for arg in _iter_union_args(fi.annotation):
        if isinstance(arg, type) and issubclass(arg, VcoModel):
            return arg
    return None


class _DiscriminatedListItem:
    """Stand-in item type for a list element typed as a discriminated union of VcoModels.

    Exposes the two surfaces call sites use: ``model_validate`` (delegated to a
    ``TypeAdapter`` so the pydantic discriminator runs) and ``model_fields``
    (taken from the first variant — variants are expected to share a common base
    for any ``Resolvable`` / ``KeyField`` markers ``_pre_resolve`` cares about).
    """

    __slots__ = ("_adapter", "_variants")

    def __init__(self, annotation: Any, variants: tuple[type[VcoModel], ...]):
        self._adapter = TypeAdapter(annotation)
        self._variants = variants

    def model_validate(self, value: Any) -> VcoModel:
        return self._adapter.validate_python(value)

    @property
    def model_fields(self) -> dict[str, FieldInfo]:
        return self._variants[0].model_fields


def _unwrap_annotated(tp: Any) -> Any:
    """Strip ``Annotated[...]`` wrappers, returning the underlying type."""
    while hasattr(tp, "__metadata__"):
        tp = tp.__origin__
    return tp


def _get_list_item_type(
    field_info: FieldInfo,
) -> type[VcoModel] | _DiscriminatedListItem | None:
    """Extract the VcoModel item type T from a list[T] field annotation.

    Handles plain ``list[VcoModel]`` and ``list[Annotated[Union[...], Discriminator]]``
    (via :class:`_DiscriminatedListItem`).
    """
    for arg in _iter_union_args(field_info.annotation):
        origin = getattr(arg, "__origin__", None)
        if origin is not list:
            continue
        items = getattr(arg, "__args__", None)
        if not items:
            continue
        elem = items[0]
        unwrapped = _unwrap_annotated(elem)

        if isinstance(unwrapped, type) and issubclass(unwrapped, VcoModel):
            return unwrapped

        variants = tuple(
            v
            for v in (_unwrap_annotated(a) for a in _iter_union_args(unwrapped))
            if isinstance(v, type) and issubclass(v, VcoModel)
        )
        if len(variants) >= 2:
            return _DiscriminatedListItem(elem, variants)
        if len(variants) == 1:
            return variants[0]
    return None


def _resolve_position(
    current_list: list[Any],
    key_path: str,
    position: int | str | None,
) -> int:
    """Resolve a _position directive to a numeric index."""
    if position is None:
        return len(current_list)  # append

    if isinstance(position, int):
        return max(0, min(position, len(current_list)))

    if isinstance(position, str):
        if position == "end":
            return len(current_list)
        if position.startswith("before:"):
            target = position[7:]
            for idx, item in enumerate(current_list):
                if str(_resolve_key_path(item, key_path)) == target:
                    return idx
            return len(current_list)
        if position.startswith("after:"):
            target = position[6:]
            for idx, item in enumerate(current_list):
                if str(_resolve_key_path(item, key_path)) == target:
                    return idx + 1
            return len(current_list)

    return len(current_list)


def _has_non_edge_local_changes(
    current: VcoModel,
    old: dict[str, Any] | VcoModel,
    field_names: set[str] | None = None,
) -> bool:
    """Check if any non-EdgeLocal field was changed in this model or descendants.

    *old* may be a snapshot dict (top-level call from merge_patch) or a
    VcoModel (recursive sub-model comparison).  When *field_names* is
    provided, only those fields are inspected; otherwise all fields are
    checked.
    """
    from .fields import has_override_flag, is_edge_local

    fields = type(current).model_fields
    names = field_names if field_names is not None else fields.keys()
    _get_old = old.get if isinstance(old, dict) else lambda n: getattr(old, n)

    for name in names:
        if name not in fields:
            continue
        fi = fields[name]

        if has_override_flag(fi):
            continue
        if is_edge_local(fi):
            continue

        cur_val = getattr(current, name)
        old_val = _get_old(name)

        # Sub-model: recurse to check if changes are all EdgeLocal
        if isinstance(cur_val, VcoModel) and isinstance(old_val, VcoModel):
            if _has_non_edge_local_changes(cur_val, old_val):
                return True
            continue

        # Scalar or other: any change counts
        if cur_val != old_val:
            return True

    return False


def _auto_set_override_flags(
    model: VcoModel, snapshot: dict[str, Any], patch_field_names: set[str]
) -> None:
    """Set OverrideFlag fields to True if non-EdgeLocal changes detected."""
    from .fields import has_override_flag

    fields = type(model).model_fields

    # Check if any override flags exist in this model
    has_any_override = any(has_override_flag(fi) for fi in fields.values())
    if not has_any_override:
        return

    if _has_non_edge_local_changes(model, snapshot, patch_field_names):
        for name, fi in fields.items():
            if has_override_flag(fi):
                setattr(model, name, True)


@dataclass(frozen=True)
class ContextFieldInfo:
    """Context-specific metadata for a single field."""

    name: str
    is_present: bool
    default: Any
    field_info: FieldInfo


class ContextView:
    """Attribute-access proxy for context-specific field metadata."""

    def __init__(self, model_cls: type[VcoModel], config_context: ConfigContext):
        self._model_cls = model_cls
        self._context = config_context
        self._cache: dict[str, ContextFieldInfo] = {}

    def _resolve(self, name: str) -> ContextFieldInfo:
        if name in self._cache:
            return self._cache[name]
        fields = self._model_cls.model_fields
        if name not in fields:
            raise AttributeError(f"{self._model_cls.__name__} has no field '{name}'")
        fi = fields[name]
        status, default = _classify_context_default(fi, self._context)
        info = ContextFieldInfo(
            name=name,
            is_present=status is not _CtxDefaultStatus.ABSENT,
            default=default,
            field_info=fi,
        )
        self._cache[name] = info
        return info

    def __getattr__(self, name: str) -> ContextFieldInfo:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._resolve(name)

    def __iter__(self):
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
    data: dict[str, Any] | str | list[Any] | None = Field(default=None)


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


def _looks_like_bare_filter(v: dict[str, Any]) -> bool:
    """True if dict has a field-name key and an operator key but no group key."""
    return (
        any(k in v for k in _BARE_FILTER_FIELD_KEYS)
        and any(k in v for k in _BARE_FILTER_OP_KEYS)
        and not any(k in v for k in _GROUP_KEYS)
    )


def _api_filters_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
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


type ApiFilters = Annotated[
    (
        Annotated[ApiFilter, Tag("filter")]
        | Annotated[ApiFiltersAnd, Tag("and")]
        | Annotated[ApiFiltersOr, Tag("or")]
    ),
    Discriminator(_api_filters_discriminator),
]


def _wrap_bare_filter(v: Any) -> Any:
    """Wrap a bare single filter into an ApiFiltersAnd group.

    Some VCO endpoints reject bare single-filter payloads — they require an
    and/or group even for a single condition.  Also normalises JSON-Schema
    keywords that LLMs confuse with filter operator keys.
    """
    if isinstance(v, ApiFilter):
        return ApiFiltersAnd(and_=[v])
    if isinstance(v, dict):
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


def check_time_is_never(timestamp: Any) -> Any:
    @functools.cache
    def epoch_start() -> datetime.datetime:
        return datetime.datetime.fromtimestamp(0, datetime.UTC)

    if isinstance(timestamp, str) and timestamp.startswith("0000-00-00 00:00:00"):
        return epoch_start()

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


def missing_or(value: Any, default: Any) -> Any:
    """Return *value* unless it is MISSING, in which case return *default*."""
    return default if value is MISSING else value


def value_or(value: Any, default: Any) -> Any:
    """Return *value* unless it is MISSING, in which case return *default*."""
    return default if value is MISSING else value
