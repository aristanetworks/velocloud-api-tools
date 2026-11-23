"""Field declaration toolkit for VcoModel.

Provides:
- Constants and sentinels (CONTEXT_NOT_SET)
- ConfigContext enum and ContextDefaults dataclass
- Normalizer helpers for compact serialization equivalence
- Annotated validators (NoneIsFalse, NoneIsTrue, NoneIsZero, NoneIsMissing, CoerceInt)
- DefaultingModel for fallback-to-default validation
- Composable Annotated markers (Alias, EdgeOnly, ProfileOnly, Normalizer, ContextDefault,
  OwnsRef, RefOnly)
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from enum import StrEnum
from types import UnionType
from typing import Any, cast

from pydantic import (
    MISSING,
    BaseModel,
    BeforeValidator,
    GetCoreSchemaHandler,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
)
from pydantic.fields import FieldInfo
from pydantic_core import CoreSchema, core_schema

CONTEXT_NOT_SET: object = object()  # sentinel for "use field's own default"


class ConfigContext(StrEnum):
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


def get_normalizer(field_info: FieldInfo) -> NormalizerFn | None:
    """Extract processed normalizer from FieldInfo.metadata."""
    for m in field_info.metadata:
        if isinstance(m, _ProcessedNorm):
            return m.fn
    return None


def get_context_defaults(field_info: FieldInfo) -> ContextDefaults | None:
    """Extract processed context defaults from FieldInfo.metadata."""
    for m in field_info.metadata:
        if isinstance(m, _ProcessedCtxDefaults):
            return m.defaults
    return None


def get_key_spec(field_info: FieldInfo) -> KeySpec | None:
    """The ``KeySpec`` of a field marked ``KeyField``, or None."""
    for m in field_info.metadata:
        if isinstance(m, KeySpec):
            return m
    return None


def has_override_flag(field_info: FieldInfo) -> bool:
    """Check if a field is marked with OverrideFlag."""
    return any(isinstance(m, _ProcessedOverrideFlag) for m in field_info.metadata)


def is_edge_local(field_info: FieldInfo) -> bool:
    """Check if a field is marked with EdgeLocal."""
    return any(isinstance(m, _ProcessedEdgeLocal) for m in field_info.metadata)


def get_resolvable(field_info: FieldInfo) -> _ProcessedResolvable | None:
    """Extract Resolvable info from FieldInfo.metadata, or None."""
    for m in field_info.metadata:
        if isinstance(m, _ProcessedResolvable):
            return m
    return None


def is_positional(field_info: FieldInfo) -> bool:
    """Check if a field is marked with ``Positional``."""
    return any(isinstance(m, Positional) for m in field_info.metadata)


def get_name_of(field_info: FieldInfo) -> type | None:
    """The identity type a field is marked ``NameOf`` for, or None."""
    for m in field_info.metadata:
        if isinstance(m, NameOf):
            return m.identity_type
    return None


def is_server_owned(field_info: FieldInfo) -> bool:
    """Check if a field is marked with ``ServerOwned``."""
    return any(isinstance(m, ServerOwned) for m in field_info.metadata)


def is_secret(field_info: FieldInfo) -> bool:
    """Check if a field is marked with ``Secret``."""
    return any(isinstance(m, Secret) for m in field_info.metadata)


def get_owns_ref(field_info: FieldInfo) -> OwnsRef | None:
    """The ``OwnsRef`` a field is marked with, or None."""
    for m in field_info.metadata:
        if isinstance(m, OwnsRef):
            return m
    return None


def get_ref_only(field_info: FieldInfo) -> RefOnly | None:
    """The ``RefOnly`` a field is marked with, or None."""
    for m in field_info.metadata:
        if isinstance(m, RefOnly):
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


def _audit(info: ValidationInfo, v: Any, reason: str):
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
    adapter: TypeAdapter[Any] = TypeAdapter(cast(Any, model_type))

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

    __slots__ = ("key",)

    def __init__(self, key: str):
        self.key = key


class EdgeOnly:
    """Annotated marker: field absent at profile level."""


class ProfileOnly:
    """Annotated marker: field absent at edge level."""


class Normalizer:
    """Annotated marker: normalizer for compact serialization equivalence."""

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[..., Any]):
        self.fn = fn


class ContextDefault:
    """Annotated marker: context-specific defaults."""

    __slots__ = ("edge", "profile")

    def __init__(self, *, edge: Any = CONTEXT_NOT_SET, profile: Any = CONTEXT_NOT_SET):
        self.edge = edge
        self.profile = profile


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

    __slots__ = ("alt", "key_paths", "label")

    def __init__(
        self, *key_paths: str, alt: str | None = None, label: str | None = None
    ) -> None:
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


class Resolvable:
    """Annotated marker: field accepts a name, resolves to a specific ID.

    The merge engine detects when a string is provided where a newtype ID
    is expected and calls the registered resolver for identity_type,
    extracting the value at id_attr.

    Validates at class creation time that id_attr exists on identity_type.
    """

    __slots__ = ("id_attr", "identity_type")

    def __init__(self, identity_type: type, id_attr: str):
        # Validate id_attr exists on the identity dataclass
        import dataclasses

        if dataclasses.is_dataclass(identity_type):
            field_names = {f.name for f in dataclasses.fields(identity_type)}
            if id_attr not in field_names:
                raise TypeError(
                    f"{identity_type.__name__} has no field '{id_attr}'. "
                    f"Available: {', '.join(sorted(field_names))}"
                )
        self.identity_type = identity_type
        self.id_attr = id_attr


@dataclass(frozen=True, slots=True)
class NameOf:
    """Annotated marker: this string field holds the name of an identity.

    ``merge_patch`` fills the sibling ``Resolvable(identity_type, ...)`` fields
    that a new item's patch leaves out from this field's value. No other
    string in a patch is tried as a name.

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

    A row of ref name *name* goes with the identity (spec 5.15). *identity* is
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
        import dataclasses

        if dataclasses.is_dataclass(self.identity):
            field_names = {f.name for f in dataclasses.fields(self.identity)}
            if self.by not in field_names:
                raise TypeError(
                    f"{self.identity.__name__} has no field '{self.by}'. "
                    + f"Available: {', '.join(sorted(field_names))}"
                )


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
    id_attr: str


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
    for fi in cls.model_fields.values():
        if not fi.exclude and get_ref_only(fi) is not None:
            fi.exclude = True  # a dump leaves it out
            needs_rebuild = True
        already = {type(m) for m in fi.metadata if isinstance(m, _processed_types)}
        for m in fi.metadata:
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
                fi.metadata.append(
                    _ProcessedResolvable(
                        identity_type=m.identity_type, id_attr=m.id_attr
                    )
                )
                already.add(_ProcessedResolvable)
    if needs_rebuild:
        cls.model_rebuild(force=True)
