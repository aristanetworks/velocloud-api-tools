"""Built-in structural validators: override_consistency, key_uniqueness, required_fields."""

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
from pyvelo.core.fields import get_key_field


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


def _walk_model_for_keys(
    model: BaseModel,
    path: str,
    errors: list[ValidationError],
) -> None:
    """Check that KeyField lists have unique keys."""
    for field_name, field_info in type(model).model_fields.items():
        key_path = get_key_field(field_info)
        val = getattr(model, field_name)

        if key_path is not None and isinstance(val, list):
            seen_keys: dict[str, int] = {}
            for i, item in enumerate(val):
                # Resolve the key value from the dotted path
                key_val = item
                for part in key_path.split("."):
                    key_val = getattr(key_val, part, None)
                    if key_val is None:
                        break

                if key_val is not None:
                    key_str = str(key_val)
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
