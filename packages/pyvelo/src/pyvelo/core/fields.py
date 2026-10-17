"""Field declaration toolkit for VcoModel.

Provides:
- Constants and sentinels (_CONTEXT_NOT_SET)
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
    BeforeValidator,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
)
from pydantic.fields import FieldInfo
from pydantic_core import MISSING

_CONTEXT_NOT_SET = object()  # sentinel for "use field's own default"


class ConfigContext(StrEnum):
    EDGE = "edge"
    PROFILE = "profile"


@dataclass(frozen=True)
class ContextDefaults:
    edge: Any = _CONTEXT_NOT_SET
    profile: Any = _CONTEXT_NOT_SET

    def for_level(self, level: ConfigContext) -> Any:
        return self.edge if level is ConfigContext.EDGE else self.profile


def _resolve_ctx_default(ctx_default: Any) -> Any:
    """Invoke callable context defaults (factories like list, dict) to produce values."""
    if ctx_default is _CONTEXT_NOT_SET or ctx_default is MISSING:
        return ctx_default
    if callable(ctx_default):
        return ctx_default()
    return ctx_default


def _wrap_normalizer(
    fn: Callable[..., Any],
) -> Callable[[Any, ConfigContext | None], Any]:
    """Wrap a 1-arg normalizer into the canonical 2-arg (value, ctx) form.

    Inspects the callable's parameter count at registration time so there is
    no runtime overhead per call.
    """
    try:
        sig = inspect.signature(fn)
        nparams = len(sig.parameters)
    except ValueError, TypeError:
        nparams = 1
    if nparams >= 2:
        return fn
    return lambda v, ctx: fn(v)


@dataclass(frozen=True, slots=True)
class _ProcessedNorm:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""

    fn: Callable[[Any, ConfigContext | None], Any]


@dataclass(frozen=True, slots=True)
class _ProcessedCtxDefaults:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""

    defaults: ContextDefaults


def _get_normalizer(
    field_info: FieldInfo,
) -> Callable[[Any, ConfigContext | None], Any] | None:
    """Extract processed normalizer from FieldInfo.metadata."""
    for m in field_info.metadata:
        if isinstance(m, _ProcessedNorm):
            return m.fn
    return None


# Public alias
get_normalizer = _get_normalizer


def _get_context_defaults(field_info: FieldInfo) -> ContextDefaults | None:
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

    Usage: duplex: Annotated[PortDuplex, DefaultingScalar(PortDuplex, default="FULL")] = "FULL"
    """
    adapter = TypeAdapter(model_type)

    def _validator(v: Any, info: ValidationInfo) -> Any:
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

    def __init__(
        self, *, edge: Any = _CONTEXT_NOT_SET, profile: Any = _CONTEXT_NOT_SET
    ):
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
class _ProcessedResolvable:
    """Stored in FieldInfo.metadata by _apply_vco_markers."""

    identity_type: type
    id_attr: str


def _apply_vco_markers(cls) -> None:
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
