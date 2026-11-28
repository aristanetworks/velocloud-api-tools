"""Built-in structural validators.

These are override_consistency, key_uniqueness, required_fields.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

from pydantic import MISSING, BaseModel

from pyvelo.client.enums.configuration import python_module_name
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config.validation import (
    ConfigStateLike,
    ValidationError,
    ValidationResult,
    validator,
)
from pyvelo.core import Kind, VcoModel, field_kind, named_path, unwrap
from pyvelo.core.fields import KeySpec, get_key_spec
from pyvelo.core.rebase import (
    KeyForm,
    Path,
    PathPart,
    item_key,
    item_key_text,
    key_form,
)


def _get_target_config(
    state: ConfigStateLike,
) -> EdgeConfiguration | EnterpriseConfigurationProfile:
    """Get the target config (edge or profile)."""
    if state.target_type == "edge" and state.edge_config is not None:
        return state.edge_config
    return state.profile_config


def _walk_model_for_override(
    model: BaseModel,
    path: str,
    errors: list[ValidationError],
) -> None:
    """Recursively walk nested models for future override-consistency checks.

    This traversal is currently a placeholder; it does not inspect override
    flags or append validation messages.
    """
    # Recurse into sub-models
    for field_name in type(model).model_fields:
        val = unwrap(getattr(model, field_name))
        if isinstance(val, BaseModel):
            _walk_model_for_override(val, f"{path}.{field_name}", errors)
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, BaseModel):
                    _walk_model_for_override(item, f"{path}.{field_name}[{i}]", errors)


@validator(modules=[], phases=["write"])
def override_consistency(state: ConfigStateLike) -> ValidationResult:
    """Placeholder for override-consistency validation; currently reports none."""
    result = ValidationResult()
    config = _get_target_config(state)

    if config.modules is MISSING:
        return result

    for mod in config.modules:
        if isinstance(mod.data, BaseModel):
            _walk_model_for_override(mod.data, mod.name, result.warnings)

    return result


@dataclass(frozen=True, slots=True)
class _Module:
    """The module a walk is in: its name as the VCO has it, and its data."""

    name: str
    data: VcoModel


type _Parts = tuple[PathPart, ...]
"""A place in a module's data: attribute names and list positions."""


def _finding(
    module: _Module, parts: _Parts, message: str, code: str
) -> ValidationError:
    """A finding at *parts* of *module*, named as pyvelo shows a place."""
    return ValidationError(
        path=named_path(Path(parts), module.data),
        message=message,
        code=code,
        module=python_module_name(module.name),
    )


def _duplicate_keys(
    items: list[object], spec: KeySpec, module: _Module, parts: _Parts
) -> Iterator[ValidationError]:
    """Report each item of a KeyField list whose key an earlier item has.

    *parts* is the list's place in *module*. Keys compare in string form, part
    by part (24 and "24" are one).
    """
    seen_keys: dict[KeyForm, int] = {}
    for i, item in enumerate(items):
        form = key_form(item_key(item, spec.paths))
        if form is not None:
            if form in seen_keys:
                key_str = item_key_text(item, spec.paths)
                yield _finding(
                    module,
                    (*parts, i),
                    f"Duplicate key {key_str!r} (first at index {seen_keys[form]})",
                    "key_uniqueness",
                )
            else:
                seen_keys[form] = i


def _models_below(value: object, parts: _Parts) -> Iterator[tuple[BaseModel, _Parts]]:
    """The models a field's value holds, each with its place.

    That is the value itself if it is a model, or the items of a list that are.
    """
    if isinstance(value, BaseModel):
        yield value, parts
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, BaseModel):
                yield item, (*parts, i)


def _walk_model_for_keys(
    model: BaseModel,
    module: _Module,
    parts: _Parts,
    errors: list[ValidationError],
) -> None:
    """Check that KeyField lists have unique keys (every part, for a composite key)."""
    for field_name, field_info in type(model).model_fields.items():
        spec = get_key_spec(field_info)
        val = unwrap(getattr(model, field_name))
        field_parts = (*parts, field_name)

        if spec is not None and isinstance(val, list):
            errors.extend(
                _duplicate_keys(cast("list[object]", val), spec, module, field_parts)
            )

        for child, child_parts in _models_below(val, field_parts):
            _walk_model_for_keys(child, module, child_parts, errors)


@validator(modules=[], phases=["read", "write"])
def key_uniqueness(state: ConfigStateLike) -> ValidationResult:
    """Verify KeyField items have unique keys."""
    result = ValidationResult()
    config = _get_target_config(state)

    if config.modules is MISSING:
        return result

    for mod in config.modules:
        if isinstance(mod.data, VcoModel):
            module = _Module(mod.name, mod.data)
            _walk_model_for_keys(mod.data, module, (), result.errors)

    return result


def _walk_model_for_required(
    model: BaseModel,
    module: _Module,
    parts: _Parts,
    errors: list[ValidationError],
) -> None:
    """Check that required fields (no default) have values."""
    for field_name, field_info in type(model).model_fields.items():
        val = unwrap(getattr(model, field_name))
        field_parts = (*parts, field_name)

        # An inheritable field is never required: the edge stores nothing where
        # it inherits, and the profile reads its display default.
        if (
            val is MISSING
            and field_info.is_required()
            and field_kind(field_info).kind is not Kind.INHERITABLE
        ):
            errors.append(
                _finding(
                    module, field_parts, "Required field is MISSING", "required_fields"
                )
            )
        else:
            for child, child_parts in _models_below(val, field_parts):
                _walk_model_for_required(child, module, child_parts, errors)


@validator(modules=[], phases=["write"])
def required_fields(state: ConfigStateLike) -> ValidationResult:
    """Verify that required (template-filled) fields have valid values."""
    result = ValidationResult()
    config = _get_target_config(state)

    if config.modules is MISSING:
        return result

    for mod in config.modules:
        if isinstance(mod.data, VcoModel):
            module = _Module(mod.name, mod.data)
            _walk_model_for_required(mod.data, module, (), result.errors)

    return result
