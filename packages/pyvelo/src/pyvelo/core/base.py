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

# merge_patch imports merge.py inside the method; merge.py imports this module.
# pyright: reportImportCycles=false

from __future__ import annotations

import datetime
import functools
import types
import warnings
from collections.abc import Iterable, Iterator, Mapping
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
    get_normalizer,
    resolve_ctx_default,
)

type JsonObject = dict[str, object]
"""A JSON object: a merge patch, a compact dump, a pydantic context, ..."""


class IdentityResolver(Protocol):
    """What ``merge_patch`` needs to turn names into IDs.

    Satisfied by ``pyvelo.config.resolver.ResolverRegistry``.
    """

    def resolve_sync[T](self, key: type[T], name: str, /) -> T: ...

    def has_id_sync(
        self, identity_type: type[object], id_attr: str, value: object, /
    ) -> bool: ...


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
        return _field_names_by_key(cls.model_fields)

    def merge_patch(
        self,
        patch: Mapping[str, object],
        *,
        resolvers: IdentityResolver | None = None,
    ) -> Self:
        """Recursively merge a partial dict into this model instance.

        - Dict values merge recursively into sub-models, or build one that is unset.
        - KeyField arrays merge by key (add/update). null values delete.
        - null unsets a field: MISSING if its type takes it, else None if it
          takes that, else ``PatchError``.
        - Every value assigned is validated as on parse; a bad one raises
          ``PatchError``.
        - A key that names no field raises ``PatchError``, at every depth.
        - Keys prefixed with '_' are directives: ``_position`` on a KeyField
          item. Any other raises ``PatchError``.
        - OverrideFlag fields auto-set to True when siblings change.
        - Resolvable fields resolve names to IDs via resolvers.
        - Absent keys are left unchanged.

        All or nothing: a patch that raises (``PatchError``, or a resolver's
        ``ResolutionError`` or ``LookupError``) leaves self as it was.
        Mutates self in-place and returns self for chaining.
        """
        from .merge import apply_patch  # merge.py imports this module

        _ = apply_patch(self, patch, resolvers)
        return self


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


def _field_names_by_key(fields: Mapping[str, FieldInfo]) -> dict[str, str]:
    """Map each input key that names one of *fields* to its python name.

    The keys: the python name, its aliases, and its camelCase form.
    """
    names: dict[str, str] = {}
    for python_name, fi in fields.items():
        names[python_name] = python_name
        if fi.serialization_alias:
            names[fi.serialization_alias] = python_name
        if isinstance(fi.validation_alias, str):
            names[fi.validation_alias] = python_name
        names[to_camel(python_name)] = python_name
    return names


def _field_accepts_missing(fi: FieldInfo) -> bool:
    """Check if a field's type annotation includes MISSING."""
    return MISSING in _union_members(fi.annotation)


def _field_accepts_none(fi: FieldInfo) -> bool:
    """Check if a field's type annotation includes None."""
    return type(None) in _union_members(fi.annotation)


def _id_type(fi: FieldInfo) -> object | None:
    """The ID type of a Resolvable field: its annotation without ``MISSING``, ``None`` and ``Annotated``.

    None unless exactly one type is left.
    """
    members = (
        _unwrap_annotated(m) for m in _union_members(_unwrap_annotated(fi.annotation))
    )
    kept = [m for m in members if m is not MISSING and m is not type(None)]
    return kept[0] if len(kept) == 1 else None


@functools.cache
def _id_adapter(id_type: object) -> TypeAdapter[object]:
    return TypeAdapter[object](id_type)  # pyright: ignore[reportArgumentType]


def _field_model_type(fi: FieldInfo) -> type[VcoModel] | None:
    """Get the VcoModel class from an annotation like ``T`` or ``T | MISSING | None``."""
    return next(
        (m for m in _union_members(fi.annotation) if _is_vco_model_type(m)), None
    )


def _field_model_types(fi: FieldInfo) -> tuple[type[VcoModel], ...]:
    """Every VcoModel class a field's value can be, in the order written.

    Handles ``T | MISSING | None`` and (discriminated) unions of models, also
    inside ``Annotated``: ``Annotated[A | B, Discriminator(...)] | None``.
    """
    found: list[type[VcoModel]] = []
    for member in _union_members(fi.annotation):
        for inner in _union_members(_unwrap_annotated(member)):
            model = _unwrap_annotated(inner)
            if _is_vco_model_type(model) and model not in found:
                found.append(model)
    return tuple(found)


def _merged_fields(variants: Iterable[type[VcoModel]]) -> dict[str, FieldInfo]:
    """The fields of every one of *variants*; for a name two have, the first's."""
    fields: dict[str, FieldInfo] = {}
    for variant in variants:
        for name, fi in variant.model_fields.items():
            _ = fields.setdefault(name, fi)
    return fields


def _variant_names(variants: Iterable[type[VcoModel]]) -> str:
    """The class names of *variants*, joined with `` | ``."""
    return " | ".join(variant.__name__ for variant in variants)


@dataclass(frozen=True)
class _ListItemType:
    """The VcoModel element type of a ``list[...]`` field.

    It is either a single model class or a discriminated union of them. In
    the union case ``validate`` lets pydantic's discriminator pick the
    variant. ``fields`` are the fields of every variant; where two variants
    have a field of one name, the first variant's wins. Variants are expected
    to share the Resolvable / KeyField fields that ``_build_input`` looks at.
    """

    annotation: object
    """The element annotation as written, kept so the discriminator applies."""
    variants: tuple[type[VcoModel], ...]

    @functools.cached_property
    def _union_adapter(self) -> TypeAdapter[VcoModel]:
        return TypeAdapter[VcoModel](self.annotation)  # pyright: ignore[reportArgumentType]

    def validate(self, value: object, *, context: JsonObject | None = None) -> VcoModel:
        if len(self.variants) == 1:
            return self.variants[0].model_validate(value, context=context)
        return self._union_adapter.validate_python(value, context=context)

    @property
    def name(self) -> str:
        """The class name, or for a union the variants' names joined with `` | ``."""
        return _variant_names(self.variants)

    @functools.cached_property
    def fields(self) -> dict[str, FieldInfo]:
        return _merged_fields(self.variants)


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
