"""Field declaration toolkit for VcoModel.

Provides:
- Constants and sentinels (CONTEXT_NOT_SET)
- ConfigContext enum and ContextDefaults dataclass
- Normalizer helpers for compact serialization equivalence
- Annotated validators (NoneIsFalse, NoneIsTrue, NoneIsZero, NoneIsMissing, CoerceInt)
- DefaultingModel for fallback-to-default validation
- Composable Annotated markers (Alias, EdgeOnly, ProfileOnly, Normalizer,
  ContextDefault, OwnsRef, RefOnly, Provides, RefersTo, HubRole)
"""

# EdgeOnly and ProfileOnly are the kinds of the layer models: their static
# types and their runtime carriers are in layers.py, which imports this module.
# pyright: reportImportCycles=false

from __future__ import annotations

import dataclasses
import difflib
import inspect
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass, field
from enum import StrEnum
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Final,
    Literal,
    NewType,
    Self,
    TypeAliasType,
    Union,  # pyright: ignore[reportDeprecated]  # get_origin(Optional[X]) is typing.Union
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from pydantic import (
    MISSING,
    BaseModel,
    BeforeValidator,
    GetCoreSchemaHandler,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
)
from pydantic_core import CoreSchema, core_schema

from .identity import SERVICE_TYPES

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

CONTEXT_NOT_SET: object = object()  # sentinel for "use field's own default"


class ConfigContext(StrEnum):
    """The level a configuration is read or written at: an Edge's or a profile's."""

    EDGE = "edge"
    PROFILE = "profile"


type NormalizerFn = Callable[[object, ConfigContext | None], object]
"""Canonical normalizer signature: ``(value, active_config_level) -> normalized``."""


@dataclass(frozen=True)
class ContextDefaults:
    """Per-level default overrides.

    Each entry is one of: ``CONTEXT_NOT_SET`` (use the field's own default),
    ``MISSING`` (field is absent at that level), a zero-arg factory, or a value.
    """

    edge: object = CONTEXT_NOT_SET
    profile: object = CONTEXT_NOT_SET

    def for_level(self, level: ConfigContext) -> object:
        """The entry for *level*: ``edge`` for ``EDGE``, else ``profile``."""
        return self.edge if level is ConfigContext.EDGE else self.profile


def resolve_ctx_default(ctx_default: object) -> object:
    """Invoke callable context defaults (factories like list, dict) to make values."""
    if ctx_default is CONTEXT_NOT_SET or ctx_default is MISSING:
        return ctx_default
    if callable(ctx_default):
        return ctx_default()
    return ctx_default


def _wrap_normalizer(fn: Callable[..., Any]) -> NormalizerFn:
    """Wrap a 1-arg normalizer into the canonical 2-arg (value, ctx) form.

    Inspects the callable's parameter count at registration time so there is
    no runtime overhead per call.
    """
    try:
        sig = inspect.signature(fn)
        nparams = len(sig.parameters)
    except (ValueError, TypeError):
        nparams = 1
    if nparams > 1:
        return fn
    return lambda v, _: fn(v)


@dataclass(frozen=True, slots=True)
class _ProcessedNorm:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""

    fn: NormalizerFn


@dataclass(frozen=True, slots=True)
class _ProcessedCtxDefaults:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""

    defaults: ContextDefaults


def _markers(field_info: FieldInfo) -> tuple[object, ...]:
    """The markers of a field: its own, and those inside its kind (``field_kind``)."""
    from .layers import field_kind  # noqa: PLC0415 (layers imports this module)

    return field_kind(field_info).metadata


def get_normalizer(field_info: FieldInfo) -> NormalizerFn | None:
    """Extract processed normalizer from FieldInfo.metadata."""
    for m in _markers(field_info):
        if isinstance(m, _ProcessedNorm):
            return m.fn
    return None


def get_context_defaults(field_info: FieldInfo) -> ContextDefaults | None:
    """Extract processed context defaults from FieldInfo.metadata."""
    for m in _markers(field_info):
        if isinstance(m, _ProcessedCtxDefaults):
            return m.defaults
    return None


def get_key_spec(field_info: FieldInfo) -> KeySpec | None:
    """The ``KeySpec`` of a field marked ``KeyField``, or None."""
    for m in _markers(field_info):
        if isinstance(m, KeySpec):
            return m
    return None


def get_fixed_list(field_info: FieldInfo) -> FixedList | None:
    """The ``FixedList`` of a field marked with one, or None."""
    for m in _markers(field_info):
        if isinstance(m, FixedList):
            return m
    return None


def has_override_flag(field_info: FieldInfo) -> bool:
    """Check if a field is marked with OverrideFlag."""
    return any(isinstance(m, _ProcessedOverrideFlag) for m in _markers(field_info))


def is_edge_local(field_info: FieldInfo) -> bool:
    """Check if a field is marked with EdgeLocal."""
    return any(isinstance(m, _ProcessedEdgeLocal) for m in _markers(field_info))


def get_resolvable(field_info: FieldInfo) -> _ProcessedResolvable | None:
    """Extract Resolvable info from FieldInfo.metadata, or None."""
    for m in _markers(field_info):
        if isinstance(m, _ProcessedResolvable):
            return m
    return None


def is_positional(field_info: FieldInfo) -> bool:
    """Check if a field is marked with ``Positional``."""
    return any(isinstance(m, Positional) for m in _markers(field_info))


def get_name_of(field_info: FieldInfo) -> type | None:
    """The identity type a field is marked ``NameOf`` for, or None."""
    for m in _markers(field_info):
        if isinstance(m, NameOf):
            return m.identity_type
    return None


def is_server_owned(field_info: FieldInfo) -> bool:
    """Check if a field is marked with ``ServerOwned``."""
    return any(isinstance(m, ServerOwned) for m in _markers(field_info))


def is_secret(field_info: FieldInfo) -> bool:
    """Check if a field is marked with ``Secret``."""
    return any(isinstance(m, Secret) for m in _markers(field_info))


def get_owns_ref(field_info: FieldInfo) -> OwnsRef[Any] | None:
    """The ``OwnsRef`` a field is marked with, or None."""
    for m in _markers(field_info):
        if isinstance(m, OwnsRef):
            return cast("OwnsRef[Any]", m)
    return None


def get_ref_only(field_info: FieldInfo) -> RefOnly | None:
    """The ``RefOnly`` a field is marked with, or None."""
    for m in _markers(field_info):
        if isinstance(m, RefOnly):
            return m
    return None


def service_type_of(model: BaseModel | type[BaseModel], field_name: str) -> str | None:
    """The type of network service the field *field_name* of *model* takes.

    ``None`` for a field that is no provider field: one with neither a ``RefOnly``
    nor an ``OwnsRef`` marker, or whose ref name is not in ``SERVICE_TYPES``. The
    ref name is the ``ref`` of *model* when it is an instance that has one as
    text, else the field's ``RefOnly`` default, else its ``OwnsRef`` name. A
    *field_name* that is no field of *model* raises ``KeyError``.
    """
    if isinstance(model, type):
        return _service_type(model.model_fields[field_name])
    field_info = type(model).model_fields[field_name]
    return _service_type(field_info, getattr(model, "ref", None))


def _service_type(field_info: FieldInfo, stub_ref: object = None) -> str | None:
    """``service_type_of`` from a field's *field_info*.

    *stub_ref* is the ``ref`` its model holds, or ``None``.
    """
    ref_only = get_ref_only(field_info)
    owns_ref = get_owns_ref(field_info)
    if ref_only is None and owns_ref is None:
        return None
    name: str | None
    if isinstance(stub_ref, str) and stub_ref:
        name = stub_ref
    elif ref_only is not None:
        name = ref_only.default
    else:
        name = None if owns_ref is None else owns_ref.name
    return None if name is None else SERVICE_TYPES.get(name)


def get_provides(field_info: FieldInfo) -> Provides | None:
    """The ``Provides`` a field is marked with, or None."""
    for m in _markers(field_info):
        if isinstance(m, Provides):
            return m
    return None


def get_refers_to(field_info: FieldInfo) -> RefersTo | None:
    """The ``RefersTo`` a field is marked with, or None."""
    for m in _markers(field_info):
        if isinstance(m, RefersTo):
            return m
    return None


def get_hub_role(field_info: FieldInfo) -> HubRole | None:
    """The ``HubRole`` a field is marked with, or None."""
    for m in _markers(field_info):
        if isinstance(m, HubRole):
            return m
    return None


# ---------------------------------------------------------------------------
# Annotated validators
# ---------------------------------------------------------------------------


def _none_to(default: Any) -> Callable[[Any], Any]:
    """Create a validator that replaces None with *default*."""

    def validator(v: Any) -> Any:
        return default if v is None else v

    return validator


_none_to_missing = _none_to(MISSING)
_none_to_false = _none_to(False)
_none_to_true = _none_to(True)
_none_to_zero = _none_to(0)


def _coerce_int(v: Any) -> Any:
    """Accept quoted-int strings ("42") in addition to real ints.

    Pydantic v2 does not coerce str->int in lax mode; some VCO endpoints
    inconsistently return numeric fields as strings on older builds. Attach
    this to ``int`` fields whose wire form has been observed to vary.
    """
    if isinstance(v, str) and v.strip():
        try:
            return int(v)
        except ValueError:
            return v  # let pydantic raise the real error
    return v


CoerceInt = BeforeValidator(_coerce_int)


# ---------------------------------------------------------------------------
# DefaultingModel
# ---------------------------------------------------------------------------


def _in_patch(info: ValidationInfo) -> bool:
    """Whether this validation is of a value from a patch (context ``{"patch": True}``).

    ``merge_patch`` validates the values it assigns with that context. The
    defaulting markers do not fall back to a default there: a bad value in a
    patch is an error, not something to tolerate.
    """
    return bool((info.context or {}).get("patch"))


def Lenient(rewrite: Callable[[Any], Any]) -> BeforeValidator:  # noqa: N802 (an Annotated marker, named like the types it stands beside)
    """Annotated marker: *rewrite* a value on parse, to tolerate a bad one from the VCO.

    Not in a patch (see ``_in_patch``): there the value is left as it is, and
    the field's type accepts it or raises.

    Usage: ``lease: Annotated[int, Lenient(lambda v: 86400 if v is None else v)]``
    """

    def _validator(v: Any, info: ValidationInfo) -> Any:
        return v if _in_patch(info) else rewrite(v)

    return BeforeValidator(_validator)


NoneIsMissing = Lenient(_none_to_missing)
NoneIsFalse = Lenient(_none_to_false)
NoneIsTrue = Lenient(_none_to_true)
NoneIsZero = Lenient(_none_to_zero)


def _audit(info: ValidationInfo, v: Any, reason: str) -> None:
    ctx = info.context or {}
    audit = ctx.get("audit")
    if audit is None or not isinstance(audit, list):
        return

    audit.append(
        {
            "field": info.field_name if info.field_name is not None else "<unknown>",
            "bad_value": v,
            "reason": reason,
            "action": "set_to_default",
        }
    )


def DefaultingModel[T](  # noqa: N802 (an Annotated marker, named like the types it stands beside)
    model_type: type[T] | UnionType,
    *,
    default_factory: Callable[..., Any] | None = None,
) -> BeforeValidator:
    """Annotated marker: validate as *model_type*, and fall back to a default.

    The default is what *default_factory* returns, else ``model_type()``. The bad
    value is appended to the validation context's ``audit`` list, when it has one.
    Not in a patch (see ``_in_patch``): there a failure raises.

    Usage: ``networks: Annotated[list[BgpNetwork], DefaultingModel(list[BgpNetwork],
    default_factory=list)]``
    """
    adapter: TypeAdapter[Any] = TypeAdapter(cast("Any", model_type))

    if default_factory is None:
        default_factory = model_type  # pyright: ignore[reportAssignmentType]  # ty: ignore[invalid-assignment]

    def _validator(v: Any, info: ValidationInfo) -> Any:
        if _in_patch(info):
            return adapter.validate_python(v, context=info.context)
        try:
            return adapter.validate_python(v)
        except ValidationError as e:
            _audit(
                info, v, f"failed model validation: {e.errors(include_context=False)}"
            )
            return default_factory()  # pyright: ignore[reportOptionalCall]  # ty: ignore[call-non-callable]

    return BeforeValidator(_validator)


def DefaultingScalar(model_type: Any, *, default: Any) -> BeforeValidator:  # noqa: N802 (an Annotated marker, named like the types it stands beside)
    """Annotated marker: fall back to *default* when validation fails.

    Not in a patch (see ``_in_patch``): there a failure raises.

    Usage: duplex: Annotated[PortDuplex, DefaultingScalar(PortDuplex,
    default="FULL")] = "FULL"
    """
    adapter = TypeAdapter(model_type)

    def _validator(v: Any, info: ValidationInfo) -> Any:
        if _in_patch(info):
            return adapter.validate_python(v, context=info.context)
        try:
            return adapter.validate_python(v)
        except ValidationError as e:
            _audit(
                info, v, f"failed model validation: {e.errors(include_context=False)}"
            )
            return default

    return BeforeValidator(_validator)


# ---------------------------------------------------------------------------
# Composable Annotated markers
# ---------------------------------------------------------------------------


class Alias:
    """Annotated marker for non-standard VCO JSON key aliases.

    Usage: dh_group: Annotated[int, Alias("DHGroup")] = 0
    """

    __slots__: ClassVar[tuple[str, ...]] = ("key",)

    def __init__(self, key: str) -> None:
        """*key* is the VCO's JSON key for the field."""
        self.key: str = key


if TYPE_CHECKING:
    from .effective import Resolved
    from .layers import Composed, Edge, Effective, Layered, Profile

    class EdgeOnly[T]:
        """Annotated marker: field absent at profile level.

        Subscripted, ``EdgeOnly[T]`` is the field kind of a ``Layered`` model:
        the field exists only in the edge layer (``T``), and the effective
        view gives it with its origin (``Resolved[T]``).
        """

        @overload
        def __get__(self, obj: None, owner: type) -> Self: ...
        @overload
        def __get__(self, obj: Layered[Edge], owner: type | None = None) -> T: ...
        @overload
        def __get__(
            self, obj: Layered[Effective], owner: type | None = None
        ) -> Resolved[T]: ...
        @overload
        def __get__(self, obj: Layered[Composed], owner: type | None = None) -> T: ...
        def __get__(self, obj: object, owner: type | None = None) -> object:
            """The field's value, typed per layer by the overloads above."""

        def __set__(self, obj: object, value: T) -> None:
            """Assign the field a ``T``."""

    class ProfileOnly[T]:
        """Annotated marker: field absent at edge level.

        Subscripted, ``ProfileOnly[T]`` is the field kind of a ``Layered``
        model: the field exists only in the profile layer (``T``), and the
        effective view gives it with its origin (``Resolved[T]``).
        """

        @overload
        def __get__(self, obj: None, owner: type) -> Self: ...
        @overload
        def __get__(self, obj: Layered[Profile], owner: type | None = None) -> T: ...
        @overload
        def __get__(
            self, obj: Layered[Effective], owner: type | None = None
        ) -> Resolved[T]: ...
        @overload
        def __get__(self, obj: Layered[Composed], owner: type | None = None) -> T: ...
        def __get__(self, obj: object, owner: type | None = None) -> object:
            """The field's value, typed per layer by the overloads above."""

        def __set__(self, obj: object, value: T) -> None:
            """Assign the field a ``T``."""

else:

    class EdgeOnly:
        """Annotated marker: field absent at profile level.

        Subscripted, ``EdgeOnly[T]`` is the field kind of a ``Layered`` model:
        the field exists only in the edge layer (``T``), and the effective
        view gives it with its origin (``Resolved[T]``).
        """

        def __class_getitem__(cls, item: object) -> object:
            """``EdgeOnly[item]``: the ``Kind.EDGE_ONLY`` carrier of *item*."""
            from .layers import Kind, carrier  # noqa: PLC0415 (layers imports this module)

            return carrier(Kind.EDGE_ONLY, item)

    class ProfileOnly:
        """Annotated marker: field absent at edge level.

        Subscripted, ``ProfileOnly[T]`` is the field kind of a ``Layered``
        model: the field exists only in the profile layer (``T``), and the
        effective view gives it with its origin (``Resolved[T]``).
        """

        def __class_getitem__(cls, item: object) -> object:
            """``ProfileOnly[item]``: the ``Kind.PROFILE_ONLY`` carrier of *item*."""
            from .layers import Kind, carrier  # noqa: PLC0415 (layers imports this module)

            return carrier(Kind.PROFILE_ONLY, item)


class Normalizer:
    """Annotated marker: normalizer for compact serialization equivalence."""

    __slots__: ClassVar[tuple[str, ...]] = ("fn",)

    def __init__(self, fn: Callable[..., Any]) -> None:
        """*fn* takes the value, or the value and the active ``ConfigContext``."""
        self.fn: Callable[..., Any] = fn


class ContextDefault:
    """Annotated marker: context-specific defaults."""

    __slots__: ClassVar[tuple[str, ...]] = ("edge", "profile")

    def __init__(
        self, *, edge: Any = CONTEXT_NOT_SET, profile: Any = CONTEXT_NOT_SET
    ) -> None:
        """Each level not given uses the field's own default (``CONTEXT_NOT_SET``)."""
        self.edge: Any = edge
        self.profile: Any = profile


class KeyField:
    """Annotated marker: the items of a list field are named by a key.

    Usage: ``segments: Annotated[list[Segment], KeyField("segment.name",
    alt="segment.segment_id")]``

    Each path is a dotted path of python field names. The key is the value at
    the one path, or the tuple of the values at several (a composite key).
    *alt* names a second field that identifies an item on its own (a stable
    ID): a selector that is no item's key is tried as it. *label* names a
    field people use for the item that is not kept unique. *fold_case* is for
    keys the VCO compares ignoring letter case (a MAC address): two keys that
    differ only by case are one key wherever keys of the list are compared,
    and the stored text of a key is never changed. Stored as a ``KeySpec``
    (``get_key_spec``).
    """

    __slots__: ClassVar[tuple[str, ...]] = ("alt", "fold_case", "key_paths", "label")

    def __init__(
        self,
        *key_paths: str,
        alt: str | None = None,
        label: str | None = None,
        fold_case: bool = False,
    ) -> None:
        """Raises ``TypeError`` when no key path is given."""
        if not key_paths:
            raise TypeError("KeyField needs at least one key path")
        self.key_paths: tuple[str, ...] = key_paths
        self.alt: str | None = alt
        self.label: str | None = label
        self.fold_case: bool = fold_case


@dataclass(frozen=True, slots=True)
class KeySpec:
    """How the items of a ``KeyField`` list are named (``get_key_spec``).

    Stored in FieldInfo.metadata by _apply_vco_markers. *paths* are the key's
    paths, one per part; *alt* is the alt key's path and *label* the label's,
    if the list has them; *fold_case*, whether keys are compared ignoring
    letter case. See ``KeyField``.
    """

    paths: tuple[str, ...]
    alt: str | None = None
    label: str | None = None
    fold_case: bool = False


@dataclass(frozen=True, slots=True)
class FixedList:
    """Annotated marker: the entries of a keyed list of models are fixed.

    They are fixed but for some fields.

    Usage: ``defaults: Annotated[list[Rule], KeyField("name"),
    FixedList(editable=("action",))]``. Put it beside a ``KeyField``.

    The entries, their keys and their order stay as they are: a patch may
    change the fields in *editable* (python names) of an entry, and nothing
    else. ``merge_patch`` refuses any other edit with a ``PatchError`` of kind
    ``fixed_list`` (``get_fixed_list``).
    """

    editable: tuple[str, ...]


class OverrideFlag:
    """Annotated marker: this boolean field is an override indicator.

    When the merge engine modifies any non-EdgeLocal sibling field (or
    descendant) in the containing model, fields marked OverrideFlag are
    auto-set to True.

    Self-contained: automatically implies EdgeOnly (override fields are
    inherently edge-only) and coerces None → False. Fields using this
    marker only need ``Annotated[bool, OverrideFlag]``.
    """


@dataclass(frozen=True, slots=True)
class _ProcessedOverrideFlag:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""


class EdgeLocal:
    """Annotated marker: field can be set at edge level without triggering override.

    Changes to EdgeLocal fields do not cause the nearest ancestor
    OverrideFlag to be set to True. This handles VCO's "whitelisted"
    fields that can differ per-edge even when the containing object
    is owned by the profile template.
    """


@dataclass(frozen=True, slots=True)
class _ProcessedEdgeLocal:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""


class Resolvable[IdentityT]:
    """Annotated marker: field accepts a name, resolves to a specific ID.

    The merge engine detects when a string is provided where a newtype ID
    is expected and calls the registered resolver for *identity_type*,
    reading the ID off the identity it finds with *accessor*.

    *accessor* is read once, on a probe that records what it reads, when the
    marker is built: it must read exactly one field of *identity_type* and
    return it, as in ``lambda i: i.segment_id``. That field's name is kept as
    ``attribute``. ``apply_vco_markers`` then refuses, at class creation, a
    field whose type cannot hold what the accessor returns.
    """

    __slots__: ClassVar[tuple[str, ...]] = ("accessor", "attribute", "identity_type")

    def __init__(
        self, identity_type: type[IdentityT], accessor: Callable[[IdentityT], object]
    ) -> None:
        """Probe *accessor* for the one field it reads, kept as ``attribute``."""
        self.identity_type: type[IdentityT] = identity_type
        self.accessor: Callable[[IdentityT], object] = accessor
        self.attribute: str = _probe_accessor(identity_type, accessor)


def _identity_field_names(identity_type: type) -> list[str]:
    """The names of the dataclass fields of *identity_type*, in order.

    Any other class has none.
    """
    if not dataclasses.is_dataclass(identity_type):
        return []
    return [f.name for f in dataclasses.fields(identity_type)]


class _ProbeAnswer:
    """What the probe answers for each attribute read.

    It cannot be tested for truth, so an accessor that falls back to another value for a
    falsy one (``i.segment_id or None``) is refused.
    """

    __slots__: ClassVar[tuple[str, ...]] = ()

    def __bool__(self) -> bool:
        raise TypeError("the value read was tested for truth")


@dataclass(frozen=True)
class _MarkerWords:
    """How the refusals of a marker that reads an identity name it.

    *written* is how the marker is written, with ``{name}`` for the identity and
    ``{accessor}`` for the accessor, and *consequence* what an accessor that is no
    plain read could lead to. *marker* is the marker's name, *mismatch* the sentence
    on what a field that cannot hold the attribute would do, and *fitting* how an
    accessor that fits is written, with the same two places as *written*.
    """

    written: str
    consequence: str
    marker: str
    mismatch: str
    fitting: str


_RESOLVABLE_WORDS: Final = _MarkerWords(
    written="Resolvable({name}, {accessor})",
    consequence="a name resolved through it could give a value the field refuses",
    marker="Resolvable",
    mismatch=(
        "A name resolved through it would give a value the field refuses, and that "
        "would show only when a name is merged in."
    ),
    fitting="Resolvable({name}, {accessor})",
)
_OWNS_REF_WORDS: Final = _MarkerWords(
    written="OwnsRef(..., {name}, by={accessor}, ...)",
    consequence="a ref row could be matched by a value the field never holds",
    marker="OwnsRef",
    mismatch=(
        "A ref row could never be matched to the value the field holds, and that "
        "would show only when a write derives its refs."
    ),
    fitting="by={accessor}",
)


def _probe_accessor(
    identity_type: type, accessor: object, words: _MarkerWords = _RESOLVABLE_WORDS
) -> str:
    """The name of the one field of *identity_type* that *accessor* reads and returns.

    Any other accessor raises ``TypeError``, whose text names the marker as *words*
    write it.

    The accessor is called once on a stand-in for the identity that records each
    attribute read and answers a unique sentinel. One that reads a name that is
    not a field, reads several, computes from what it reads or raises cannot be
    checked against the type of the field it goes on, so none is accepted. The
    answer cannot be tested for truth: an accessor that falls back to another
    value for a falsy ID (the Global Segment's ``segment_id`` is 0) is refused.
    """
    read, problem = _probe_problem(identity_type, accessor)
    if problem is None:
        return read[0]
    raise TypeError(_probe_refusal(identity_type, accessor, read, problem, words))


def _probe_problem(
    identity_type: type, accessor: object
) -> tuple[list[str], str | None]:
    """The names *accessor* read on the stand-in, and what is wrong with it.

    The problem is None when it read one field of *identity_type* and returned it.
    """
    read: list[str] = []
    if not callable(accessor):
        return read, f"{accessor!r} is not a function."
    answer = _ProbeAnswer()

    class Probe:
        def __getattr__(self, name: str) -> object:
            read.append(name)
            return answer

    try:
        returned = cast("Callable[[object], object]", accessor)(Probe())
    except Exception as err:  # noqa: BLE001 - whatever it raises, it is not a plain read
        returned = err
    problem: str | None = None
    if not read and isinstance(returned, Exception):
        problem = (
            f"It raised {type(returned).__name__}: {returned} before reading any "
            "field of the identity."
        )
    elif not read:
        problem = "It read no field of the identity."
    elif len(read) > 1:
        problem = f"It read several fields ({', '.join(read)}); it must read one."
    elif read[0] not in _identity_field_names(identity_type):
        problem = (
            f"It read '{read[0]}', which is not a field of {identity_type.__name__}."
        )
    elif returned is not answer:
        problem = f"It did more than return '{read[0]}'."
    return read, problem


def _probe_refusal(
    identity_type: type,
    accessor: object,
    read: list[str],
    problem: str,
    words: _MarkerWords,
) -> str:
    """The refusal of an accessor that *problem* says is wrong, with one to write.

    The suggested accessor reads the field it read, or the field closest to it, or
    the first field of *identity_type*. *words* say how the marker is written.
    """
    names = _identity_field_names(identity_type)
    target = read[0] if read else accessor if isinstance(accessor, str) else None
    if target in names:
        suggested = target
    elif target is not None and (close := difflib.get_close_matches(target, names, 1)):
        suggested = close[0]
    else:
        suggested = names[0] if names else "<field>"
    name = identity_type.__name__
    return (
        f"{words.written.format(name=name, accessor='accessor')} needs an accessor "
        f"that reads one field of {name} and returns it. {problem} An accessor that "
        "does anything else cannot be checked against the type of the field it goes "
        f"on, so {words.consequence}. Write it as "
        f"{words.written.format(name=name, accessor=f'lambda i: i.{suggested}')}."
    )


@dataclass(frozen=True, slots=True)
class NameOf:
    """Annotated marker: this string field holds the name of an identity.

    ``merge_patch`` fills the sibling ``Resolvable(identity_type, ...)`` fields
    that a new item's patch leaves out from this field's value. A new item that
    gives an ID of the identity (a ``Resolvable`` field of the type whose values
    are not a fixed set, as a ``Literal`` is) and not this name takes the name,
    and the other fields it lacks, from the identity that ID finds. An unknown
    ID raises ``ResolutionError``. No other string in a patch is tried as a
    name.

    Usage: ``name: Annotated[str, NameOf(SegmentIdentity)]``
    """

    identity_type: type


@dataclass(frozen=True, slots=True)
class Positional:
    """Annotated marker: order is the only identity of this list's items.

    Its items are selected by position (``at(n)``) or the list is replaced
    whole; no field of an item names it.

    Usage: ``nodes: Annotated[list[Node], Positional()]``
    """


@dataclass(frozen=True, slots=True)
class ServerOwned:
    """Annotated marker: the VCO may store another value than the one sent.

    The response check leaves the field out: a difference there is reported,
    and is never a write that was not applied.

    Usage: ``rule_logical_id: Annotated[str | MISSING, ServerOwned()] = MISSING``
    """


@dataclass(frozen=True, slots=True)
class Secret:
    """Annotated marker: the field holds a configuration secret.

    A secret is a password, a key or a community string. The ``data`` of a
    ref row is marked too: it is the data of the object the row refers to,
    which can hold a service's credentials.

    ``hide_secrets`` shows ``***`` in its place; the value itself is read,
    compared and written like any other.

    Usage: ``password: Annotated[str, Secret()] = ""``
    """


@dataclass(frozen=True, slots=True)
class OwnsRef[IdentityT]:
    """Annotated marker: this data field holds an identity that a ref row goes with.

    A row of ref name *name* goes with the identity. *identity* is
    the identity type, whose resolver gives the ``object_id`` a new row needs;
    *by* is an accessor that reads the field of the identity whose value the data
    holds, as ``Resolvable``'s does, and *row* the row field that value is compared
    with (``"logicalId"``, ``"data.segmentId"``). *value* is the path from the
    annotated field to that value, in field names, when the marker sits on an
    object and not on the scalar. *per_segment* is true for a ref that is kept per
    segment. *level* limits the declaration to one config level; None is both.

    *by* is probed when the marker is built, as ``Resolvable``'s accessor is: it
    must read exactly one field of *identity* and return it, and that field's name
    is kept as ``attribute``. Two markers that differ only in the accessor object
    are equal. ``apply_vco_markers`` then refuses, at class creation, a field (or
    the field *value* names) whose type cannot hold that attribute.

    Usage: ``logical_id: Annotated[uuid.UUID | Literal[""],
    OwnsRef("deviceSettings:css:provider", NetworkServiceIdentity,
    by=lambda i: i.logical_id, row="logicalId", per_segment=True)] = ""``
    """

    name: str
    identity: type[IdentityT]
    _: KW_ONLY
    by: Callable[[IdentityT], object] = field(compare=False)
    row: str
    value: str | None = None
    per_segment: bool = False
    level: ConfigContext | None = None
    attribute: str = field(init=False, default="")

    def __post_init__(self) -> None:
        """Probe *by* for the field of *identity* it reads, kept as ``attribute``."""
        object.__setattr__(
            self, "attribute", _probe_accessor(self.identity, self.by, _OWNS_REF_WORDS)
        )


@dataclass(frozen=True, slots=True)
class Provides:
    """Annotated marker: the items of this list (or map) are referred to by name.

    They are the things a set of names refers to.

    *name* is the name of the set (``"segment"``); *key* is the dotted
    python path in an item to the value a ``RefersTo`` of the same name holds
    (as a ``KeyField`` path is). *per_segment* is true when the set is that
    of one segment's entries, not of the whole module: the ``RefersTo`` fields
    are then read inside the segment that holds them.

    Usage: ``segments: Annotated[list[DeviceSegment], KeyField("segment.name"),
    Provides("segment", key="segment.segment_id")]``
    """

    name: str
    _: KW_ONLY
    key: str
    per_segment: bool = False


@dataclass(frozen=True, slots=True)
class RefersTo:
    """Annotated marker: this scalar field holds a value of a ``Provides`` set.

    The value is one that a ``Provides`` of the same name lists.

    A value that no item of the set has is a dangling reference. *none* lists
    the values that say "no reference" (``-1`` for an interface that is in no
    segment) and are never looked up. *per_segment* is true when the set is
    the one of the segment the field sits in; it agrees with the
    ``Provides`` of the same name.

    Usage: ``segment_id: Annotated[int, RefersTo("segment", none=(-1,))] = -1``
    """

    name: str
    _: KW_ONLY
    none: tuple[object, ...] = ()
    per_segment: bool = False


@dataclass(frozen=True, slots=True)
class HubRole:
    """Annotated marker: this list holds the hubs that play a role for its segment.

    *role* is ``"hub"`` (the Edges and clusters the segment's Edges connect
    to as a hub), ``"bridge"`` (those that bridge Edge to Edge traffic) or
    ``"backhaul"`` (those that carry the segment's backhauled traffic).

    Usage: ``vpn_hubs: Annotated[list[VpnHub], KeyField("logical_id"),
    HubRole("hub")]``
    """

    role: Literal["hub", "bridge", "backhaul"]


def _ref_only_is_not_read(value: Any, info: ValidationInfo) -> Any:
    """What a ``RefOnly`` field takes from validated input.

    The input is not read, except from a patch.
    """
    return value if _in_patch(info) else MISSING


@dataclass(frozen=True, slots=True)
class RefOnly:
    """Annotated marker: this field stands for a ref row.

    It is never part of the wire data.

    A dump leaves it out, and validation does not read a key of its name: the
    field stays ``MISSING`` and the key is not kept as an extra. That holds
    for validated input (``model_validate``, a parent's validation of its
    data). A keyword of the constructor, a patch (``merge_patch``) and an
    attribute assignment do set it, and a patch ``null`` sets ``None``:
    ``MISSING`` says the module was read without refs, ``None`` says there is
    no row. The ref name is the value of the sibling ``ref`` field of the same
    model; *default* is the name that a value given in one of those ways gives
    a model whose ``ref`` is ``MISSING``. A model with no ``ref`` field has
    nothing to fill, and *default* is not used.

    Code that fills the field from a module's refs does it with
    ``object.__setattr__(model, name, value)``: that sets neither the sibling
    ``ref`` nor ``model_fields_set``.

    Usage: ``service: Annotated[uuid.UUID | None | MISSING,
    RefOnly(default="deviceSettings:authentication")] = MISSING``
    """

    default: str | None = None

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """The field's schema, after a validator that leaves validated input unread.

        The input is kept only from a patch; otherwise the field is ``MISSING``.
        """
        return core_schema.with_info_before_validator_function(
            _ref_only_is_not_read, handler(source)
        )


def ref_only_defaults(cls: type[BaseModel]) -> dict[str, str | None]:
    """Each ``RefOnly`` field of *cls* with its marker's *default*."""
    found: dict[str, str | None] = {}
    for name, field_info in cls.model_fields.items():
        marker = get_ref_only(field_info)
        if marker is not None:
            found[name] = marker.default
    return found


@dataclass(frozen=True, slots=True)
class _ProcessedResolvable:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""

    identity_type: type[object]
    accessor: Callable[[Any], object]
    attribute: str


def _absent_at_other_level(field_info: FieldInfo) -> ContextDefaults | None:
    """What makes a field of one layer absent at the other level.

    A field of one layer is of the kind ``EdgeOnly`` or ``ProfileOnly``, and this makes
    it absent as the bare markers do; None for any other field.
    """
    from .layers import Kind, field_kind  # noqa: PLC0415 (layers imports this module)

    kind = field_kind(field_info).kind
    if kind is Kind.EDGE_ONLY:
        return ContextDefaults(profile=MISSING)
    if kind is Kind.PROFILE_ONLY:
        return ContextDefaults(edge=MISSING)
    return None


@dataclass(frozen=True)
class _LiteralValue:
    """One value of a ``Literal``, as a type atom of its own."""

    value: object


def _type_atoms(tp: object) -> frozenset[object]:
    """The types *tp* allows, apart from ``None`` and ``MISSING``.

    ``Annotated``, ``type`` aliases and unions are taken apart, a ``NewType`` counts
    as its supertype, and each ``Literal`` value is an atom.
    """
    while isinstance(tp, NewType) or get_origin(tp) is Annotated:
        tp = tp.__supertype__ if isinstance(tp, NewType) else get_args(tp)[0]
    if isinstance(tp, TypeAliasType):
        return _type_atoms(tp.__value__)
    if get_origin(tp) is Literal:
        return frozenset(_LiteralValue(v) for v in get_args(tp) if v is not None)
    if get_origin(tp) in (Union, UnionType):  # pyright: ignore[reportDeprecated]
        return frozenset().union(*(_type_atoms(m) for m in get_args(tp)))
    return frozenset() if tp is MISSING or tp is type(None) else frozenset({tp})


def _allows_none(tp: object) -> bool:
    """Whether *tp* allows ``None``.

    ``Annotated``, ``type`` aliases and unions are taken apart. ``MISSING`` does not
    count.
    """
    while get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    if isinstance(tp, TypeAliasType):
        return _allows_none(tp.__value__)
    if get_origin(tp) is Literal:
        return None in get_args(tp)
    if get_origin(tp) in (Union, UnionType):  # pyright: ignore[reportDeprecated]
        return any(_allows_none(m) for m in get_args(tp))
    return tp is None or tp is type(None)


def _atoms_text(atoms: frozenset[object]) -> str:
    """The atoms as a type expression, in a stable order."""
    values = sorted(repr(a.value) for a in atoms if isinstance(a, _LiteralValue))
    named = sorted(
        a.__name__ if isinstance(a, type) else repr(a)
        for a in atoms
        if not isinstance(a, _LiteralValue)
    )
    return " | ".join([*named, *([f"Literal[{', '.join(values)}]"] if values else [])])


def _fits(wanted: object, held: frozenset[object]) -> bool:
    """Whether a field that holds the atoms *held* can hold the atom *wanted*.

    It can when *wanted* is one of them, is a class that subclasses one of them, or is a
    ``Literal`` value that is an instance of one of them.
    """
    if wanted in held:
        return True
    classes = tuple(h for h in held if isinstance(h, type))
    if isinstance(wanted, _LiteralValue):
        return isinstance(wanted.value, classes)
    return isinstance(wanted, type) and issubclass(wanted, classes)


def _check_resolvable(
    cls: type[BaseModel],
    field_name: str,
    fi: FieldInfo,
    marker: Resolvable[Any],
) -> None:
    """Refuse a field that cannot hold what its ``Resolvable`` accessor returns.

    The field's ID type is the type held inside its field kind; ``_check_holds``
    says what it must allow. A field that cannot hold the attribute is refused here
    because it would otherwise fail only when a name resolved to a value it refuses.
    """
    from .layers import field_kind  # noqa: PLC0415 (layers imports this module)

    _check_holds(
        f"{cls.__name__}.{field_name}",
        field_kind(fi).inner_type,
        marker.identity_type,
        marker.attribute,
        _RESOLVABLE_WORDS,
    )


def _check_holds(
    owner: str,
    held_type: object,
    identity: type,
    attribute: str,
    words: _MarkerWords,
) -> None:
    """Refuse *held_type*, the field *owner*'s type, when it cannot hold *attribute*.

    Each type the identity's *attribute* allows apart from ``None`` must fit
    *held_type* apart from ``None`` and ``MISSING`` (``Annotated`` and ``type``
    aliases taken apart): it fits when it is one of the field's types, or is a class
    that subclasses one, or is a ``Literal`` value that is an instance of one. A
    ``NewType`` counts as its supertype on both sides. A field that holds ``Any`` or
    ``object`` is not checked at all. An attribute that allows ``None`` needs a
    field that allows it too (a field that holds only ``MISSING`` besides its type
    does not). The ``TypeError`` names the marker as *words* write it.
    """
    hints = get_type_hints(identity)
    wanted = _type_atoms(hints[attribute])
    held = _type_atoms(held_type)
    if Any in held or object in held:
        return
    gives_none = _allows_none(hints[attribute])
    holds_none = _allows_none(held_type)
    if all(_fits(w, held) for w in wanted) and (holds_none or not gives_none):
        return
    fitting = [
        name
        for name in _identity_field_names(identity)
        if name in hints
        and (atoms := _type_atoms(hints[name]))
        and all(_fits(a, held) for a in atoms)
        and (holds_none or not _allows_none(hints[name]))
    ]
    advice = (
        "Read a field that fits instead: "
        + " or ".join(
            words.fitting.format(name=identity.__name__, accessor=f"lambda i: i.{n}")
            for n in fitting
        )
        if fitting
        else (
            f"No field of {identity.__name__} fits this field's type: change the "
            "field to hold what the identity gives, or read another identity."
        )
    )
    raise TypeError(
        f"{owner} holds {_atoms_text(held)}{' or None' if holds_none else ''}, but "
        f"its {words.marker} reads {identity.__name__}.{attribute}, which can be "
        f"{_atoms_text(wanted)}{' or None' if gives_none else ''}. {words.mismatch} "
        f"{advice}"
    )


def _check_owns_ref(
    cls: type[BaseModel],
    field_name: str,
    fi: FieldInfo,
    marker: OwnsRef[Any],
) -> None:
    """Refuse a field that cannot hold what its ``OwnsRef`` accessor reads.

    The field is the annotated one or, with ``value``, the field that path names:
    its dotted parts are followed from the one model class the field holds, through
    each model's fields. A part that is no field, or a step with no model class to
    follow, raises ``TypeError``. The field found is then checked as
    ``_check_holds`` says, and the refusal names it by the path.
    """
    from .layers import field_kind  # noqa: PLC0415 (layers imports this module)

    owner = f"{cls.__name__}.{field_name}"
    held_type = field_kind(fi).inner_type
    for part in [] if marker.value is None else marker.value.split("."):
        models = [
            a
            for a in _type_atoms(held_type)
            if isinstance(a, type) and issubclass(a, BaseModel)
        ]
        if len(models) != 1 or part not in models[0].model_fields:
            raise TypeError(
                f"{owner}: OwnsRef(value={marker.value!r}) is no path to a field of "
                "what the field holds, so the value a ref row goes with could not "
                "be read. Give the path from this field to the value in field "
                'names, as in value="segment_id".'
            )
        held_type = field_kind(models[0].model_fields[part]).inner_type
    if marker.value is not None:
        owner = f"{owner}.{marker.value}"
    _check_holds(owner, held_type, marker.identity, marker.attribute, _OWNS_REF_WORDS)


def _process_level_marker(fi: FieldInfo, m: object, already: set[type]) -> bool:
    """Process *m* when it is ``EdgeOnly``, ``ProfileOnly``, a normalizer or a default.

    The normalizer is a ``Normalizer`` and the default a ``ContextDefault``.
    Whether *m* was processed: one of them is not when a marker of its kind was
    processed before it (the first marker of a kind wins).
    """
    if (
        m is EdgeOnly or (isinstance(m, type) and issubclass(m, EdgeOnly))
    ) and _ProcessedCtxDefaults not in already:
        fi.metadata.append(_ProcessedCtxDefaults(ContextDefaults(profile=MISSING)))
        already.add(_ProcessedCtxDefaults)
        return True
    if (
        m is ProfileOnly or (isinstance(m, type) and issubclass(m, ProfileOnly))
    ) and _ProcessedCtxDefaults not in already:
        fi.metadata.append(_ProcessedCtxDefaults(ContextDefaults(edge=MISSING)))
        already.add(_ProcessedCtxDefaults)
        return True
    if isinstance(m, Normalizer) and _ProcessedNorm not in already:
        fi.metadata.append(_ProcessedNorm(_wrap_normalizer(m.fn)))
        already.add(_ProcessedNorm)
        return True
    if isinstance(m, ContextDefault) and _ProcessedCtxDefaults not in already:
        fi.metadata.append(
            _ProcessedCtxDefaults(ContextDefaults(edge=m.edge, profile=m.profile))
        )
        already.add(_ProcessedCtxDefaults)
        return True
    return False


def _process_flag_marker(fi: FieldInfo, m: object, already: set[type]) -> bool:
    """Process *m* when it is a ``KeyField``, an ``OverrideFlag`` or ``EdgeLocal``.

    Whether *m* was processed: one of them is not when a marker of its kind was
    processed before it (the first marker of a kind wins).
    """
    if isinstance(m, KeyField) and KeySpec not in already:
        fi.metadata.append(
            KeySpec(m.key_paths, alt=m.alt, label=m.label, fold_case=m.fold_case)
        )
        already.add(KeySpec)
        return True
    if (
        isinstance(m, OverrideFlag)
        or (isinstance(m, type) and issubclass(m, OverrideFlag))
    ) and _ProcessedOverrideFlag not in already:
        fi.metadata.append(_ProcessedOverrideFlag())
        already.add(_ProcessedOverrideFlag)
        if _ProcessedCtxDefaults not in already:
            fi.metadata.append(_ProcessedCtxDefaults(ContextDefaults(profile=MISSING)))
            already.add(_ProcessedCtxDefaults)
        fi.metadata.append(Lenient(_none_to_false))
        return True
    if (
        isinstance(m, EdgeLocal) or (isinstance(m, type) and issubclass(m, EdgeLocal))
    ) and _ProcessedEdgeLocal not in already:
        fi.metadata.append(_ProcessedEdgeLocal())
        already.add(_ProcessedEdgeLocal)
        return True
    return False


def _process_identity_marker(
    cls: type[BaseModel],
    field_name: str,
    fi: FieldInfo,
    marker: object,
    already: set[type],
) -> None:
    """Check *marker* when it is an ``OwnsRef``, or the field's first ``Resolvable``.

    A ``Resolvable`` is then processed; nothing is added for an ``OwnsRef``, as
    ``get_owns_ref`` reads the marker itself. It does nothing for any other marker.
    """
    if isinstance(marker, OwnsRef):
        _check_owns_ref(cls, field_name, fi, cast("OwnsRef[Any]", marker))
    elif isinstance(marker, Resolvable) and _ProcessedResolvable not in already:
        resolvable = cast("Resolvable[Any]", marker)
        _check_resolvable(cls, field_name, fi, resolvable)
        fi.metadata.append(
            _ProcessedResolvable(
                identity_type=resolvable.identity_type,
                accessor=resolvable.accessor,
                attribute=resolvable.attribute,
            )
        )
        already.add(_ProcessedResolvable)


def apply_vco_markers(cls: type[BaseModel]) -> None:
    """Scan FieldInfo.metadata for VCO markers.

    The processed results are stored back in metadata.
    """
    needs_rebuild = False
    _processed_types = (
        _ProcessedNorm,
        _ProcessedCtxDefaults,
        KeySpec,
        _ProcessedOverrideFlag,
        _ProcessedEdgeLocal,
        _ProcessedResolvable,
    )
    for field_name, fi in cls.model_fields.items():
        if not fi.exclude and get_ref_only(fi) is not None:
            fi.exclude = True  # a dump leaves it out
            needs_rebuild = True
        # The markers inside a kind count as the field's own; what is processed
        # is appended to the field's metadata, outside the kind.
        markers = _markers(fi)
        already = {type(m) for m in markers if isinstance(m, _processed_types)}
        absent = _absent_at_other_level(fi)
        if absent is not None and _ProcessedCtxDefaults not in already:
            fi.metadata.append(_ProcessedCtxDefaults(absent))
            already.add(_ProcessedCtxDefaults)
        for m in markers:
            if isinstance(m, Alias):
                fi.validation_alias = m.key
                fi.serialization_alias = m.key
                needs_rebuild = True
            elif not (
                _process_level_marker(fi, m, already)
                or _process_flag_marker(fi, m, already)
            ):
                _process_identity_marker(cls, field_name, fi, m, already)
    if needs_rebuild:
        cls.model_rebuild(force=True)
