"""VcoModel base class, serialization helpers, and API types.

Provides:
- VcoModel base class with compact and context-aware serialization
- ``VcoModel.merge_patch``: recursive merge of a partial dict into a model
- ``VcoModel.mark_detached``: a model a write replaced refuses edits
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
# _raise_detached imports pyvelo.client.exceptions, which imports pyvelo.core.
# pyright: reportImportCycles=false

from __future__ import annotations

import datetime as dt
import functools
import sys
import types
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum, IntEnum, auto
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    Never,
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
from pydantic_core import PydanticUndefined

from .fields import (
    CONTEXT_NOT_SET,
    Alias,
    ConfigContext,
    NormalizerFn,
    apply_vco_markers,
    get_context_defaults,
    get_key_spec,
    get_normalizer,
    is_positional,
    ref_only_defaults,
    resolve_ctx_default,
)
from .owned import Owned, unwrap

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

type JsonObject = dict[str, object]
"""A JSON object: a merge patch, a compact dump, a pydantic context, ..."""


class IdentityResolver(Protocol):
    """What ``merge_patch`` needs to turn names into IDs.

    Satisfied by ``pyvelo.config.resolver.ResolverRegistry``.
    """

    def lookup[T](self, key: type[T], name: str, /) -> T:
        """The loaded identity of type *key* named *name*."""
        ...

    def lookup_by[T](
        self, identity_type: type[T], accessor: Callable[[T], object], value: object, /
    ) -> T:
        """The first loaded *identity_type* whose *accessor* gives *value*."""
        ...

    def resolver_for(self, identity_type: type[object], /) -> object | None:
        """The resolver registered for *identity_type*, or None when there is none."""
        ...


class IdentityLookup(Protocol):
    """What a derived ref needs to find a loaded identity by one of its IDs.

    Satisfied by ``pyvelo.config.resolver.ResolverRegistry``.
    """

    def lookup_by[T](
        self, identity_type: type[T], accessor: Callable[[T], object], value: object, /
    ) -> T:
        """The first loaded *identity_type* whose *accessor* gives *value*."""
        ...


# ---------------------------------------------------------------------------
# Runtime narrowing helpers
# ---------------------------------------------------------------------------
# Patches, pydantic contexts and field values reach this module untyped. A
# bare ``isinstance(x, dict)`` narrows to ``dict[Unknown, Unknown]``; these
# helpers narrow to ``object`` elements instead, so code that uses the
# elements still has to check their types.


def is_list(value: object) -> TypeIs[list[object]]:
    """Whether *value* is a list, narrowed to a list of ``object``."""
    return isinstance(value, list)


def is_dict(value: object) -> TypeIs[dict[object, object]]:
    """Whether *value* is a dict, narrowed to a dict of ``object`` keys and values."""
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
    """Get the field's serialization key.

    Falls back to the camelCase alias_generator.
    """
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
        equal = norm(a, level) == norm(b, level) if norm is not None else a == b
    except (TypeError, ValueError):
        return False
    return equal


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
    ref_only = type(model)._ref_only_fields  # pyright: ignore[reportPrivateUsage]
    for name, field_info in type(model).model_fields.items():
        if ref_only and name in ref_only:
            continue  # it stands for a ref row, not for data
        value = unwrap(_field_value(model, name))
        if _should_omit_field(value, field_info, level):
            continue
        key = _field_alias(field_info, name) if by_alias else name
        result[key] = _compact_value(value, by_alias=by_alias, level=level)

    extra: Mapping[str, object] = model.model_extra or {}
    for key, value in extra.items():
        result[key] = _compact_value(value, by_alias=by_alias, level=level)
    return model._finish_dump(  # pyright: ignore[reportPrivateUsage]
        result,
        by_alias=by_alias,
        compact_value=lambda value: _compact_value(
            value, by_alias=by_alias, level=level
        ),
    )


def _compact_value(
    value: object, *, by_alias: bool, level: ConfigContext | None
) -> object:
    """Compact a field value, recursing into VcoModels, lists and dicts."""
    if isinstance(value, VcoModel):
        return _compact_dump(value, by_alias=by_alias, inherited_level=level)
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=by_alias)
    if is_list(value):
        return [_compact_value(v, by_alias=by_alias, level=level) for v in value]
    if is_dict(value):
        return {
            k: _compact_value(v, by_alias=by_alias, level=level)
            for k, v in value.items()
        }
    return value


def same_state(a: object, b: object) -> bool:
    """Whether *a* and *b* hold the same state: equal, or equal once compacted.

    Models compare unequal by their private state, the config level among it
    (``for_profile()`` sets it on a model and every model inside), and by the
    form they hold the same state in; the compact dump removes those.
    """
    return a == b or _compact_value(a, by_alias=False, level=None) == _compact_value(
        b, by_alias=False, level=None
    )


def _propagate_context(value: object, level: ConfigContext) -> None:
    """Set the config level on every VcoModel inside *value*, at any depth."""
    if isinstance(value, VcoModel):
        _ = value.for_context(level)
    elif isinstance(value, Owned):
        _propagate_context(value.stored, level)  # pyright: ignore[reportUnknownMemberType]
    elif is_list(value):
        for item in value:
            _propagate_context(item, level)
    elif is_dict(value):
        for item in value.values():
            _propagate_context(item, level)


type _Place = tuple[str | int, ...]
"""Where a model is from another: dump keys (``str``) and list positions (``int``)."""


def _models_inside(value: object, place: _Place) -> Iterator[tuple[_Place, BaseModel]]:
    """Every pydantic model in *value*, at any depth, with its place.

    *value* is at *place*.

    Walks as ``_propagate_context`` does: fields, extra values, lists and
    dicts. A model comes before the models inside it. A field is named by its
    dump key (its alias), a dict entry by its key as text, as in a JSON dump.
    A pydantic model that is not a ``VcoModel`` is yielded and not entered.
    """
    if isinstance(value, BaseModel) and not isinstance(value, VcoModel):
        yield place, value
    elif isinstance(value, VcoModel):
        yield place, value
        for name, field_info in type(value).model_fields.items():
            key = _field_alias(field_info, name)
            yield from _models_inside(_field_value(value, name), (*place, key))
        extra: Mapping[str, object] = value.model_extra or {}
        for key, item in extra.items():
            yield from _models_inside(item, (*place, key))
    elif isinstance(value, Owned):
        yield from _models_inside(value.stored, place)  # pyright: ignore[reportUnknownMemberType]
    elif is_list(value):
        for index, item in enumerate(value):
            yield from _models_inside(item, (*place, index))
    elif is_dict(value):
        for key, item in value.items():
            text = key if isinstance(key, str) else str(key)
            yield from _models_inside(item, (*place, text))


def _raise_detached(model: VcoModel, name: str) -> Never:
    """Raise ``DetachedObjectError`` for an edit of *name* through *model*."""
    from pyvelo.client.exceptions import DetachedObjectError  # noqa: PLC0415 (it imports pyvelo.core)

    raise DetachedObjectError(
        f"{type(model).__name__}.{name}: this object was replaced by a write "
        "or a revert; take it again from the module the state holds now "
        "(after a save or a revert: state.edge.<module> or state.profile.<module>; "
        "after update_module: the module it returned, "
        "or error.module after a write error), "
        "or put a deep copy of it there (copy.deepcopy)"
    )


# ---------------------------------------------------------------------------
# RefOnly fields
# ---------------------------------------------------------------------------

_REF_FILLING_HOOKS: set[Callable[..., object]] = set()
"""The hooks ``_install_ref_filling`` put on classes.

They are kept to tell them from a class's own.
"""


def ref_only_fields(model_cls: type[VcoModel]) -> Mapping[str, str | None]:
    """The ``RefOnly`` fields of *model_cls*.

    Each comes with the default ref name of its marker.

    Computed once, when the class is complete. Empty for a class with none.
    """
    return model_cls._ref_only_fields  # pyright: ignore[reportPrivateUsage]


def _fill_ref(model: VcoModel, name: str) -> None:
    """Give *model* the default ref name of its ``RefOnly`` field *name*.

    It does so if the field holds a value and *model* has no ``ref``.

    A value is anything but ``None`` and ``MISSING``. The ref name is that of
    the sibling field ``ref``; a model with none, and a marker with no default,
    have nothing to fill.
    """
    default = type(model)._ref_only_fields[name]  # pyright: ignore[reportPrivateUsage]
    if default is None or "ref" not in type(model).model_fields:
        return
    value = _field_value(model, name)
    if value is None or value is MISSING or _field_value(model, "ref") is not MISSING:
        return
    model.ref = default


def _install_ref_filling(cls: type[VcoModel]) -> None:  # noqa: C901 (existing: split it when it next changes)
    """Make the ways to set a ``RefOnly`` field fill the ``ref``.

    Assignment, a constructor keyword and a patch each set the field and fill the
    ``ref``.

    Each is a hook put on *cls* itself, and only on a class that has such a
    field: the base class pays nothing, for the instances of every other
    class. A patch sets the field through attribute assignment or builds a
    model from it, which ``model_post_init`` sees. A keyword of the
    constructor (``__init__``) sets it as an assignment does; validated input
    (``model_validate``, a parent's validation of its data) does not read it.
    """
    previous_setattr = cls.__setattr__
    if previous_setattr not in _REF_FILLING_HOOKS:

        def setattr_filling_ref(self: VcoModel, name: str, value: object) -> None:
            previous_setattr(self, name, value)
            if name in type(self)._ref_only_fields:  # pyright: ignore[reportPrivateUsage]
                _fill_ref(self, name)

        _REF_FILLING_HOOKS.add(setattr_filling_ref)
        cls.__setattr__ = setattr_filling_ref

    previous_post_init = cls.model_post_init
    if previous_post_init not in _REF_FILLING_HOOKS:

        def post_init_filling_ref(
            self: VcoModel, validation_context: object, /
        ) -> None:
            previous_post_init(self, validation_context)
            in_patch = _is_mapping(validation_context) and bool(
                validation_context.get("patch")
            )
            for name in type(self)._ref_only_fields:  # pyright: ignore[reportPrivateUsage]
                if in_patch:
                    _fill_ref(self, name)
                else:
                    # The key was not read, so it did not set the field.
                    self.__pydantic_fields_set__.discard(name)

        _REF_FILLING_HOOKS.add(post_init_filling_ref)
        cls.model_post_init = post_init_filling_ref

    previous_init = cls.__init__
    if previous_init not in _REF_FILLING_HOOKS:

        def init_taking_ref_only(self: VcoModel, /, **data: object) -> None:
            ref_only = type(self)._ref_only_fields  # pyright: ignore[reportPrivateUsage]
            keys = type(self)._ref_only_keys  # pyright: ignore[reportPrivateUsage]
            given = {name: data.pop(key) for key, name in keys.items() if key in data}
            previous_init(self, **data)
            for name in ref_only:
                if name in given:
                    setattr(self, name, given[name])

        # A subclass inherits this hook. Pydantic validates through an
        # ``__init__`` that is not its own base one (``__pydantic_custom_init__``),
        # and that call would read the keys of validated input. The hook
        # keeps the base mark of the init it wraps, as ``BaseModel.__init__`` has it.
        init_taking_ref_only.__pydantic_base_init__ = getattr(  # pyright: ignore[reportFunctionMemberAccess]
            previous_init, "__pydantic_base_init__", False
        )
        _REF_FILLING_HOOKS.add(init_taking_ref_only)
        cls.__init__ = init_taking_ref_only  # pyright: ignore[reportAttributeAccessIssue]


# ---------------------------------------------------------------------------
# VcoModel
# ---------------------------------------------------------------------------


def _register_parametrization(cls: type[BaseModel]) -> None:
    """Make a parametrized generic model findable by pickle.

    ``Data[Edge]`` is a class made when it is first written, named
    ``Data[Edge]`` and found nowhere, so an instance of it, or a plan or an
    error that holds the class, cannot be pickled. It is put in the module of
    the generic class under that name, which is where pickle looks.
    """
    metadata = cls.__pydantic_generic_metadata__
    if metadata["origin"] is None or metadata["parameters"]:
        return
    module = sys.modules.get(cls.__module__)
    if module is not None:
        _ = module.__dict__.setdefault(cls.__qualname__, cls)


class VcoModel(BaseModel):
    """The base of every VCO model: python field names, camelCase on the wire.

    A model validates by either name and dumps by alias. A dump leaves out the
    fields absent at the config level (the pydantic context's ``config_level``, or
    the model's own from ``for_context``), and a compact dump
    (``context={"compact": True}``) leaves out MISSING and default values too.
    ``merge_patch`` merges a patch into the model in place, and a model a write
    replaced (``mark_detached``) refuses edits.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        alias_generator=to_camel,
        use_enum_values=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
        hide_input_in_errors=True,
    )

    _config_context: ConfigContext | None = PrivateAttr(default=None)
    _detached: bool = PrivateAttr(default=False)
    _ref_only_fields: ClassVar[Mapping[str, str | None]] = types.MappingProxyType({})
    _ref_only_keys: ClassVar[Mapping[str, str]] = types.MappingProxyType({})
    """The keys that name a ``RefOnly`` field, each with the field.

    A field is named by its python name and by its alias.
    """

    @override
    def model_post_init(self, validation_context: object, /) -> None:
        level = _level_from_context(validation_context)
        if level is not None:
            self._config_context = level

    @override
    def __setattr__(self, name: str, value: object) -> None:
        # Every assignment to every model runs through here, also while it is
        # built: one read of the private attributes, which may not be set yet.
        if not name.startswith("_") and self.is_detached:
            _raise_detached(self, name)
        super().__setattr__(name, value)

    @override
    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        # A deep copy shares nothing with the module a write replaced, so it is
        # a new object. Each nested model is copied through here too.
        copied = super().__deepcopy__(memo)
        if copied.is_detached:
            copied._detached = False
        return copied

    @property
    def is_detached(self) -> bool:
        """Whether a write replaced this object (``mark_detached``).

        An object a write replaced takes no edit.

        A save marks the modules it replaces, and so does ``update_module``.
        """
        try:
            private = self.__pydantic_private__
        except AttributeError:  # not set yet: in __new__, or while unpickling
            return False
        return private is not None and private.get("_detached") is True

    def check_not_detached(self, name: str) -> None:
        """Raise ``DetachedObjectError`` naming *name* if a write replaced this object.

        That is what ``is_detached`` says.

        For an edit that is not an assignment to a field of this model, such
        as an ``Owned`` intent, which has to refuse as an assignment does.
        """
        if self.is_detached:
            _raise_detached(self, name)

    def check_nothing_detached(self, name: str) -> None:
        """Raise ``DetachedObjectError`` naming *name* if this holds a detached model.

        This object itself is refused as ``check_not_detached`` refuses it. A
        ``VcoModel`` inside it that a write replaced (one put back as it is,
        not as a deep copy) is refused too, at the first place it sits, in
        ``mark_detached`` order: the error's ``paths`` are that place. An
        edit that reached such an object would stop half-way, with the
        changes before it applied, so an edit checks this before it changes
        anything, also when it would not reach the object.
        """
        from pyvelo.client.exceptions import DetachedObjectError  # noqa: PLC0415 (it imports pyvelo.core)

        from .rebase import Path  # noqa: PLC0415 (rebase imports this module)

        for place, model in _models_inside(self, ()):
            if not isinstance(model, VcoModel) or not model.is_detached:
                continue
            if not place:
                _raise_detached(self, name)
            where = Path(place)
            raise DetachedObjectError(
                f"{type(self).__name__}.{name}: the {type(model).__name__} at "
                f"{where} is a detached object (it belongs to a module the state "
                "no longer holds) that was put back here as it is, so an edit "
                "that reached it would stop half-way, with the changes before "
                "it applied. Nothing was changed. Put a deep copy of it there "
                "instead (copy.deepcopy), or take the object again from the "
                "module the state holds",
                [where],
            )

    def mark_detached(self) -> list[tuple[tuple[str | int, ...], BaseModel]]:
        """Mark this model and every VcoModel inside it as replaced by a write.

        Returns each of them with where it is from here: the dump key of each
        field on the way, and the position of each list item. It also returns
        each pydantic model inside that is not a ``VcoModel`` (a segment's
        ``vrrp``), without marking it: it has no guard, so an assignment to it
        does not raise, and a change through it is found by comparing dumps.

        A marked model raises ``DetachedObjectError`` on assignment to an
        attribute whose name does not start with ``_``, and on
        ``merge_patch``; reading it is fine. A change in place of a plain
        ``list`` or ``dict`` it holds passes no attribute guard: such changes
        are found by comparing dumps, at the places returned. A deep copy of a
        marked model is not marked: it shares nothing with the module that was
        replaced. A shallow copy is, because it holds the same lists and nested
        models. A marked model put back as it is into a model that is not
        marked makes that model refuse ``merge_patch`` the same way, whole,
        before anything changes (``check_nothing_detached``).
        """
        marked = list(_models_inside(self, ()))
        for _, model in marked:
            if isinstance(model, VcoModel):
                model._detached = True
        return marked

    @override
    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        super().__pydantic_on_complete__()
        _register_parametrization(cls)
        cls._alias_map.cache_clear()
        apply_vco_markers(cls)
        cls._ref_only_fields = types.MappingProxyType(ref_only_defaults(cls))
        cls._ref_only_keys = types.MappingProxyType(
            {
                key: name
                for name in cls._ref_only_fields
                for key in {name, _field_alias(cls.model_fields[name], name)}
            }
        )
        if cls._ref_only_fields:
            _install_ref_filling(cls)

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

        dumped: JsonObject = handler(self)

        # Full dump: drop fields that are absent at the active level, but keep
        # every other value, defaults included. Unlike compact mode, nested
        # models don't inherit this model's level; pydantic serializes them
        # with the same context, and each one falls back to its own level.
        level = context_level if context_level is not None else self.config_context
        # Full dumps follow model_config (serialize_by_alias=True) unless
        # by_alias=False is passed.
        by_alias = info.by_alias is not False
        dumped = self._finish_dump(dumped, by_alias=by_alias, compact_value=None)
        if level is None:
            return dumped

        for name, field_info in type(self).model_fields.items():
            if _is_context_absent(unwrap(_field_value(self, name)), field_info, level):
                key = _field_alias(field_info, name) if by_alias else name
                _ = dumped.pop(key, None)
        return dumped

    def _finish_dump(
        self,
        dumped: JsonObject,
        *,
        by_alias: bool,
        compact_value: Callable[[object], object] | None,
    ) -> JsonObject:
        """The last step of a dump of this model, full or compact.

        A subclass changes *dumped* here.

        *dumped* has the keys of the fields as ``by_alias`` says. A full dump
        holds each field as pydantic serialized it. A compact one holds each
        field as the model has it; *compact_value* compacts a value (nested
        models, lists and dicts included) as the dump does, and is ``None``
        for a full dump.
        """
        del by_alias, compact_value
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
        """Merge *patch*, a JSON object, into this model in place; return self.

        **JSON.** *patch* is a ``dict`` (``TypeError`` otherwise) that holds what
        ``json.loads`` returns: dicts, lists, strings, finite numbers, booleans and
        ``None``, nested at most 64 levels deep (``patch.MAX_PATCH_DEPTH``). A
        selector may also be an ``int``, a ``UUID`` or the tuple of a composite
        key's parts. Anything else raises ``PatchError`` where it sits: a tuple, a
        set or a generator for a list, another mapping type for a dict, an object
        for its text, ``NaN`` or an infinity. This holds from Python too:
        ``None`` unsets a field, not ``MISSING``, and a model is given as its
        JSON-mode dump (``model_dump(mode="json")``). The patch is copied before
        it is merged (``patch.checked_patch``): the model shares no object with
        it, and a member of an ``IntEnum`` or ``StrEnum`` is copied as its plain
        ``int`` or ``str``.

        **Fields.** A key names a field by its python name, an alias or its
        camelCase form. Absent keys leave their fields as they are. For a key
        that names a field:

        - an object, on a field that holds a sub-model: merged into it, or
          built from the patch when the field is unset;
        - an object or a list, on a list field: see *Lists*;
        - an object, on a field that takes one sub-model or a list of them
          (``Model | list[Model]``): the sub-model, merged or built as
          above, unless the field holds a list, when it is item operations.
          A list value makes the field a list;
        - an object, on a map of models (``dict[K, Model]``): item
          operations by map key;
        - ``null``: unsets the field if its type includes ``MISSING``, sets
          ``None`` if it includes ``None`` and not ``MISSING``, and raises
          otherwise. The same holds in an item or sub-model the patch builds,
          except that a field whose type includes both is ``None`` there:
          leaving the key out leaves it unset, and the item's own JSON dump
          writes ``null`` for ``None``;
        - anything else: replaces the value.

        **Inheritable fields.** On an edge layer (``Data[Edge]``) an
        ``Inheritable`` field holds an ``Owned``, and a patch changes who owns
        the value:

        - ``null``: inherits (``Owned.inherit``), whatever the inner type
          takes; the edge stores nothing, or the reset object of a ``ByFlag``
          field that declares one;
        - a value that is not an object: sets the value local
          (``Owned.set_local``), validated as above;
        - an object, on a field that is local: merged into the local value;
        - an object, on a field that is not local: ``Owned.localize`` makes
          the value the edge's own first, and the object is merged into it.
          That is a copy of the profile's value, or of the field's display
          default where the profile stores none; or, on a ``presence_wins``
          field where the edge stores an object with its flag off, that
          object, kept (nothing is copied). ``UnboundError`` on a model
          that was not read from a loaded state, which does not know its
          profile, when a copy is needed;
        - a member of a coupled group: ``null`` inherits the whole group,
          and any other value first makes the group local (``localize``:
          each member that is not local, copied from the profile), then
          applies as above to that member;
        - the flag of a ``ByFlag`` field (``override``) is not patchable: it
          follows the value, set when an object is given, and ``null``
          inherits.

        The profile layer (``Data[Profile]``) holds a plain value: the rows
        above this one apply, with ``null`` as its inner type takes it. A
        patch that changes it, in place or not, marks it stored, so a value
        equal to the display default is written all the same.

        Every value assigned is first validated as pydantic validates the
        field when it parses a response: the same coercions and constraints,
        the field's validators and the model's config. The one difference: a
        field that tolerates a bad value from the VCO (``DefaultingScalar``,
        ``DefaultingModel``, ``Lenient``, ``NoneIsFalse`` and its siblings)
        does not in a patch, so the bad value raises. A boolean is taken only
        where the field holds one: ``true`` for a number raises. After the
        merge, every model the patch changed is checked as the parser would
        check it: its ``after`` and ``wrap`` model validators
        (``@model_validator``; a ``before`` one takes raw input, not a model)
        run, and where it sits in a union of several models (a field, or the
        items of a list) it must still be the member its fields select. A
        change that fails raises ``PatchError`` at the model's path: a patch
        that deletes every netflow collector while ``enabled`` stays true
        raises at the netflow block, so set ``enabled`` in the same patch. The
        check runs on a copy: a validator that rewrites values does not change
        what the patch wrote. An item or sub-model built whole from the patch is
        validated whole, its model validators included. A key that names no
        field raises, at every depth, items and sub-models built from the
        patch included, and so do two keys that name one field (``rule_id``
        and ``ruleId``). Keys that start with ``_`` are directives: only
        ``_op`` and ``_position``, and only on a list item's value.
        ``OverrideFlag`` fields are set to True when a sibling that is not
        ``EdgeLocal`` changes. On an edge layer the layer's own section then
        applies in place of the profile's, with nothing copied from the
        profile. A ``Resolvable`` field given a name resolves it to an ID with
        *resolvers*.

        **Lists.** Each list field has a kind (``list_kind``):

        - keyed (``KeyField``): items are named by key, alt key, label or
          position;
        - value (``list[str]`` or ``list[int]`` without ``Positional()``): by
          the value or a position;
        - positional (``Positional()``, or a list of models that declares
          nothing, which ``list_kind`` reports as ``"undeclared"``): by
          position;
        - untyped (``list[Any]``, ``list[dict]``): not at all; it takes a
          list value only;
        - map (``dict[K, Model]``): by map key. It takes an object only and
          has no order: no positions, no ``move`` and no ``_position``. An
          ``add`` validates its key as the map's key type and keeps that type
          (``"2"`` adds the key ``2`` of a ``dict[int, ...]``). A ``dict`` of
          anything else is a plain value.

        A list value replaces the list, its items validated; a keyed list's
        items must have distinct keys, and distinct alt keys where the list
        has one. In a list keyed by a ``NameOf`` field,
        an item given as an object is treated as an ``add`` under its name:
        it takes the resolver's name and IDs, and one that gets its IDs from
        neither a resolver nor its own fields raises ``LookupError``.
        An object is item operations, keyed by selector.

        **Selectors.** In a keyed list, tried in this order:

        1. the key, compared in string form (``"10"`` names VLAN 10). For a
           composite key (``KeyField`` with several fields): its parts joined by
           commas (``10.0.0.0,24,192.168.1.1,GE3``), the parts compared in
           string form; a part that holds a comma or a quote needs the form
           ``rebase.key_text`` writes (``"a,b",24,...``), which is tried first;
           in Python also a tuple of the parts;
        2. ``at(n)``, which is ``"#n"``: the item at position ``n``, counted
           from 0 in the list as it was before the patch (any kind of list);
        3. ``"+"`` or ``"+tag"``: a new item, for ``add`` only; the tag only
           keeps two such selectors apart;
        4. the alt key (``KeyField(alt=...)``), in string form;
        5. the label (``KeyField(label=...)``);
        6. where the key is a ``NameOf`` field and *resolvers* are given, the
           name the resolver has for the selector (matched ignoring case),
           as the key again. A name it does not know raises
           ``ResolutionError``.

        In a value list: the value, then ``"#n"``. In a positional list only
        ``"#n"`` names an item, and ``"+"`` adds one. A selector that names no
        item, or several, raises; for several, the message lists each match
        with its position and key. Two selectors that name one item raise. An
        item whose key, or a part of it, is null or absent has no key.

        **Item operations.** The value under a selector:

        - a plain object: a change, merged into the item. It may change the
          key (a rename) to one no other item has. It may change the alt key
          to one no other item has. A list of values takes none. An empty
          object changes nothing; it must still name one item.
        - ``add(body, **place)``, which is ``{"_op": "add", ...}``: a new
          item built from *body*, validated whole. In a keyed list the
          selector is the key and is written into the item; a body that gives
          another key raises, and so does a key that is already in the list,
          as a key or as another item's alt key. A body that gives an alt
          key another item has raises too. Under ``"+"`` the key is whatever
          the body gives; in a list keyed by a ``NameOf`` field a name in the
          body is an ``add`` under that name. In a value list the selector
          is the value and ``add`` takes no body; in a map it is the new key.
          Neither takes ``"+"``, and a map takes no ``"#n"``. A positional
          list of models takes ``add`` under ``"+"`` only, one of values none
          (give a list value), and ``at(n)`` never takes one.
        - ``delete()``, which is ``{"_op": "delete"}``: the item is removed.
          It carries nothing else.
        - ``move(**place)``, which is ``{"_position": P}``: the item moves.

        ``null`` (write ``delete()``), a scalar or a list raises. ``P`` is
        ``"first"``, ``"last"``, ``{"before": S}`` or ``{"after": S}``, ``S`` a
        selector of another item of the list (a composite key also as a JSON
        array of its parts); *place* is one of ``first=True``, ``last=True``,
        ``before=S``, ``after=S``. A ``_position`` may go with a change or an
        ``add``; an ``add`` without one goes last. An anchor that names no
        item, several, the item itself or an item the patch deletes raises.
        Every selector and anchor is resolved against the list as it was;
        then come changes, then deletes, then adds and moves in the order
        written (in a map: changes, deletes, adds). In an item or sub-model
        the patch builds, a field that takes only a list takes a list value
        or an object of ``add`` entries only.

        **Errors.** A patch that cannot be applied raises ``PatchError`` (a
        ``ValueError``) whose ``path`` is where in the patch it failed and
        whose ``kind`` says which sort of refusal it is
        (``patch.PatchErrorKind``); a pydantic ``ValidationError`` behind it
        is its ``__cause__``. Resolver errors keep their types. A name on a
        field whose resolver is not registered raises ``PatchError`` at the
        field, unless the text can be the ID. All or nothing: a patch that
        raises, ``PatchError``, ``ResolutionError`` or ``LookupError``, leaves
        self as it was, the same dump and the same ``model_fields_set``. On an
        object a write replaced (``is_detached``: a save or ``update_module``
        did it) it raises ``DetachedObjectError`` before anything else, and so
        it does on a model that holds such an object anywhere inside (one put
        back as it is, not as a deep copy), naming where it sits, also for a
        patch that would not reach it (``check_nothing_detached``).
        """
        self.check_nothing_detached("merge_patch")
        from .merge import apply_patch  # noqa: PLC0415 (merge imports this module)

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


def _held_type(fi: FieldInfo) -> object:
    """The type a field holds: its annotation, or the type inside its kind.

    The kind is the one ``field_kind`` finds.

    ``FieldInfo.annotation`` of a converted field is the carrier, so every
    reader of a field's type reads it here.
    """
    from .layers import field_kind  # noqa: PLC0415 (layers imports this module)

    return field_kind(fi).inner_type


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


def _field_accepts_missing(fi: FieldInfo) -> bool:  # pyright: ignore[reportUnusedFunction]  # imported by core.changes and core.merge
    """Check if a field's type annotation includes MISSING."""
    return MISSING in _union_members(_held_type(fi))


def _field_accepts_none(fi: FieldInfo) -> bool:  # pyright: ignore[reportUnusedFunction]  # imported by core.changes and core.merge
    """Check if a field's type annotation includes None."""
    return type(None) in _union_members(_held_type(fi))


def _id_type(fi: FieldInfo) -> object | None:  # pyright: ignore[reportUnusedFunction]  # imported by core.merge
    """The ID type of a Resolvable field.

    It is the field's annotation without ``MISSING``, ``None`` and ``Annotated``.

    None unless exactly one type is left.
    """
    members = (
        _unwrap_annotated(m) for m in _union_members(_unwrap_annotated(_held_type(fi)))
    )
    kept = [m for m in members if m is not MISSING and m is not type(None)]
    return kept[0] if len(kept) == 1 else None


@functools.cache
def _id_adapter(id_type: object) -> TypeAdapter[object]:
    return TypeAdapter[object](id_type)  # pyright: ignore[reportArgumentType]


def _field_model_type(fi: FieldInfo) -> type[VcoModel] | None:  # pyright: ignore[reportUnusedFunction]  # imported by core.merge and core.rebase
    """Get the VcoModel class from an annotation.

    The annotation is like ``T`` or ``T | MISSING | None``.
    """
    return next(
        (m for m in _union_members(_held_type(fi)) if _is_vco_model_type(m)), None
    )


def _field_model_types(fi: FieldInfo) -> tuple[type[VcoModel], ...]:  # pyright: ignore[reportUnusedFunction]  # imported by core.changes and core.merge
    """Every VcoModel class a field's value can be, in the order written.

    Handles ``T | MISSING | None`` and (discriminated) unions of models, also
    inside ``Annotated``: ``Annotated[A | B, Discriminator(...)] | None``.
    """
    found: list[type[VcoModel]] = []
    for member in _union_members(_held_type(fi)):
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
class ListItemType:
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
        return TypeAdapter[VcoModel](
            self.annotation,  # pyright: ignore[reportArgumentType]
            config=ConfigDict(hide_input_in_errors=True),
        )

    def validate(self, value: object, *, context: JsonObject | None = None) -> VcoModel:
        """*value* as the element type; for a union, the discriminator picks one."""
        if len(self.variants) == 1:
            return self.variants[0].model_validate(value, context=context)
        return self._union_adapter.validate_python(value, context=context)

    @property
    def name(self) -> str:
        """The class name, or for a union the variants' names joined with `` | ``."""
        return _variant_names(self.variants)

    @functools.cached_property
    def fields(self) -> dict[str, FieldInfo]:
        """The fields of every variant; for a name two variants have, the first's."""
        return _merged_fields(self.variants)


def list_item_type(fi: FieldInfo) -> ListItemType | None:
    """Get the VcoModel element type of a ``list[T]`` field, or None.

    Handles plain ``list[Model]`` and
    ``list[Annotated[A | B, Discriminator(...)]]``.
    """
    for member in _union_members(_held_type(fi)):
        if get_origin(member) is not list or not (args := get_args(member)):
            continue
        element: object = args[0]
        inner = _unwrap_annotated(element)
        if _is_vco_model_type(inner):
            return ListItemType(inner, (inner,))

        variants = tuple(
            v
            for v in map(_unwrap_annotated, _union_members(inner))
            if _is_vco_model_type(v)
        )
        if len(variants) > 1:
            return ListItemType(element, variants)
        if len(variants) == 1:
            return ListItemType(variants[0], variants)
    return None


type ListKind = Literal["keyed", "value", "positional", "undeclared", "untyped", "map"]
"""How the items of a list field are told apart (``list_kind``).

``"undeclared"`` is a list of models with neither ``KeyField`` nor
``Positional()``. A patch names its items as it names those of a
``"positional"`` list, by position; the value is kept apart so that such
lists can be counted and declared.
"""


def _is_model_members(members: Iterable[object]) -> bool:
    """True if *members* is non-empty and every one is a VcoModel class."""
    kept = list(members)
    return bool(kept) and all(_is_vco_model_type(m) for m in kept)


def _members_without_missing_and_none(annotation: object) -> list[object]:
    """The union members of *annotation* as written.

    ``MISSING`` and ``None`` are left out.
    """
    return [
        inner
        for member in _union_members(_unwrap_annotated(annotation))
        for inner in [_unwrap_annotated(member)]
        if inner is not MISSING and inner is not type(None)
    ]


def wire_name(model: type[VcoModel], name: str) -> str:
    """The JSON key field *name* of *model* is written under.

    Raises ``ValueError`` if *model* has no field of that name.
    """
    field_info = model.model_fields.get(name)
    if field_info is None:
        raise ValueError(f"{model.__name__} has no field '{name}'")
    return _field_alias(field_info, name)


def python_name(model: type[VcoModel], key: str) -> str | None:
    """The python name of the field of *model* that *key* names, or None.

    *key* is any key a patch accepts for a field: its python name, an alias,
    or its camelCase form. This is the mapping *model* has (``_alias_map``).
    """
    return model._alias_map().get(key)  # pyright: ignore[reportPrivateUsage]


def field_models(field_info: FieldInfo) -> tuple[type[VcoModel], ...]:
    """Every ``VcoModel`` class the value of a field can hold, in the order written.

    The field's own class, the classes of the items of a list, the classes of
    the values of a map and the variants of a union, each once. ``MISSING``,
    ``None`` and plain types add nothing, so a field of plain values gives an
    empty tuple.
    """
    found: dict[type[VcoModel], None] = {}
    _collect_models(_held_type(field_info), found)
    return tuple(found)


def _collect_models(annotation: object, found: dict[type[VcoModel], None]) -> None:
    """Add to *found* the ``VcoModel`` classes *annotation* is made of, in order."""
    annotation = _unwrap_annotated(annotation)
    if _is_vco_model_type(annotation):
        _ = found.setdefault(annotation)
    elif get_origin(annotation) in (*_UNION_ORIGINS, list, dict, tuple):
        for member in get_args(annotation):
            _collect_models(member, found)


def list_kind(field_info: FieldInfo) -> ListKind | None:  # noqa: C901 (existing: split it when it next changes)
    """How a list field's items are told apart, or None if it is not a list.

    A ``dict`` of models is ``"map"``. Of lists: ``"keyed"`` has a
    ``KeyField``, ``"positional"`` has ``Positional()``, ``"undeclared"`` is a
    list of models with neither, ``"value"`` is a list of ``str`` or ``int``,
    ``"untyped"`` is any other list. ``MISSING`` and ``None`` in a union are
    set aside.
    """
    is_dict_of_models = False
    has_list = False
    element_kind: Literal["model", "value", "other"] = "other"
    for member in _members_without_missing_and_none(_held_type(field_info)):
        origin = get_origin(member)
        args = get_args(member)
        if origin is dict and len(args) == 2:  # noqa: PLR2004  # dict[K, V] has two type arguments
            value_members = _members_without_missing_and_none(args[1])
            is_dict_of_models = is_dict_of_models or _is_model_members(value_members)
        elif member is list or origin is list:
            has_list = True
            element_members = _members_without_missing_and_none(args[0]) if args else []
            if _is_model_members(element_members):
                element_kind = "model"
            elif element_members and all(m is str or m is int for m in element_members):
                element_kind = "value"
    if is_dict_of_models and not has_list:
        return "map"
    if not has_list:
        return None
    if get_key_spec(field_info) is not None:
        return "keyed"
    if is_positional(field_info):
        return "positional"
    if element_kind == "model":
        return "undeclared"
    return "value" if element_kind == "value" else "untyped"


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

    def __init__(
        self, model_cls: type[VcoModel], config_context: ConfigContext
    ) -> None:
        """A view of the fields of *model_cls* at *config_context*, read as asked."""
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
        """The field *name* at this view's level; a name starting ``_`` raises."""
        if name.startswith("_"):
            raise AttributeError(name)
        return self._resolve(name)

    def __iter__(self) -> Iterator[tuple[str, ContextFieldInfo]]:
        """Each field's name and its metadata at this level, in field order."""
        for name in self._model_cls.model_fields:
            yield name, self._resolve(name)


# ---------------------------------------------------------------------------
# Paginated model
# ---------------------------------------------------------------------------


class PaginatedVcoModel(VcoModel):
    """A request of a paginated VCO method.

    ``next_page_link`` is the cursor of the page to ask for. The client records
    where paging over the request stopped (``record_page_cursor``).
    """

    next_page_link: str | MISSING = Field(default=MISSING)  # ty: ignore[invalid-type-form]

    _max_pages: int = PrivateAttr(default=0)
    _page_has_more: bool = PrivateAttr(default=False)
    _page_next_link: str | None = PrivateAttr(default=None)

    @property
    def max_pages(self) -> int:
        """The most pages ``portal_paginated_list`` collects for this request.

        0 means no cap.
        """
        return self._max_pages

    @max_pages.setter
    def max_pages(self, value: int) -> None:
        self._max_pages = value

    @property
    def page_has_more(self) -> bool:
        """Whether VCO had more pages when paging over this request last stopped."""
        return self._page_has_more

    @property
    def page_next_link(self) -> str | None:
        """The cursor of the next page when paging last stopped.

        None when there is none.
        """
        return self._page_next_link

    def record_page_cursor(self, next_page_link: str | None) -> None:
        """Record where paging over this request stopped.

        That is the next page's cursor, or None when VCO has no more.
        """
        self._page_has_more = next_page_link is not None
        self._page_next_link = next_page_link


# ---------------------------------------------------------------------------
# JSON-RPC types
# ---------------------------------------------------------------------------


class JsonRpcRequest[ParamsT](VcoModel):
    """A JSON-RPC 2.0 request of *method* with *params*; its ``id`` is a time in ms."""

    jsonrpc: Literal["2.0"] = Field(default="2.0")
    id_: Annotated[int | str | None, Alias("id")] = Field(
        default_factory=lambda: int(1000 * dt.datetime.now(dt.UTC).timestamp())
    )
    method: str
    params: ParamsT | None = Field(default=None)


class JsonRpcError(VcoModel):
    """The error of a JSON-RPC response: its code, its message and the VCO's data."""

    code: int
    message: str
    data: dict[str, object] | str | list[object] | None = Field(default=None)


class JsonRpcResponse[ResultT](VcoModel):
    """A JSON-RPC 2.0 response, with its ``result`` or its ``error``."""

    jsonrpc: Literal["2.0"] = Field(default="2.0")
    id_: Annotated[int | str | None, Alias("id")] = None
    result: ResultT | None = Field(default=None)
    error: JsonRpcError | None = Field(default=None)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginationMetadata(VcoModel):
    """The ``metaData`` of a page: whether ``more`` pages follow, and their cursors."""

    limit: int | None = None
    more: bool
    next_page_link: str | None = Field(default=None)
    prev_page_link: str | None = Field(default=None)


class PaginatedResult[ItemT: BaseModel](VcoModel):
    """One page of a paginated VCO method: its ``data`` and its ``metaData``."""

    metadata: Annotated[PaginationMetadata, Alias("metaData")]
    data: list[ItemT]


class RowsModifiedConfirmation(VcoModel):
    """The answer of a VCO write: the count of rows it changed, or an error."""

    error: str | None = None
    rows: int | None = None


class InsertionConfirmation(VcoModel):
    """The answer of a VCO insert: the new row's ``id``."""

    id_: Annotated[int, Alias("id")]


# ---------------------------------------------------------------------------
# API filters
# ---------------------------------------------------------------------------


class ApiFilter(VcoModel):
    """One condition of a VCO filter: a ``field``, an ``operator`` and a ``value``.

    ``fieldName`` and ``op`` are read as ``field`` and ``operator``.
    """

    field: str = Field(validation_alias=AliasChoices("field", "fieldName"))
    operator: str = Field(validation_alias=AliasChoices("operator", "op"))
    value: str | float | list[str | float]


type ApiFiltersList = Annotated[list[ApiFilter], annotated_types.Len(min_length=1)]


class ApiFiltersOr(VcoModel):
    """A filter group that holds when any one of its conditions does."""

    or_: Annotated[ApiFiltersList, Alias("or")]


class ApiFiltersAnd(VcoModel):
    """A filter group that holds when all of its conditions do."""

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


def _api_filters_discriminator(v: object) -> str | None:  # noqa: PLR0911 (existing: split it when it next changes)
    if is_dict(v):
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
    if is_dict(v):
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
    """A VCO flag sent as 0 or 1."""

    ZERO = 0
    ONE = 1


_EPOCH = dt.datetime.fromtimestamp(0, dt.UTC)


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
                return dt.datetime.strptime(date_part, "%a %b %d %Y %H:%M:%S GMT%z")
            except ValueError:
                pass

    return timestamp


type VcoTimestamp = Annotated[AwareDatetime, BeforeValidator(check_time_is_never)]


class ApiInterval(VcoModel):
    """The time interval of a VCO request: its ``start``, and its ``end`` when given."""

    start: VcoTimestamp
    end: VcoTimestamp | MISSING = MISSING  # ty: ignore[invalid-type-form]


def missing_or[T, D](value: T | MISSING, default: D) -> T | D:  # ty: ignore[invalid-type-form]
    """Return *value* unless it is MISSING, in which case return *default*."""
    return default if value is MISSING else value


value_or = missing_or
"""Alias of :func:`missing_or`."""
