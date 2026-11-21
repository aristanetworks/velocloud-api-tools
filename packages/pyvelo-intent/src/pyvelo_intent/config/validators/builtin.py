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
from pyvelo.core.fields import get_key_spec
from pyvelo.core.rebase import KeyScalar, key_text


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
        val = getattr(model, field_name)
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


def _key_text(item: object, key_paths: tuple[str, ...]) -> str | None:
    """The key of *item* at *key_paths* in string form; None if a part is None or MISSING.

    A one-part key is its value in string form; a composite key is its parts
    joined by ``key_text``, so two keys are one when every part is.
    """
    parts: list[KeyScalar] = []
    for key_path in key_paths:
        val: object = item
        for part in key_path.split("."):
            val = cast(object, getattr(val, part, None))
            if val is None or val is MISSING:
                return None
        parts.append(val if isinstance(val, (str, int, float)) else str(val))
    if len(parts) == 1:
        return str(parts[0])
    return key_text(tuple(parts))


def _walk_model_for_keys(
    model: BaseModel,
    path: str,
    errors: list[ValidationError],
) -> None:
    """Check that KeyField lists have unique keys (every part, for a composite key)."""
    for field_name, field_info in type(model).model_fields.items():
        spec = get_key_spec(field_info)
        val = getattr(model, field_name)

        if spec is not None and isinstance(val, list):
            seen_keys: dict[str, int] = {}
            for i, item in enumerate(val):
                key_str = _key_text(item, spec.paths)
                if key_str is not None:
                    if key_str in seen_keys:
                        errors.append(
                            ValidationError(
                                path=f"{path}.{field_name}[{i}]",
                                message=f"Duplicate key {key_str!r} (first at index {seen_keys[key_str]})",
                                code="key_uniqueness",
                            )
                        )
                    else:
                        seen_keys[key_str] = i

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
        val = getattr(model, field_name)

        if val is MISSING and field_info.is_required():
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
