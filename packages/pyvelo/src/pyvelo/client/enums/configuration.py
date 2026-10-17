from enum import StrEnum


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


# imageUpdate,controlPlane,managementPlane,firewall,QOS,deviceSettings,WAN,metaData,properties,analyticsSettings,atpMetadata
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


class ConfigurationType(StrEnum):
    SEGMENT_BASED = "SEGMENT_BASED"
    NETWORK_BASED = "NETWORK_BASED"


class ConfigurationScope(StrEnum):
    ENTERPRISE = "ENTERPRISE"
