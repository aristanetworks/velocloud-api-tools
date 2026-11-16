"""Normalize user-facing configuration module names."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

_MODULE_NAME_MAP: dict[str, ConfigurationModuleName] = {
    "device_settings": ConfigurationModuleName.DEVICE_SETTINGS,
    "deviceSettings": ConfigurationModuleName.DEVICE_SETTINGS,
    "firewall": ConfigurationModuleName.FIREWALL,
    "qos": ConfigurationModuleName.QOS,
    "QOS": ConfigurationModuleName.QOS,
    "wan": ConfigurationModuleName.WAN,
    "WAN": ConfigurationModuleName.WAN,
    "control_plane": ConfigurationModuleName.CONTROL_PLANE,
    "controlPlane": ConfigurationModuleName.CONTROL_PLANE,
}


def resolve_module_name(name: str | ConfigurationModuleName) -> ConfigurationModuleName:
    """Map a user-facing name to its internal VCO module name."""
    if isinstance(name, ConfigurationModuleName):
        return name

    try:
        return ConfigurationModuleName(name)
    except ValueError:
        pass

    resolved = _MODULE_NAME_MAP.get(name)
    if resolved is None:
        available = ", ".join(
            sorted({value.value for value in _MODULE_NAME_MAP.values()})
        )
        raise ValueError(f"Unknown module: {name!r}. Available: {available}")
    return resolved
