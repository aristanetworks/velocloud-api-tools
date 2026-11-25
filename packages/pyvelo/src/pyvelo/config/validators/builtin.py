"""Built-in structural validators: override_consistency, key_uniqueness, required_fields."""

from typing import cast

from pydantic import MISSING, BaseModel

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
from pyvelo.core import Kind, field_kind, unwrap
from pyvelo.core.fields import get_key_spec
from pyvelo.core.rebase import KeyForm, item_key, item_key_text, key_form


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


def _walk_model_for_keys(  # noqa: C901 (existing: split it when it next changes)
    model: BaseModel,
    path: str,
    errors: list[ValidationError],
) -> None:
    """Check that KeyField lists have unique keys (every part, for a composite key)."""
    for field_name, field_info in type(model).model_fields.items():
        spec = get_key_spec(field_info)
        val = unwrap(getattr(model, field_name))

        if spec is not None and isinstance(val, list):
            # Keys compare in string form, part by part (24 and "24" are one).
            seen_keys: dict[KeyForm, int] = {}
            for i, item in enumerate(cast("list[object]", val)):
                form = key_form(item_key(item, spec.paths))
                if form is not None:
                    if form in seen_keys:
                        key_str = item_key_text(item, spec.paths)
                        errors.append(
                            ValidationError(
                                path=f"{path}.{field_name}[{i}]",
                                message=f"Duplicate key {key_str!r} (first at index {seen_keys[form]})",
                                code="key_uniqueness",
                            )
                        )
                    else:
                        seen_keys[form] = i

            # Recurse into list items
            for i, item in enumerate(val):
                if isinstance(item, BaseModel):
                    _walk_model_for_keys(item, f"{path}.{field_name}[{i}]", errors)
        elif isinstance(val, BaseModel):
            _walk_model_for_keys(val, f"{path}.{field_name}", errors)
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, BaseModel):
                    _walk_model_for_keys(item, f"{path}.{field_name}[{i}]", errors)


@validator(modules=[], phases=["read", "write"])
def key_uniqueness(state: ConfigStateLike) -> ValidationResult:
    """Verify KeyField items have unique keys."""
    result = ValidationResult()
    config = _get_target_config(state)

    if config.modules is MISSING:
        return result

    for mod in config.modules:
        if isinstance(mod.data, BaseModel):
            _walk_model_for_keys(mod.data, mod.name, result.errors)

    return result


def _walk_model_for_required(
    model: BaseModel,
    path: str,
    errors: list[ValidationError],
) -> None:
    """Check that required fields (no default) have values."""
    for field_name, field_info in type(model).model_fields.items():
        val = unwrap(getattr(model, field_name))

        # An inheritable field is never required: the edge stores nothing where
        # it inherits, and the profile reads its display default.
        if (
            val is MISSING
            and field_info.is_required()
            and field_kind(field_info).kind is not Kind.INHERITABLE
        ):
            errors.append(
                ValidationError(
                    path=f"{path}.{field_name}",
                    message="Required field is MISSING",
                    code="required_fields",
                )
            )
        elif isinstance(val, BaseModel):
            _walk_model_for_required(val, f"{path}.{field_name}", errors)
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, BaseModel):
                    _walk_model_for_required(item, f"{path}.{field_name}[{i}]", errors)


@validator(modules=[], phases=["write"])
def required_fields(state: ConfigStateLike) -> ValidationResult:
    """Verify that required (template-filled) fields have valid values."""
    result = ValidationResult()
    config = _get_target_config(state)

    if config.modules is MISSING:
        return result

    for mod in config.modules:
        if isinstance(mod.data, BaseModel):
            _walk_model_for_required(mod.data, mod.name, result.errors)

    return result
