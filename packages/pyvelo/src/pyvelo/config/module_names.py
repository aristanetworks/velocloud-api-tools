"""Normalize user-facing configuration module names."""

from pyvelo.client.enums.configuration import (
    PYTHON_MODULE_NAMES,
    ConfigurationModuleName,
)

_MODULE_NAME_MAP: dict[str, ConfigurationModuleName] = {
    python_name: module for module, python_name in PYTHON_MODULE_NAMES.items()
}


def resolve_module_name(name: str | ConfigurationModuleName) -> ConfigurationModuleName:
    """Map a module's python name, or the VCO's own name of it, to the module."""
    if isinstance(name, ConfigurationModuleName):
        return name

    try:
        return ConfigurationModuleName(name)
    except ValueError:
        pass

    resolved = _MODULE_NAME_MAP.get(name)
    if resolved is None:
        available = ", ".join(sorted(_MODULE_NAME_MAP))
        raise ValueError(
            f"Unknown module: {name!r}. Give a module by its name: {available} "
            "(the VCO's own name, such as QOS or deviceSettings, is accepted too)"
        )
    return resolved
