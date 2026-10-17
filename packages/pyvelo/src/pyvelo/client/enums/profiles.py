from enum import StrEnum


class GetEnterpriseConfigurationsWith(StrEnum):
    EDGES = "edges"
    MODULES = "modules"
    EDGE_COUNT = "edgeCount"
    REFS = "refs"
    DEVICE_SETTINGS = "deviceSettings"


class GetConfigurationWith(StrEnum):
    MODULES = "modules"
    EDGE_COUNT = "edgeCount"
