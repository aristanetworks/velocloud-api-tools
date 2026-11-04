"""Patch schema generation for the aspect system.

Generates JSON Schema from VcoModel subtrees with transformations:
- All properties made optional
- KeyField arrays → additionalProperties objects
- OverrideFlag fields removed
- Resolvable fields accept strings alongside IDs
"""

import copy
import types
from typing import Any, get_args, get_origin

from pydantic import MISSING, BaseModel
from pyvelo.core.base import VcoModel
from pyvelo.core.fields import get_key_field, get_resolvable, has_override_flag


def resolve_model_at_path(
    model_type: type[VcoModel], path: str | None
) -> type[BaseModel]:
    """Navigate a dotted path on a model class to find the sub-model type.

    Returns the model type at the end of the path. If path is None or empty,
    returns the input model_type.
    """
    if not path:
        return model_type

    current_type: type = model_type
    for part in path.split("."):
        if not hasattr(current_type, "model_fields"):
            raise ValueError(
                f"Cannot navigate into {current_type.__name__}: not a model"
            )
        fields = current_type.model_fields
        if part not in fields:
            raise ValueError(
                f"{current_type.__name__} has no field {part!r}. Available: {', '.join(sorted(fields))}"
            )
        fi = fields[part]
        annotation = fi.annotation
        # Unwrap Optional / Union with MISSING
        current_type = _unwrap_annotation(annotation)

    return current_type


def _unwrap_annotation(annotation: Any) -> Any:
    """Unwrap Union, Optional, and list types to find the core model type."""
    # Python 3.15 uses this origin for both typing.Union and PEP 604 unions.
    origin = get_origin(annotation)
    if origin is types.UnionType:
        for arg in get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
            # Recurse into list[T]
            origin = getattr(arg, "__origin__", None)
            if origin is list:
                inner = getattr(arg, "__args__", (None,))[0]
                if (
                    inner is not None
                    and isinstance(inner, type)
                    and issubclass(inner, BaseModel)
                ):
                    return inner
        # If no BaseModel found, return first non-MISSING arg
        for arg in get_args(annotation):
            if (
                arg is not MISSING
                and arg is not type(MISSING)
                and arg is not type(None)
            ):
                return arg

    # Handle list[T]
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", None)
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return args[0]

    if isinstance(annotation, type):
        return annotation

    return annotation


def _make_all_optional(schema: dict[str, Any]) -> None:
    """Remove 'required' from all object schemas, making all properties optional."""
    schema.pop("required", None)
    for defn in schema.get("$defs", {}).values():
        defn.pop("required", None)


def _get_keyfield_fields(model_type: type) -> set[str]:
    """Get field names that have KeyField markers."""
    result: set[str] = set()
    if not hasattr(model_type, "model_fields"):
        return result
    for name, fi in model_type.model_fields.items():
        if get_key_field(fi) is not None:
            result.add(name)
    return result


def _get_override_fields(model_type: type) -> set[str]:
    """Get field names that have OverrideFlag markers."""
    result: set[str] = set()
    if not hasattr(model_type, "model_fields"):
        return result
    for name, fi in model_type.model_fields.items():
        if has_override_flag(fi):
            result.add(name)
    return result


def _get_resolvable_fields(model_type: type) -> set[str]:
    """Get field names that have Resolvable markers."""
    result: set[str] = set()
    if not hasattr(model_type, "model_fields"):
        return result
    for name, fi in model_type.model_fields.items():
        if get_resolvable(fi) is not None:
            result.add(name)
    return result


def _field_alias(model_type: type, field_name: str) -> str:
    """Get the JSON schema property name for a field (alias or camelCase)."""
    from pydantic.alias_generators import to_camel

    if not hasattr(model_type, "model_fields"):
        return to_camel(field_name)
    fi = model_type.model_fields[field_name]
    if fi.serialization_alias:
        return fi.serialization_alias
    if fi.alias:
        return fi.alias
    return to_camel(field_name)


def _transform_keyfield_arrays(schema: dict[str, Any], model_type: type) -> None:
    """Transform KeyField list fields from array schemas to object schemas.

    Changes: {"type": "array", "items": {...}} → {"type": "object", "additionalProperties": {...}}
    """
    keyfield_names = _get_keyfield_fields(model_type)
    if not keyfield_names:
        return

    properties = schema.get("properties", {})
    for field_name in keyfield_names:
        alias = _field_alias(model_type, field_name)
        if alias not in properties:
            continue
        prop = properties[alias]
        if prop.get("type") == "array" and "items" in prop:
            items = prop.pop("items")
            prop["type"] = "object"
            prop["additionalProperties"] = items


def _remove_override_fields(schema: dict[str, Any], model_type: type) -> None:
    """Remove OverrideFlag fields from the schema properties."""
    override_names = _get_override_fields(model_type)
    if not override_names:
        return

    properties = schema.get("properties", {})
    for field_name in override_names:
        alias = _field_alias(model_type, field_name)
        properties.pop(alias, None)


def _add_string_to_resolvable(schema: dict[str, Any], model_type: type) -> None:
    """For Resolvable fields, allow string values alongside the original type."""
    resolvable_names = _get_resolvable_fields(model_type)
    if not resolvable_names:
        return

    properties = schema.get("properties", {})
    for field_name in resolvable_names:
        alias = _field_alias(model_type, field_name)
        if alias not in properties:
            continue
        prop = properties[alias]
        # Wrap in anyOf with string alternative if not already
        if "anyOf" in prop:
            # Check if string is already an option
            has_string = any(s.get("type") == "string" for s in prop["anyOf"])
            if not has_string:
                prop["anyOf"].append({"type": "string"})
        elif prop.get("type") == "string":
            pass  # Already accepts strings
        else:
            # Wrap current schema in anyOf with string
            original = {
                k: v for k, v in prop.items() if k != "title" and k != "default"
            }
            title = prop.get("title")
            default = prop.get("default")
            prop.clear()
            prop["anyOf"] = [original, {"type": "string"}]
            if title:
                prop["title"] = title
            if default is not None:
                prop["default"] = default


def _transform_nested_models(schema: dict[str, Any], model_type: type) -> None:
    """Apply transforms to nested model schemas in $defs."""
    defs = schema.get("$defs", {})
    if not defs or not hasattr(model_type, "model_fields"):
        return

    # Build a map from $def name to model type
    def_model_map: dict[str, type] = {}
    _collect_nested_models(model_type, def_model_map, visited=set())

    for def_name, def_schema in defs.items():
        nested_type = def_model_map.get(def_name)
        if nested_type is None:
            continue
        _transform_keyfield_arrays(def_schema, nested_type)
        _remove_override_fields(def_schema, nested_type)
        _add_string_to_resolvable(def_schema, nested_type)


def _collect_nested_models(
    model_type: type,
    result: dict[str, type],
    visited: set[type],
) -> None:
    """Recursively collect model_name -> model_type for all nested VcoModels."""
    if model_type in visited or not hasattr(model_type, "model_fields"):
        return
    visited.add(model_type)

    result[model_type.__name__] = model_type

    for fi in model_type.model_fields.values():
        inner = _unwrap_annotation(fi.annotation)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            _collect_nested_models(inner, result, visited)


def generate_patch_schema(
    model_type: type[VcoModel], path: str | None = None
) -> dict[str, Any]:
    """Generate a JSON Schema for patch input from a model subtree.

    Transforms applied:
    - All properties optional
    - KeyField arrays → additionalProperties objects
    - OverrideFlag fields removed
    - Resolvable fields accept strings alongside IDs
    """
    target_type = resolve_model_at_path(model_type, path)

    if not hasattr(target_type, "model_json_schema"):
        raise TypeError(f"Cannot generate schema from {target_type}")

    schema = copy.deepcopy(target_type.model_json_schema())

    # Apply transforms
    _make_all_optional(schema)
    _transform_keyfield_arrays(schema, target_type)
    _remove_override_fields(schema, target_type)
    _add_string_to_resolvable(schema, target_type)
    _transform_nested_models(schema, target_type)

    return schema
