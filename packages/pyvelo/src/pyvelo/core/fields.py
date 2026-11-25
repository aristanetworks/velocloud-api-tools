"""Field declaration toolkit for VcoModel.

Provides:
- Constants and sentinels (CONTEXT_NOT_SET)
- ConfigContext enum and ContextDefaults dataclass
- Normalizer helpers for compact serialization equivalence
- Annotated validators (NoneIsFalse, NoneIsTrue, NoneIsZero, NoneIsMissing, CoerceInt)
- DefaultingModel for fallback-to-default validation
- Composable Annotated markers (Alias, EdgeOnly, ProfileOnly, Normalizer, ContextDefault,
  OwnsRef, RefOnly, Provides, RefersTo, HubRole)
"""

# EdgeOnly and ProfileOnly are the kinds of the layer models: their static
# types and their runtime carriers are in layers.py, which imports this module.
# pyright: reportImportCycles=false

from __future__ import annotations

import dataclasses
import difflib
import inspect
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from enum import StrEnum
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
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
    """Invoke callable context defaults (factories like list, dict) to produce values."""
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
    if nparams >= 2:
        return fn
    return lambda v, ctx: fn(v)


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


def get_owns_ref(field_info: FieldInfo) -> OwnsRef | None:
    """The ``OwnsRef`` a field is marked with, or None."""
    for m in _markers(field_info):
        if isinstance(m, OwnsRef):
            return m
    return None


def get_ref_only(field_info: FieldInfo) -> RefOnly | None:
    """The ``RefOnly`` a field is marked with, or None."""
    for m in _markers(field_info):
        if isinstance(m, RefOnly):
            return m
    return None


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


def Lenient(rewrite: Callable[[Any], Any]) -> BeforeValidator:
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


def DefaultingModel[T](
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


def DefaultingScalar(model_type: Any, *, default: Any) -> BeforeValidator:
    """Annotated marker: fall back to *default* when validation fails.

    Not in a patch (see ``_in_patch``): there a failure raises.

    Usage: duplex: Annotated[PortDuplex, DefaultingScalar(PortDuplex, default="FULL")] = "FULL"
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
    field people use for the item that is not kept unique. Stored as a
    ``KeySpec`` (``get_key_spec``).
    """

    __slots__: ClassVar[tuple[str, ...]] = ("alt", "key_paths", "label")

    def __init__(
        self, *key_paths: str, alt: str | None = None, label: str | None = None
    ) -> None:
        """Raises ``TypeError`` when no key path is given."""
        if not key_paths:
            raise TypeError("KeyField needs at least one key path")
        self.key_paths: tuple[str, ...] = key_paths
        self.alt: str | None = alt
        self.label: str | None = label


@dataclass(frozen=True, slots=True)
class KeySpec:
    """How the items of a ``KeyField`` list are named (``get_key_spec``).

    Stored in FieldInfo.metadata by _apply_vco_markers. *paths* are the key's
    paths, one per part; *alt* is the alt key's path and *label* the label's,
    if the list has them. See ``KeyField``.
    """

    paths: tuple[str, ...]
    alt: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class FixedList:
    """Annotated marker: the entries of a keyed list of models are fixed, but for some fields.

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
    """The names of the dataclass fields of *identity_type*, in order; none for any other class."""
    if not dataclasses.is_dataclass(identity_type):
        return []
    return [f.name for f in dataclasses.fields(identity_type)]


class _ProbeAnswer:
    """What the probe answers for each attribute read: it cannot be tested for truth, so an accessor that falls back to another value for a falsy one (``i.segment_id or None``) is refused."""

    __slots__: ClassVar[tuple[str, ...]] = ()

    def __bool__(self) -> bool:
        raise TypeError("the value read was tested for truth")


def _probe_accessor(identity_type: type, accessor: object) -> str:
    """The name of the one field of *identity_type* that *accessor* reads and returns; ``TypeError`` for any other accessor.

    The accessor is called once on a stand-in for the identity that records each
    attribute read and answers a unique sentinel. One that reads a name that is
    not a field, reads several, computes from what it reads or raises cannot be
    checked against the type of the field it goes on, so none is accepted. The
    answer cannot be tested for truth: an accessor that falls back to another
    value for a falsy ID (the Global Segment's ``segment_id`` is 0) is refused.
    """
    names = _identity_field_names(identity_type)
    read: list[str] = []
    answer = _ProbeAnswer()

    class Probe:
        def __getattr__(self, name: str) -> object:
            read.append(name)
            return answer

    problem: str
    if not callable(accessor):
        problem = f"{accessor!r} is not a function."
    else:
        try:
            returned = cast("Callable[[object], object]", accessor)(Probe())
        except Exception as err:  # noqa: BLE001 - whatever it raises, it is not a plain read
            returned = err
        if not read and isinstance(returned, Exception):
            problem = f"It raised {type(returned).__name__}: {returned} before reading any field of the identity."
        elif not read:
            problem = "It read no field of the identity."
        elif len(read) > 1:
            problem = f"It read several fields ({', '.join(read)}); it must read one."
        elif read[0] not in names:
            problem = f"It read '{read[0]}', which is not a field of {identity_type.__name__}."
        elif returned is not answer:
            problem = f"It did more than return '{read[0]}'."
        else:
            return read[0]
    target = read[0] if read else accessor if isinstance(accessor, str) else None
    if target in names:
        suggested = target
    elif target is not None and (close := difflib.get_close_matches(target, names, 1)):
        suggested = close[0]
    else:
        suggested = names[0] if names else "<field>"
    name = identity_type.__name__
    raise TypeError(
        f"Resolvable({name}, accessor) needs an accessor that reads one field of {name} "
        f"and returns it. {problem} An accessor that does anything else cannot be "
        "checked against the type of the field it goes on, so a name resolved "
        "through it could give a value the field refuses. "
        f"Write it as Resolvable({name}, lambda i: i.{suggested})."
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
    """Annotated marker: the field holds a configuration secret (a password, a key, a community string).

    ``hide_secrets`` shows ``***`` in its place; the value itself is read,
    compared and written like any other.

    Usage: ``password: Annotated[str, Secret()] = ""``
    """


@dataclass(frozen=True, slots=True)
class OwnsRef:
    """Annotated marker: this data field holds an identity that a ref row goes with.

    A row of ref name *name* goes with the identity. *identity* is
    the identity type, whose resolver gives the ``object_id`` a new row needs;
    *by* is the attribute of it that the data holds, and *row* the row field
    that value is compared with (``"logicalId"``, ``"data.segmentId"``).
    *value* is the path from the annotated field to that value, when the marker
    sits on an object and not on the scalar. *per_segment* is true for a ref
    that is kept per segment. *level* limits the declaration to one config
    level; None is both.

    Validates at class creation time that *by* exists on *identity*, as
    ``Resolvable`` does.

    Usage: ``logical_id: Annotated[uuid.UUID | Literal[""],
    OwnsRef("deviceSettings:css:provider", NetworkServiceIdentity,
    by="logical_id", row="logicalId", per_segment=True)] = ""``
    """

    name: str
    identity: type
    _: KW_ONLY
    by: str
    row: str
    value: str | None = None
    per_segment: bool = False
    level: ConfigContext | None = None

    def __post_init__(self) -> None:
        """Raise ``TypeError`` when *by* is not a field of the *identity* dataclass."""
        if dataclasses.is_dataclass(self.identity):
            field_names = {f.name for f in dataclasses.fields(self.identity)}
            if self.by not in field_names:
                raise TypeError(
                    f"{self.identity.__name__} has no field '{self.by}'. "
                    f"Available: {', '.join(sorted(field_names))}"
                )


@dataclass(frozen=True, slots=True)
class Provides:
    """Annotated marker: the items of this list (or map) are the things a set of names refers to.

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
    """Annotated marker: this scalar field holds a value that a ``Provides`` of the same name lists.

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
    """What a ``RefOnly`` field takes from validated input: the input is not read, except from a patch."""
    return value if _in_patch(info) else MISSING


@dataclass(frozen=True, slots=True)
class RefOnly:
    """Annotated marker: this field stands for a ref row and is never part of the wire data.

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
    """What makes a field of one layer (the kind ``EdgeOnly`` or ``ProfileOnly``) absent at the other level, as the bare markers do; None for any other field."""
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
    """The types *tp* allows, apart from ``None`` and ``MISSING``: ``Annotated``, ``type`` aliases and unions taken apart, each ``Literal`` value an atom."""
    while get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    if isinstance(tp, TypeAliasType):
        return _type_atoms(tp.__value__)
    if get_origin(tp) is Literal:
        return frozenset(_LiteralValue(v) for v in get_args(tp) if v is not None)
    if get_origin(tp) in (Union, UnionType):  # pyright: ignore[reportDeprecated]
        return frozenset().union(*(_type_atoms(m) for m in get_args(tp)))
    return frozenset() if tp is MISSING or tp is type(None) else frozenset({tp})


def _allows_none(tp: object) -> bool:
    """Whether *tp* allows ``None``: ``Annotated``, ``type`` aliases and unions taken apart. ``MISSING`` does not count."""
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


def _check_resolvable(
    cls: type[BaseModel],
    field_name: str,
    fi: FieldInfo,
    marker: Resolvable[Any],
) -> None:
    """Refuse a field that cannot hold what its ``Resolvable`` accessor returns.

    Every type the identity's attribute allows apart from ``None`` must be one
    the field's ID type allows apart from ``None`` and ``MISSING`` (the type held
    inside a field kind, ``Annotated`` and ``type`` aliases taken apart); a
    ``Literal`` attribute fits a ``Literal`` field that holds all its values.
    An attribute that allows ``None`` needs a field that allows it too (a field
    that holds only ``MISSING`` besides its type does not). A
    field that cannot is refused here because it would otherwise fail only
    when a name resolved to a value it refuses.
    """
    from .layers import field_kind  # noqa: PLC0415 (layers imports this module)

    identity = marker.identity_type
    hints = get_type_hints(identity)
    wanted = _type_atoms(hints[marker.attribute])
    held = _type_atoms(field_kind(fi).inner_type)
    gives_none = _allows_none(hints[marker.attribute])
    holds_none = _allows_none(field_kind(fi).inner_type)
    if wanted <= held and (holds_none or not gives_none):
        return
    fitting = [
        name
        for name in _identity_field_names(identity)
        if name in hints
        and (atoms := _type_atoms(hints[name]))
        and atoms <= held
        and (holds_none or not _allows_none(hints[name]))
    ]
    advice = (
        "Read a field that fits instead: "
        + " or ".join(
            f"Resolvable({identity.__name__}, lambda i: i.{n})" for n in fitting
        )
        if fitting
        else f"No field of {identity.__name__} fits this field's type: change the field to hold what the identity gives, or read another identity."
    )
    raise TypeError(
        f"{cls.__name__}.{field_name} holds {_atoms_text(held)}{' or None' if holds_none else ''}, but its Resolvable reads "
        f"{identity.__name__}.{marker.attribute}, which can be {_atoms_text(wanted)}"
        f"{' or None' if gives_none else ''}. "
        "A name resolved through it would give a value the field refuses, and that "
        f"would show only when a name is merged in. {advice}"
    )


def apply_vco_markers(cls: type[BaseModel]) -> None:
    """Scan FieldInfo.metadata for VCO markers and store processed results back in metadata."""
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
            elif (
                m is EdgeOnly or (isinstance(m, type) and issubclass(m, EdgeOnly))
            ) and _ProcessedCtxDefaults not in already:
                fi.metadata.append(
                    _ProcessedCtxDefaults(ContextDefaults(profile=MISSING))
                )
                already.add(_ProcessedCtxDefaults)
            elif (
                m is ProfileOnly or (isinstance(m, type) and issubclass(m, ProfileOnly))
            ) and _ProcessedCtxDefaults not in already:
                fi.metadata.append(_ProcessedCtxDefaults(ContextDefaults(edge=MISSING)))
                already.add(_ProcessedCtxDefaults)
            elif isinstance(m, Normalizer) and _ProcessedNorm not in already:
                fi.metadata.append(_ProcessedNorm(_wrap_normalizer(m.fn)))
                already.add(_ProcessedNorm)
            elif isinstance(m, ContextDefault) and _ProcessedCtxDefaults not in already:
                fi.metadata.append(
                    _ProcessedCtxDefaults(
                        ContextDefaults(edge=m.edge, profile=m.profile)
                    )
                )
                already.add(_ProcessedCtxDefaults)
            elif isinstance(m, KeyField) and KeySpec not in already:
                fi.metadata.append(KeySpec(m.key_paths, alt=m.alt, label=m.label))
                already.add(KeySpec)
            elif (
                isinstance(m, OverrideFlag)
                or (isinstance(m, type) and issubclass(m, OverrideFlag))
            ) and _ProcessedOverrideFlag not in already:
                fi.metadata.append(_ProcessedOverrideFlag())
                already.add(_ProcessedOverrideFlag)
                if _ProcessedCtxDefaults not in already:
                    fi.metadata.append(
                        _ProcessedCtxDefaults(ContextDefaults(profile=MISSING))
                    )
                    already.add(_ProcessedCtxDefaults)
                fi.metadata.append(Lenient(_none_to_false))
            elif (
                isinstance(m, EdgeLocal)
                or (isinstance(m, type) and issubclass(m, EdgeLocal))
            ) and _ProcessedEdgeLocal not in already:
                fi.metadata.append(_ProcessedEdgeLocal())
                already.add(_ProcessedEdgeLocal)
            elif isinstance(m, Resolvable) and _ProcessedResolvable not in already:
                marker = cast("Resolvable[Any]", m)
                _check_resolvable(cls, field_name, fi, marker)
                fi.metadata.append(
                    _ProcessedResolvable(
                        identity_type=marker.identity_type,
                        accessor=marker.accessor,
                        attribute=marker.attribute,
                    )
                )
                already.add(_ProcessedResolvable)
    if needs_rebuild:
        cls.model_rebuild(force=True)
