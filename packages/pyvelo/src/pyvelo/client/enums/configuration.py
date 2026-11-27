"""The states, types, scopes and module names of a VCO configuration.

``ConfigurationModuleName`` names the modules of a profile or an Edge's configuration,
such as ``deviceSettings``, ``QOS`` and ``firewall``; ``python_module_name`` gives each
the name pyvelo prints it by, such as ``device_settings`` and ``business_policy``.
"""

from enum import StrEnum
from typing import Final


class ConfigurationBastionState(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    STAGE_REQUESTED = "STAGE_REQUESTED"
    UNSTAGE_REQUESTED = "UNSTAGE_REQUESTED"
    STAGED = "STAGED"
    UNSTAGED = "UNSTAGED"


class ConfigurationModuleType(StrEnum):
    ENTERPRISE = "ENTERPRISE"
    OPERATOR = "OPERATOR"
    GATEWAY = "GATEWAY"


# imageUpdate,controlPlane,managementPlane,firewall,QOS,deviceSettings,WAN,metaData,
# properties,analyticsSettings,atpMetadata
class ConfigurationModuleName(StrEnum):
    META_DATA = "metaData"
    PROPERTIES = "properties"
    ANALYTICS_SETTINGS = "analyticsSettings"
    IMAGE_UPDATE = "imageUpdate"
    CONTROL_PLANE = "controlPlane"
    MANAGEMENT_PLANE = "managementPlane"
    DEVICE_SETTINGS = "deviceSettings"
    WAN = "WAN"
    QOS = "QOS"
    FIREWALL = "firewall"
    ATP_METADATA = "atpMetadata"


PYTHON_MODULE_NAMES: Final[dict[ConfigurationModuleName, str]] = {
    ConfigurationModuleName.META_DATA: "meta_data",
    ConfigurationModuleName.PROPERTIES: "properties",
    ConfigurationModuleName.ANALYTICS_SETTINGS: "analytics_settings",
    ConfigurationModuleName.IMAGE_UPDATE: "image_update",
    ConfigurationModuleName.CONTROL_PLANE: "control_plane",
    ConfigurationModuleName.MANAGEMENT_PLANE: "management_plane",
    ConfigurationModuleName.DEVICE_SETTINGS: "device_settings",
    ConfigurationModuleName.WAN: "wan",
    ConfigurationModuleName.QOS: "business_policy",
    ConfigurationModuleName.FIREWALL: "firewall",
    ConfigurationModuleName.ATP_METADATA: "atp_metadata",
}
"""Each module's python name: the name pyvelo prints it by, which input takes too."""


def python_module_name(name: str) -> str:
    """The python name of the module the VCO calls ``name``.

    ``QOS`` is ``business_policy`` and ``deviceSettings`` is ``device_settings``. Any
    other text, a python name included, is returned as it is, so a name converted
    twice is the same name.
    """
    try:
        return PYTHON_MODULE_NAMES[ConfigurationModuleName(name)]
    except ValueError:
        return name


class ConfigurationType(StrEnum):
    SEGMENT_BASED = "SEGMENT_BASED"
    NETWORK_BASED = "NETWORK_BASED"


class ConfigurationScope(StrEnum):
    ENTERPRISE = "ENTERPRISE"
