"""Field declaration toolkit for VcoModel.

Provides:
- Constants and sentinels (CONTEXT_NOT_SET)
- ConfigContext enum and ContextDefaults dataclass
- Normalizer helpers for compact serialization equivalence
- Annotated validators (NoneIsFalse, NoneIsTrue, NoneIsZero, NoneIsMissing, CoerceInt)
- DefaultingModel for fallback-to-default validation
- Composable Annotated markers (Alias, EdgeOnly, ProfileOnly, Normalizer, ContextDefault)
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import UnionType
from typing import Any, cast

from pydantic import (
    MISSING,
    BaseModel,
    BeforeValidator,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
)
from pydantic.fields import FieldInfo

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


def get_key_field(field_info: FieldInfo) -> str | None:
    """Extract KeyField key_path from FieldInfo.metadata, or None."""
    for m in field_info.metadata:
        if isinstance(m, _ProcessedKeyField):
            return m.key_path
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

NoneIsMissing = BeforeValidator(_none_to_missing)
NoneIsFalse = BeforeValidator(_none_to_false)
NoneIsTrue = BeforeValidator(_none_to_true)
NoneIsZero = BeforeValidator(_none_to_zero)


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
    """Annotated marker: treat a list field as dict-like, keyed by a sub-field path.

    Usage: segments: Annotated[list[Segment], KeyField("segment.name")] = ...

    The merge engine will address list items by the value at the key path
    instead of by index.
    """

    __slots__ = ("key_path",)

    def __init__(self, key_path: str):
        self.key_path = key_path


@dataclass(frozen=True, slots=True)
class _ProcessedKeyField:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""

    key_path: str


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
        _ProcessedKeyField,
        _ProcessedOverrideFlag,
        _ProcessedEdgeLocal,
        _ProcessedResolvable,
    )
    for fi in cls.model_fields.values():
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
            elif isinstance(m, KeyField) and _ProcessedKeyField not in already:
                fi.metadata.append(_ProcessedKeyField(key_path=m.key_path))
                already.add(_ProcessedKeyField)
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
                fi.metadata.append(
                    BeforeValidator(lambda v, _info: False if v is None else v)
                )
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
