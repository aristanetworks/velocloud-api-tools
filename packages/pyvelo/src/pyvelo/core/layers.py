"""Layers, field kinds and their carriers, display defaults, and ``Layered`` models.

A configuration model has four faces, one per layer: ``Edge`` and ``Profile``
(what each stores, and the only ones a caller writes), ``Effective`` (what an
edge shows once its profile is applied; computed) and ``Composed`` (what the
server returns for an edge; read only). One declaration gives all four::

    class Data(Layered[L]):
        segments: Annotated[list[Seg[L]], KeyField("segment.name")]
        enabled: Annotated[Inheritable[bool], ByPresence(), Default(True)] = Field(
            default=...
        )

``Data[Edge]`` holds ``Owned[bool]`` in ``enabled``, ``Data[Profile]`` a
``bool``. The field *kind* (``Inheritable``, ``EdgeOnly``, ``ProfileOnly``,
``Additive``, ``Unconverted``, or none: structural) decides the type each layer
gives. At runtime ``Kind[T]`` is a *carrier*, generic over the layer and ``T``;
pydantic substitutes the layer when the model is parametrized and the
carrier's schema is the one for that layer. For the type checker ``Kind[T]``
is a descriptor whose ``__get__`` is overloaded on the layer, so an access a
layer does not allow is an error. ``field_kind`` is the one place that looks
inside a carrier.

Where a marker goes: those that describe the field as a whole (``Alias``,
``KeyField``, the encoding, ``Default``) are written in the field's own
``Annotated``, outside the kind. A validator that must see the raw value
before it becomes an ``Owned`` (``DefaultingScalar``, ``Lenient``) goes inside
the kind's type argument: ``Inheritable[Annotated[bool, DefaultingScalar(...)]]``.
"""

import copy
import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Generic,
    NamedTuple,
    Self,
    TypeVar,
    final,
    get_args,
    get_origin,
    overload,
    override,
)

from pydantic import (
    MISSING,
    AfterValidator,
    BeforeValidator,
    Discriminator,
    Field,
    GetCoreSchemaHandler,
    ModelWrapValidatorHandler,
    PlainValidator,
    PrivateAttr,
    PydanticUserError,
    TypeAdapter,
    WrapValidator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_core import CoreSchema, core_schema

from .base import JsonObject, VcoModel, same_state, wire_name
from .fields import ConfigContext
from .owned import (
    Binding,
    ByFlag,
    ByPresence,
    Encoding,
    Owned,
    check_groups,
)
from .owned import unwrap as unwrap  # noqa: PLC0414 (re-exported: it is defined beside Owned, which base imports)

if TYPE_CHECKING:
    from .effective import Resolved


class Edge:
    """Layer marker: the stored edge-specific layer. Never instantiated."""


class Profile:
    """Layer marker: the stored profile layer. Never instantiated."""


class Effective:
    """Layer marker: computed from an edge and a profile, each value with its origin. Never instantiated."""


class Composed:
    """Layer marker: what the server returns for an edge, plain values. Never instantiated."""


L = TypeVar("L", Edge, Profile, Effective, Composed)
"""The layer a ``Layered`` model is generic over: one object, shared by every model."""

type Layer = type[Edge | Profile | Effective | Composed]

_LAYERS: tuple[type, ...] = (Edge, Profile, Effective, Composed)


def layer_for(level: ConfigContext) -> type[Edge] | type[Profile]:
    """The stored layer a config level reads and writes."""
    return Edge if level is ConfigContext.EDGE else Profile


def _is_layer(value: object) -> bool:
    return any(value is layer for layer in _LAYERS)


# ---------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------


class Kind(StrEnum):
    """How a field of a ``Layered`` model is inherited (``field_kind``)."""

    STRUCTURAL = "structural"
    INHERITABLE = "inheritable"
    EDGE_ONLY = "edge_only"
    PROFILE_ONLY = "profile_only"
    ADDITIVE = "additive"
    UNCONVERTED = "unconverted"


class FieldKind(NamedTuple):
    """What ``field_kind`` finds in a field: its kind, the type it holds, its encoding and its markers."""

    kind: Kind
    inner_type: object
    """The kind's type argument without its ``Annotated``; for a structural field, the annotation."""
    encoding: Encoding | None
    metadata: tuple[object, ...]
    """The field's own metadata, then the metadata inside the kind's type argument."""


_UNSET: object = object()


@final
class Default:
    """Annotated marker: the display default of an ``Inheritable`` field.

    ``Default(value)`` or ``Default(factory=callable)``. It is not the
    pydantic default of the field, which would apply on the edge layer too:
    on the profile layer (and the composed one) an absent key reads as the
    display default, and the field is then not in ``model_fields_set``; on the
    edge layer an absent key is an ``Owned`` with nothing stored.

    Usage: ``Annotated[Inheritable[bool], ByPresence(), Default(True)]``
    """

    __slots__ = ("_factory", "_value")
    _value: object
    _factory: Callable[[], object] | None

    def __init__(
        self, value: object = _UNSET, *, factory: Callable[[], object] | None = None
    ) -> None:
        if (value is _UNSET) == (factory is None):
            raise TypeError(
                "Default takes a value or a factory, not both and not neither: "
                + "Default(True) or Default(factory=list)"
            )
        self._value = value
        self._factory = factory

    def make(self) -> object:
        """A fresh display default: a deep copy of the value, or what the factory returns."""
        if self._factory is not None:
            return self._factory()
        return copy.deepcopy(self._value)

    def __get_pydantic_core_schema__(
        self, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        schema = handler(source)
        layer = _carrier_layer(source)
        if layer is Profile or layer is Composed:
            return core_schema.with_default_schema(schema, default_factory=self.make)
        return schema

    @override
    def __repr__(self) -> str:
        if self._factory is not None:
            return f"Default(factory={self._factory!r})"
        return f"Default({self._value!r})"


@final
@dataclass(frozen=True)
class Authority:
    """Annotated marker: the profile's value replaces the effective value of an ``Inheritable`` field when *rule* holds.

    ``rule`` is called with the profile layer's value of the field (what it
    stores, or the display default), and when it returns true the effective
    value is that value with the origin ``PROFILE``, whatever the edge holds.
    It is the VCO's own rule for a few fields: the effective ``firewall_enabled``
    is ``false`` whenever the profile's is. Only the effective view reads it.

    Usage: ``Annotated[Inheritable[bool], ByPresence(), Default(True),
    Authority(lambda profile_value: profile_value is False)]``
    """

    rule: Callable[[object], bool]


def kind_default(
    value: object = MISSING, *, factory: Callable[[], object] | None = None
) -> Any:
    """The default of a field of the kind ``EdgeOnly``, ``ProfileOnly``, ``Additive`` or ``Unconverted``.

    ``kind_default("")`` or ``kind_default(factory=list)``: exactly one of the
    two. It is ``Field(default=value)`` or ``Field(default_factory=factory)``
    typed ``Any``, because the checker takes a plain default for a value that
    is not the field's declared kind (``EdgeOnly[str] = ""`` is an error) and
    still treats the field as optional. An ``Inheritable`` field has no use
    for it: it is ``= Field(default=...)`` and its default is the ``Default``.
    """
    if (value is MISSING) == (factory is None):
        raise TypeError(
            "kind_default takes a value or a factory, not both and not neither: "
            + 'kind_default("") or kind_default(factory=list)'
        )
    if factory is not None:
        return Field(default_factory=factory)
    return Field(default=value)


# ---------------------------------------------------------------------------
# Carriers: what Kind[T] is at runtime
# ---------------------------------------------------------------------------

T = TypeVar("T")


def _refuse_effective(_value: object) -> object:
    raise TypeError(
        "the effective view is computed from an edge layer and a profile layer, "
        + "not validated from data: validate the model for the Edge or Profile "
        + "layer, and read state.effective for the view"
    )


def _ignore_input(_value: object) -> object:
    return MISSING


def _missing_for_json(_value: object) -> None:
    return None


_ANY_SCHEMA = core_schema.any_schema()


class _Carrier(Generic[L, T]):  # noqa: UP046 (one shared constrained L, as a TypeVar)
    """What ``Kind[T]`` is at runtime: ``_Carrier[L, T]``, never instantiated.

    Pydantic replaces ``L`` when a ``Layered`` model is parametrized; this
    class's schema hook then builds the schema for that layer.
    """

    kind: ClassVar[Kind]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        layer, inner = get_args(source)
        if layer is Effective:
            return core_schema.no_info_plain_validator_function(
                _refuse_effective, json_schema_input_schema=_ANY_SCHEMA
            )
        if layer is Edge:
            return cls._edge_schema(inner, handler)
        if layer is Profile:
            return cls._profile_schema(inner, handler)
        return handler.generate_schema(
            inner
        )  # Composed, or a class not yet parametrized

    @classmethod
    def _edge_schema(cls, inner: object, handler: GetCoreSchemaHandler) -> CoreSchema:
        return handler.generate_schema(inner)

    @classmethod
    def _profile_schema(
        cls, inner: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return handler.generate_schema(inner)


def _holds_missing() -> CoreSchema:
    """A schema that reads no input and holds ``MISSING``: the field is of the other layer."""
    return core_schema.no_info_plain_validator_function(
        _ignore_input,
        json_schema_input_schema=_ANY_SCHEMA,
        serialization=core_schema.plain_serializer_function_ser_schema(
            _missing_for_json
        ),
    )


def _empty_owned() -> Owned[Any]:
    return Owned(MISSING)


class _InheritableCarrier(_Carrier[L, T]):
    kind: ClassVar[Kind] = Kind.INHERITABLE

    @classmethod
    @override
    def _edge_schema(cls, inner: object, handler: GetCoreSchemaHandler) -> CoreSchema:
        value_schema = core_schema.nullable_schema(handler.generate_schema(inner))

        def to_owned(value: object, validate: Callable[[object], object]) -> Owned[Any]:
            if isinstance(value, Owned):
                # A copy of what the caller's Owned stores: a model is
                # validated as the same object, and the two must not share it.
                stored: object = value.stored  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                if stored is MISSING:
                    return Owned(MISSING)
                return Owned(validate(copy.deepcopy(stored)))
            return Owned(validate(value))

        def stored_of(value: Owned[Any], dump: Callable[[object], object]) -> object:
            return None if value.stored is MISSING else dump(value.stored)

        owned_schema = core_schema.no_info_wrap_validator_function(
            to_owned,
            value_schema,
            serialization=core_schema.wrap_serializer_function_ser_schema(
                stored_of, schema=value_schema
            ),
        )
        return core_schema.with_default_schema(
            owned_schema, default_factory=_empty_owned
        )


class _EdgeOnlyCarrier(_Carrier[L, T]):
    kind: ClassVar[Kind] = Kind.EDGE_ONLY

    @classmethod
    @override
    def _profile_schema(
        cls, inner: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return _holds_missing()


class _ProfileOnlyCarrier(_Carrier[L, T]):
    kind: ClassVar[Kind] = Kind.PROFILE_ONLY

    @classmethod
    @override
    def _edge_schema(cls, inner: object, handler: GetCoreSchemaHandler) -> CoreSchema:
        return _holds_missing()


class _AdditiveCarrier(_Carrier[L, T]):
    kind: ClassVar[Kind] = Kind.ADDITIVE


class _UnconvertedCarrier(_Carrier[L, T]):
    kind: ClassVar[Kind] = Kind.UNCONVERTED


_CARRIERS: Mapping[Kind, type[_Carrier[Any, Any]]] = {
    Kind.INHERITABLE: _InheritableCarrier,
    Kind.EDGE_ONLY: _EdgeOnlyCarrier,
    Kind.PROFILE_ONLY: _ProfileOnlyCarrier,
    Kind.ADDITIVE: _AdditiveCarrier,
    Kind.UNCONVERTED: _UnconvertedCarrier,
}


def carrier(kind: Kind, inner: object) -> object:
    """``Kind[inner]`` at runtime: the carrier of *kind* over the shared layer variable and *inner*."""
    generic: Any = _CARRIERS[kind]
    return generic[L, inner]


def _carrier_layer(annotation: object) -> object | None:
    """The layer a carrier annotation names (a layer class, or ``L`` before parametrizing); ``None`` for other types."""
    origin = get_origin(annotation)
    if isinstance(origin, type) and issubclass(origin, _Carrier):
        return get_args(annotation)[0]
    return None


if TYPE_CHECKING:

    class Inheritable[T]:
        """Field kind: the edge may override what the profile provides.

        ``Owned[T]`` on the edge layer, ``T`` on the profile and composed ones,
        ``Resolved[T]`` on the effective view. Declared with an encoding and a
        ``Default``, and ``= Field(default=...)``.
        """

        @overload
        def __get__(self, obj: None, owner: type) -> Self: ...
        @overload
        def __get__(
            self, obj: "Layered[Edge]", owner: type | None = None
        ) -> Owned[T]: ...
        @overload
        def __get__(self, obj: "Layered[Profile]", owner: type | None = None) -> T: ...
        @overload
        def __get__(
            self, obj: "Layered[Effective]", owner: type | None = None
        ) -> "Resolved[T]": ...
        @overload
        def __get__(self, obj: "Layered[Composed]", owner: type | None = None) -> T: ...
        def __get__(self, obj: object, owner: type | None = None) -> object: ...
        def __set__(self, obj: object, value: T | Owned[T]) -> None: ...

    class Additive[T]:
        """Field kind: a list whose edge entries come ahead of the profile's; give it as ``Additive[list[X]]``.

        ``list[X]`` on the stored and composed layers, ``list[Resolved[X]]`` on
        the effective view.
        """

        @overload
        def __get__(self, obj: None, owner: type) -> Self: ...
        @overload
        def __get__(self, obj: "Layered[Edge]", owner: type | None = None) -> T: ...
        @overload
        def __get__(self, obj: "Layered[Profile]", owner: type | None = None) -> T: ...
        @overload
        def __get__[E](
            self: "Additive[list[E]]",
            obj: "Layered[Effective]",
            owner: type | None = None,
        ) -> "list[Resolved[E]]": ...
        @overload
        def __get__(self, obj: "Layered[Composed]", owner: type | None = None) -> T: ...
        def __get__(self, obj: object, owner: type | None = None) -> object: ...
        def __set__(self, obj: object, value: T) -> None: ...

    class Unconverted[T]:
        """Field kind: inheritance not declared yet.

        ``T`` on the stored and composed layers, exactly as the field is today;
        not available on the effective view.
        """

        @overload
        def __get__(self, obj: None, owner: type) -> Self: ...
        @overload
        def __get__(self, obj: "Layered[Edge]", owner: type | None = None) -> T: ...
        @overload
        def __get__(self, obj: "Layered[Profile]", owner: type | None = None) -> T: ...
        @overload
        def __get__(self, obj: "Layered[Composed]", owner: type | None = None) -> T: ...
        def __get__(self, obj: object, owner: type | None = None) -> object: ...
        def __set__(self, obj: object, value: T) -> None: ...

else:

    class Inheritable:
        """Field kind: the edge may override what the profile provides.

        ``Owned[T]`` on the edge layer, ``T`` on the profile and composed ones,
        ``Resolved[T]`` on the effective view. Declared with an encoding and a
        ``Default``, and ``= Field(default=...)``.
        """

        def __class_getitem__(cls, item: object) -> object:
            return carrier(Kind.INHERITABLE, item)

    class Additive:
        """Field kind: a list whose edge entries come ahead of the profile's; give it as ``Additive[list[X]]``.

        ``list[X]`` on the stored and composed layers, ``list[Resolved[X]]`` on
        the effective view.
        """

        def __class_getitem__(cls, item: object) -> object:
            return carrier(Kind.ADDITIVE, item)

    class Unconverted:
        """Field kind: inheritance not declared yet.

        ``T`` on the stored and composed layers, exactly as the field is today;
        not available on the effective view.
        """

        def __class_getitem__(cls, item: object) -> object:
            return carrier(Kind.UNCONVERTED, item)


# ---------------------------------------------------------------------------
# field_kind: the one place that looks inside a carrier
# ---------------------------------------------------------------------------

_FIELD_KINDS: dict[int, tuple[FieldInfo, object, int, FieldKind]] = {}
"""The ``FieldKind`` of each field seen, by the identity of its ``FieldInfo`` (which it holds on to).

An entry is stale when the annotation changed or markers were appended to the
metadata since (``apply_vco_markers`` does); it is then computed again.
"""


def _without_annotated(annotation: object) -> tuple[object, tuple[object, ...]]:
    if get_origin(annotation) is Annotated:
        inner, *metadata = get_args(annotation)
        return inner, tuple(metadata)
    return annotation, ()


def field_kind(field_info: FieldInfo) -> FieldKind:
    """What kind a field is, the type it holds, its encoding and its markers.

    Every reader of ``FieldInfo.annotation`` and ``FieldInfo.metadata`` of a
    ``Layered`` model goes through here: ``model_fields[name].annotation`` is
    the carrier, not the type. A plain annotation is a structural field.
    """
    annotation: object = field_info.annotation
    size = len(field_info.metadata)
    cached = _FIELD_KINDS.get(id(field_info))
    if (
        cached is not None
        and cached[0] is field_info
        and cached[1] is annotation
        and cached[2] == size
    ):
        return cached[3]
    origin = get_origin(annotation)
    if isinstance(origin, type) and issubclass(origin, _Carrier):
        _, argument = get_args(annotation)
        inner_type, inner_metadata = _without_annotated(argument)
        kind = origin.kind
    else:
        inner_type, inner_metadata = annotation, ()
        kind = Kind.STRUCTURAL
    metadata = (*field_info.metadata, *inner_metadata)
    encoding = next((m for m in metadata if isinstance(m, ByPresence | ByFlag)), None)
    found = FieldKind(kind, inner_type, encoding, metadata)
    _FIELD_KINDS[id(field_info)] = (field_info, annotation, size, found)
    return found


def _default_of(field_info: FieldInfo) -> Default | None:
    return next(
        (m for m in field_kind(field_info).metadata if isinstance(m, Default)), None
    )


def display_default(field_info: FieldInfo) -> object:
    """A fresh display default of an ``Inheritable`` field: what the profile layer reads at an absent key.

    ``TypeError`` for a field that has none.
    """
    default = _default_of(field_info)
    if default is None:
        raise TypeError(
            f"{field_info!r} is not an Inheritable field: only that kind has a "
            + "display default"
        )
    return default.make()


def _is_stored(model: VcoModel, name: str, default: Default) -> bool:
    return name in model.model_fields_set or not same_state(
        getattr(model, name), default.make()
    )


def profile_stores(model: "Layered[Profile]", name: str) -> bool:
    """Whether the profile layer stores the ``Inheritable`` field *name* of *model*.

    It does when the field is in ``model_fields_set`` (read from data or
    assigned) or its value differs from the display default (changed in
    place). The profile dump writes exactly the fields that are stored, and
    the effective view takes a value from the profile only when it is.
    """
    default = _default_of(type(model).model_fields[name])
    if default is None:
        raise TypeError(
            f"{type(model).__name__}.{name} is not an Inheritable field: only that "
            + "kind has a display default, which says whether a profile stores it"
        )
    return _is_stored(model, name, default)


_FIELD_VALIDATOR_MARKERS = {
    "before": BeforeValidator,
    "after": AfterValidator,
    "wrap": WrapValidator,
    "plain": PlainValidator,
}


@functools.cache
def inner_adapter(model_cls: type[VcoModel], name: str) -> TypeAdapter[object]:
    """A validator for the value of field *name* of *model_cls*, as pydantic validates it on parse.

    It validates the type the field holds (``field_kind(...).inner_type``: the
    field's annotation for a plain field, the type inside its kind for a
    carrier) with every marker of the field, so value validators
    (``DefaultingScalar``) and constraints apply to the value itself, with the
    discriminator of a union, the class's ``@field_validator``s for the field
    (``"*"`` ones too) applied after the metadata in the order defined, as
    pydantic does, and the class's config (``use_enum_values`` and the
    rest). Model validators are not field validation and do not run here. When
    a model instance is the value, as the merge gives one, the class's
    ``after`` and ``wrap`` model validators do run on it.

    It reads a value and gives a value: on the edge layer the ``Owned`` around
    it is not made. Use it with the context ``{"patch": True}``, which stops a
    defaulting validator from turning a bad value into the default.
    ``merge_patch`` and ``Owned.set_local`` validate with it, so a patch and
    ``set_local`` take the same values.
    """
    field_info = model_cls.model_fields[name]
    found = field_kind(field_info)
    metadata: list[object] = list(found.metadata)
    if isinstance(field_info.discriminator, str):
        metadata.append(Discriminator(field_info.discriminator))
    elif field_info.discriminator is not None:
        metadata.append(field_info.discriminator)
    for decorator in model_cls.__pydantic_decorators__.field_validators.values():
        info = decorator.info
        if name in info.fields or "*" in info.fields:
            metadata.append(_FIELD_VALIDATOR_MARKERS[info.mode](decorator.func))
    annotation: object = (
        Annotated[found.inner_type, *metadata] if metadata else found.inner_type
    )
    try:
        return TypeAdapter[object](annotation, config=model_cls.model_config)  # pyright: ignore[reportArgumentType]
    except PydanticUserError as exc:
        # A model type has its own config, and TypeAdapter refuses another.
        if exc.code != "type-adapter-config-unused":
            raise
        return TypeAdapter[object](annotation)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# Layered
# ---------------------------------------------------------------------------

_READ_ONLY_MESSAGE = (
    "{model}.{name}: this is the {layer} layer, which is computed or read from the "
    + "server and not edited; edit state.edge or state.profile"
)


class _DumpRule(NamedTuple):
    name: str
    alias: str
    action: str  # "drop", "owned" or "unless_stored"
    default: Default | None


_OUTSIDE_VALIDATOR_MARKERS = (AfterValidator, WrapValidator, PlainValidator)


def _refuse_validators_after_the_kind(cls: type[VcoModel], name: str) -> None:
    """``TypeError`` if the ``Inheritable`` field *name* of *cls* has a validator that would see an ``Owned``.

    Pydantic runs a class's field validators, and the validator markers in the
    field's own ``Annotated`` (``AfterValidator``, ``WrapValidator``,
    ``PlainValidator``), outside the field's type, so on an edge parse one of
    those would be given the ``Owned`` and everywhere else (profile, composed,
    ``set_local``, a patch) the value. A ``before`` one, ``BeforeValidator``
    included, runs ahead of the kind on the raw value on every path, and is
    allowed.
    """
    for function, decorator in cls.__pydantic_decorators__.field_validators.items():
        info = decorator.info
        if info.mode != "before" and (name in info.fields or "*" in info.fields):
            raise TypeError(
                f"{cls.__name__}.{name}: the {info.mode} field validator "
                + f"{function!r} would be given an Owned when the edge layer is "
                + "parsed and the value on every other path. Put the validation "
                + "inside the kind's type argument, where every path runs it on "
                + "the value: Inheritable[Annotated[T, AfterValidator(check)]]; "
                + "a before validator is allowed"
            )
    for marker in cls.model_fields[name].metadata:
        if isinstance(marker, _OUTSIDE_VALIDATOR_MARKERS):
            kind = type(marker).__name__
            raise TypeError(
                f"{cls.__name__}.{name}: the {kind} in the field's Annotated, "
                + "outside the kind, would be given an Owned when the edge layer "
                + "is parsed and the value on every other path. Put it inside "
                + "the kind's type argument, where every path runs it on the "
                + f"value: Inheritable[Annotated[T, {kind}(check)]]; a "
                + "BeforeValidator is allowed there"
            )


class Layered(VcoModel, Generic[L]):  # noqa: UP046 (one shared constrained L, as a TypeVar)
    """Base of the models whose fields depend on the layer: ``Data[Edge]``, ``Data[Profile]``, ...

    Generic over the layer ``L``. A model validated or built without a layer
    raises ``TypeError``, so does validation into an ``[Effective]`` class,
    and a ``[Effective]`` or ``[Composed]`` model refuses attribute
    assignment. On the edge layer an assignment to an ``Inheritable`` field
    raises too: it would replace who owns the value with a bare value.

    A class may not declare an ``after``, ``wrap`` or ``plain``
    ``@field_validator`` on an ``Inheritable`` field (``TypeError`` when the
    class is created): it would see an ``Owned`` on an edge parse and the value
    on every other path. Validate inside the kind's type argument instead,
    ``Inheritable[Annotated[T, AfterValidator(check)]]``, which every path runs
    on the value; a ``before`` validator is allowed.
    """

    _binding: Binding | None = PrivateAttr(default=None)
    """The profile layer this edge-layer model inherits from (``bind_layers``); ``None`` until bound."""

    @classmethod
    @functools.cache
    def layer(cls) -> Layer:
        """The layer this class is for. ``TypeError`` for a class with no layer."""
        for klass in cls.__mro__:
            metadata: Any = getattr(klass, "__pydantic_generic_metadata__", None)
            if metadata:
                for argument in metadata["args"]:
                    if _is_layer(argument):
                        return argument
        name = cls.__name__
        raise TypeError(
            f"{name} is generic over its layer and has none: parametrize it, "
            + f"{name}[Edge] or {name}[Profile], before validating or building it"
        )

    @property
    @override
    def config_context(self) -> ConfigContext | None:
        layer = type(self).layer()
        if layer is Edge:
            return ConfigContext.EDGE
        if layer is Profile:
            return ConfigContext.PROFILE
        return None

    @model_validator(mode="wrap")
    @classmethod
    def _check_layer(
        cls, data: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        # Before any field is validated: a model whose fields all have defaults
        # validates {} without running a field schema.
        layer = cls.layer()
        if layer is Effective:
            base = cls.__pydantic_generic_metadata__["origin"] or cls
            raise TypeError(
                f"{cls.__name__}: the effective view is computed from an edge layer "
                + "and a profile layer, not validated from data: validate "
                + f"{base.__name__}[Edge] or {base.__name__}[Profile], and read "
                + "state.effective for the view"
            )
        model = handler(data)
        # A field of the other layer holds MISSING, whatever its default says.
        for name in cls._other_layer_fields():
            object.__setattr__(model, name, MISSING)
            model.__pydantic_fields_set__.discard(name)
        model._attach_owned()
        return model

    def _attach_owned(self) -> None:
        """Make each ``Owned`` this edge-layer model holds know the model and the field it belongs to."""
        cls = type(self)
        if cls.layer() is not Edge:
            return
        for name, encoding in cls._owned_encodings().items():
            owned: object = self.__dict__.get(name)
            if isinstance(owned, Owned):
                owned._attach(self, name, encoding)  # pyright: ignore[reportUnknownMemberType, reportArgumentType, reportPrivateUsage]

    @classmethod
    @override
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        model = super().model_construct(_fields_set, **values)
        if cls.layer() is Edge:
            # The required Inheritable fields have no default to construct.
            for name in cls._owned_fields():
                if name not in model.__dict__:
                    object.__setattr__(model, name, Owned(MISSING))
            model._attach_owned()
        return model

    @override
    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        # The copy's Owned values are copies without an owner (the binding is
        # shared, not copied); the copy attaches them to itself.
        copied = super().__deepcopy__(memo)
        copied._attach_owned()
        return copied

    @classmethod
    @functools.cache
    def _other_layer_fields(cls) -> tuple[str, ...]:
        layer = cls.layer()
        other = (
            Kind.EDGE_ONLY
            if layer is Profile
            else Kind.PROFILE_ONLY
            if layer is Edge
            else None
        )
        return tuple(
            name
            for name, info in cls.model_fields.items()
            if other is not None and field_kind(info).kind is other
        )

    @classmethod
    @functools.cache
    def _owned_fields(cls) -> frozenset[str]:
        """The ``Inheritable`` fields, which hold an ``Owned`` on the edge layer."""
        return frozenset(cls._owned_encodings())

    @classmethod
    @functools.cache
    def _owned_encodings(cls) -> dict[str, Encoding | None]:
        """The ``Inheritable`` fields, each with its encoding."""
        return {
            name: found.encoding
            for name, info in cls.model_fields.items()
            if (found := field_kind(info)).kind is Kind.INHERITABLE
        }

    @override
    @classmethod
    def __pydantic_on_complete__(cls) -> None:
        super().__pydantic_on_complete__()
        for name, info in cls.model_fields.items():
            found = field_kind(info)
            defaults = sum(isinstance(m, Default) for m in found.metadata)
            if found.kind is Kind.INHERITABLE:
                if defaults != 1:
                    raise TypeError(
                        f"{cls.__name__}.{name}: an Inheritable field needs exactly "
                        + "one Default, its display default (Default(True), "
                        + f"Default(factory=list)); found {defaults}"
                    )
                if sum(isinstance(m, ByPresence | ByFlag) for m in found.metadata) != 1:
                    raise TypeError(
                        f"{cls.__name__}.{name}: an Inheritable field needs exactly "
                        + "one encoding, ByPresence() or ByFlag(...), which says "
                        + "how the edge stores an override"
                    )
                _refuse_validators_after_the_kind(cls, name)
            elif defaults:
                raise TypeError(
                    f"{cls.__name__}.{name}: only an Inheritable field has a display "
                    + "Default; the other kinds use the field's own default"
                )
            if found.kind is not Kind.INHERITABLE and any(
                isinstance(m, Authority) for m in found.metadata
            ):
                raise TypeError(
                    f"{cls.__name__}.{name}: Authority replaces the effective value "
                    + "of an Inheritable field with the profile's; this field is "
                    + f"{found.kind.value}, which has no profile value to replace it "
                    + "with"
                )
        check_groups(cls)

    @override
    def __setattr__(self, name: str, value: object) -> None:
        if not name.startswith("_"):
            cls = type(self)
            layer = cls.layer()
            if layer is Effective or layer is Composed:
                raise TypeError(
                    _READ_ONLY_MESSAGE.format(
                        model=cls.__name__, name=name, layer=layer.__name__
                    )
                )
            if layer is Edge and name in cls._owned_fields():
                key = wire_name(cls, name)
                raise TypeError(
                    f"{cls.__name__}.{name}: the edge layer holds who owns this "
                    + "value, and assigning a plain value would lose that. Use "
                    + f".{name}.set_local(value) to override the profile, "
                    + f".{name}.inherit() to inherit it, .{name}.localize() to "
                    + "copy the profile's value in and edit it; in a patch, "
                    + f'{{"{key}": <value>}} overrides and {{"{key}": null}} inherits'
                )
        super().__setattr__(name, value)

    @classmethod
    @functools.cache
    def _dump_rules(cls) -> tuple[_DumpRule, ...]:
        layer = cls.layer()
        rules: list[_DumpRule] = []
        for name, info in cls.model_fields.items():
            kind = field_kind(info).kind
            alias = wire_name(cls, name)
            if (kind is Kind.EDGE_ONLY and layer is Profile) or (
                kind is Kind.PROFILE_ONLY and layer is Edge
            ):
                rules.append(_DumpRule(name, alias, "drop", None))
            elif kind is Kind.INHERITABLE and layer is Edge:
                rules.append(_DumpRule(name, alias, "owned", None))
            elif kind is Kind.INHERITABLE and layer is Profile:
                rules.append(_DumpRule(name, alias, "unless_stored", _default_of(info)))
        return tuple(rules)

    @override
    def _finish_dump(
        self,
        dumped: JsonObject,
        *,
        by_alias: bool,
        compact_value: Callable[[object], object] | None,
    ) -> JsonObject:
        # What the layer writes: an edge field with nothing stored, and the
        # fields of the other layer, are left out; a profile field is written
        # when the profile stores it (assigned or read from data) or when its
        # value is not the display default, so reading a display default
        # never writes it.
        for rule in type(self)._dump_rules():
            key = rule.alias if by_alias else rule.name
            if rule.action == "drop":
                _ = dumped.pop(key, None)
            elif rule.action == "owned":
                owned: object = getattr(self, rule.name)
                if not isinstance(owned, Owned):
                    continue
                if owned.stored is MISSING:  # pyright: ignore[reportUnknownMemberType]
                    _ = dumped.pop(key, None)
                elif compact_value is not None:
                    dumped[key] = compact_value(owned.stored)  # pyright: ignore[reportUnknownMemberType]
            elif rule.default is not None and not _is_stored(
                self, rule.name, rule.default
            ):
                _ = dumped.pop(key, None)
        return dumped
