from enum import StrEnum
from typing import TypedDict

from pyvelo.core.enums import ModelNumber

__all__ = ["EdgeField", "GetEdgesWith", "GetEnterpriseEdgeListWith", "ModelNumber"]


class EdgeField(StrEnum):
    ID = "id"
    CREATED = "created"
    ENTERPRISE_ID = "enterpriseId"
    ENTERPRISE_LOGICAL_ID = "enterpriseLogicalId"
    SITE_ID = "siteId"
    ACTIVATION_KEY = "activationKey"
    ACTIVATION_KEY_EXPIRES = "activationKeyExpires"
    ACTIVATION_STATE = "activationState"
    ACTIVATION_TIME = "activationTime"
    SOFTWARE_VERSION = "softwareVersion"
    BUILD_NUMBER = "buildNumber"
    FACTORY_SOFTWARE_VERSION = "factorySoftwareVersion"
    FACTORY_BUILD_NUMBER = "factoryBuildNumber"
    PLATFORM_FIRMWARE_VERSION = "platformFirmwareVersion"
    PLATFORM_BUILD_NUMBER = "platformBuildNumber"
    MODEM_FIRMWARE_VERSION = "modemFirmwareVersion"
    MODEM_BUILD_NUMBER = "modemBuildNumber"
    SOFTWARE_UPDATED = "softwareUpdated"
    SELF_MAC_ADDRESS = "selfMacAddress"
    DEVICE_ID = "deviceId"
    LOGICAL_ID = "logicalId"
    SERIAL_NUMBER = "serialNumber"
    MODEL_NUMBER = "modelNumber"
    DEVICE_FAMILY = "deviceFamily"
    LTE_REGION = "lteRegion"
    NAME = "name"
    DNS_NAME = "dnsName"
    DESCRIPTION = "description"
    ALERTS_ENABLED = "alertsEnabled"
    OPERATOR_ALERTS_ENABLED = "operatorAlertsEnabled"
    EDGE_STATE = "edgeState"
    EDGE_STATE_TIME = "edgeStateTime"
    IS_LIVE = "isLive"
    SYSTEM_UP_SINCE = "systemUpSince"
    SERVICE_UP_SINCE = "serviceUpSince"
    LAST_CONTACT = "lastContact"
    SERVICE_STATE = "serviceState"
    ENDPOINT_PKI_MODE = "endpointPkiMode"
    HA_STATE = "haState"
    HA_PREVIOUS_STATE = "haPreviousState"
    HA_LAST_CONTACT = "haLastContact"
    HA_SERIAL_NUMBER = "haSerialNumber"
    BASTION_STATE = "bastionState"
    MODIFIED = "modified"
    CUSTOM_INFO = "customInfo"
    HA_MODE = "haMode"
    STANDBY_SYSTEM_UP_SINCE = "standbySystemUpSince"
    STANDBY_SERVICE_UP_SINCE = "standbyServiceUpSince"
    STANDBY_SOFTWARE_VERSION = "standbySoftwareVersion"
    STANDBY_FACTORY_SOFTWARE_VERSION = "standbyFactorySoftwareVersion"
    STANDBY_FACTORY_BUILD_NUMBER = "standbyFactoryBuildNumber"
    STANDBY_BUILD_NUMBER = "standbyBuildNumber"
    STANDBY_MODEL_NUMBER = "standbyModelNumber"
    STANDBY_DEVICE_ID = "standbyDeviceId"
    HA_WIFI_CAPABILITY_MISMATCH = "haWifiCapabilityMismatch"


class GetEdgesWith(StrEnum):
    SITE = "site"
    LINKS = "links"
    RECENT_LINKS = "recentLinks"
    CONFIGURATION = "configuration"
    CLOUD_SERVICES = "cloudServices"
    CLOUD_SERVICE_SITE_STATUS = "cloudServiceSiteStatus"
    NVS_FROM_EDGE = "nvsFromEdge"
    CERTIFICATES = "certificates"
    LICENSES = "licenses"
    WAN_LINKS = "wanLinks"


class GetEnterpriseEdgeListWith(StrEnum):
    CERTIFICATES = "certificates"
    CERTIFICATE_SUMMARY = "certificateSummary"
    CONFIGURATION = "configuration"
    CLOUD_SERVICES = "cloudServices"
    NVS_FROM_EDGE = "nvsFromEdge"
    HIGH_AVAILABILITY = "ha"
    IS_HUB = "isHub"
    IS_SOFTWARE_VERSION_SUPPORTED_BY_VCO = "isSoftwareVersionSupportedByVco"
    LICENSES = "licenses"
    LINKS = "links"
    RECENT_LINKS = "recentLinks"
    SITE = "site"
    IS_ASSIGNED_GATEWAY_QUIESCED = "isAssignedGatewayQuiesced"
    WAN_LINKS = "wanLinks"
    SECURE_DEVICE_SECRETS = "secureDeviceSecrets"


# Maps model names and aliases to their device family (profile model class).
# The device family is the key used in profile deviceSettings.data.models.
_ModelData = TypedDict("_ModelData", {"aliases": list[str], "class": str})

_MODEL_DATA: dict[str, _ModelData] = {
    "edge500": {"aliases": ["edge400", "pw_dev", "edge-nexcom"], "class": "edge500"},
    "edge5X0": {
        "aliases": ["edge520", "edge540", "edge540-poe", "edge520-v", "edge540-v"],
        "class": "edge5X0",
    },
    "edge515": {"aliases": [], "class": "edge515"},
    "edge6X0": {
        "aliases": [
            "edge610",
            "edge610-n",
            "edge620",
            "edge620-n",
            "edge640",
            "edge640-n",
            "edge680",
            "edge680-n",
        ],
        "class": "edge6X0",
    },
    "edge610lte": {"aliases": ["edge610-lte"], "class": "edge610lte"},
    "me6200": {"aliases": [], "class": "me6200"},
    "edge510": {"aliases": ["edge505", "edge510-n"], "class": "edge510"},
    "edge510lte": {"aliases": ["edge510-lte"], "class": "edge510lte"},
    "edge710": {"aliases": [], "class": "edge710"},
    "edge7105g": {"aliases": ["edge7105G", "edge710-5g"], "class": "edge7105g"},
    "edge7X0": {"aliases": ["edge720", "edge740"], "class": "edge7X0"},
    "edge840": {"aliases": [], "class": "edge840"},
    "edge1000": {"aliases": [], "class": "edge1000"},
    "edge1000qat": {"aliases": [], "class": "edge1000qat"},
    "edge3X00": {"aliases": ["edge3400", "edge3800"], "class": "edge3X00"},
    "edge3X10": {"aliases": ["edge3810"], "class": "edge3X10"},
    "edge4100": {"aliases": [], "class": "edge4100"},
    "edge5100": {"aliases": [], "class": "edge5100"},
    "cloudEdge": {"aliases": ["aws", "vc_dmz_gateway"], "class": "edge500"},
    "virtual": {
        "aliases": [
            "xen",
            "kvm",
            "ec2",
            "azure",
            "hyperv",
            "vmware",
            "aliyun",
            "gce",
            "orange",
            "azureNva",
        ],
        "class": "virtual",
    },
}

# Case-sensitive fixups after lowercasing
_CASE_FIXUPS: dict[str, str] = {
    "edge5x0": "edge5X0",
    "edge6x0": "edge6X0",
    "edge7x0": "edge7X0",
    "edge7105g": "edge7105g",
    "edge3x00": "edge3X00",
    "edge3x10": "edge3X10",
}


def _normalize_model(model: str) -> str:
    """Normalize a model string to the canonical casing."""
    lower = model.lower()
    return _CASE_FIXUPS.get(lower, lower)


def device_family_for_model(model: str) -> str:
    """Return the device family (profile model class) for a model name or alias.

    The device family is the key used in ``deviceSettings.data.models``
    within a profile configuration. Multiple specific models share the
    same device family and therefore the same interface layout.

    >>> device_family_for_model("edge620")
    'edge6X0'
    >>> device_family_for_model("EDGE7X0")
    'edge7X0'
    >>> device_family_for_model("virtual")
    'virtual'
    """
    normalized = _normalize_model(model)
    for name, data in _MODEL_DATA.items():
        if name == normalized:
            return data["class"]
        if normalized in data["aliases"]:
            return data["class"]
    return normalized


class ActivationState(StrEnum):
    UNASSIGNED = "UNASSIGNED"
    PENDING = "PENDING"
    ACTIVATED = "ACTIVATED"
    REACTIVATION_PENDING = "REACTIVATION_PENDING"


class EdgeBastionState(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    STAGE_REQUESTED = "STAGE_REQUESTED"
    UNSTAGE_REQUESTED = "UNSTAGE_REQUESTED"
    STAGED = "STAGED"
    UNSTAGED = "UNSTAGED"
    PROMOTION_REQUESTED = "PROMOTION_REQUESTED"
    PROMOTION_PENDING = "PROMOTION_PENDING"
    PROMOTED = "PROMOTED"


class EdgeServiceState(StrEnum):
    IN_SERVICE = "IN_SERVICE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    PENDING_SERVICE = "PENDING_SERVICE"


class EdgeState(StrEnum):
    NEVER_ACTIVATED = "NEVER_ACTIVATED"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    DISABLED = "DISABLED"
    EXPIRED = "EXPIRED"
    CONNECTED = "CONNECTED"


class EndpointPkiMode(StrEnum):
    CERTIFICATE_DISABLED = "CERTIFICATE_DISABLED"
    CERTIFICATE_OPTIONAL = "CERTIFICATE_OPTIONAL"
    CERTIFICATE_REQUIRED = "CERTIFICATE_REQUIRED"


class HaState(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    PENDING_INIT = "PENDING_INIT"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    PENDING_CONFIRMED = "PENDING_CONFIRMED"
    PENDING_DISSOCIATION = "PENDING_DISSOCIATION"
    READY = "READY"
    FAILED = "FAILED"


class HaType(StrEnum):
    ACTIVE_ACTIVE = "ACTIVE_ACTIVE"
    ACTIVE_STANDBY = "ACTIVE_STANDBY"
    VRRP = "VRRP"
    CLUSTER = "CLUSTER"
    NONE = "NONE"


class HaMode(StrEnum):
    STANDARD = "standard"
    ENHANCED = "enhanced"


class HaRole(StrEnum):
    ACTIVE = "ACTIVE"
    STANDBY = "STANDBY"


class SdwanPeerType(StrEnum):
    BRANCH = "BRANCH"
    HUB = "HUB"
    GATEWAY = "GATEWAY"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class GetEdgeWith(StrEnum):
    RECENT_LINKS = "recentLinks"
    LINKS = "links"
    SERVICE_GROUPS = "serviceGroups"
    SITE = "site"
    ENTERPRISE = "enterprise"
    CONFIGURATION = "configuration"
    CONFIGURATION_WITH_MODULES = "configurationWithModules"
    SELF_HEALING = "selfHealing"
    HIGH_AVAILABILITY = "ha"


class LinkState(StrEnum):
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"
    QUIET = "QUIET"
    INITIAL = "INITIAL"
    STANDBY = "STANDBY"
    DISCONNECTED = "DISCONNECTED"
    DOWN = "DOWN"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class LinkBackupState(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    STANDBY = "STANDBY"
    ACTIVE = "ACTIVE"


class LinkMode(StrEnum):
    ACTIVE = "ACTIVE"
    BACKUP = "BACKUP"
    HOTSTANDBY = "HOTSTANDBY"


class LinkVpnState(StrEnum):
    UNKNOWN = "UNKNOWN"
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"
    DEAD = "DEAD"
    STANDBY = "STANDBY"
    NONE = ""


class LinkEffectiveState(StrEnum):
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"
    DISCONNECTED = "DISCONNECTED"
    UNKNOWN = "UNKNOWN"
    STANDBY = "STANDBY"


class LinkNetworkSide(StrEnum):
    UNKNOWN = "UNKOWN"
    WAN = "WAN"
    LAN = "LAN"


class LinkNetworkType(StrEnum):
    UNKNOWN = "UNKNOWN"
    WIRELESS = "WIRELESS"
    ETHERNET = "ETHERNET"
    WIFI = "WIFI"


class LinkServiceState(StrEnum):
    IN_SERVICE = "IN_SERVICE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    HISTORICAL = "HISTORICAL"


class LinkType(StrEnum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class InterfaceState(StrEnum):
    DOWN = "DOWN"
    UP = "UP"
    LOWER_UP = "LOWER_UP"
    UNKNOWN = "UNKNOWN"


class NetworkSide(StrEnum):
    WAN = "WAN"
    LAN = "LAN"


class NetworkType(StrEnum):
    WAN = "WAN"
    LAN = "LAN"
    WLAN = "WLAN"
    ETHERNET = "ETHERNET"
