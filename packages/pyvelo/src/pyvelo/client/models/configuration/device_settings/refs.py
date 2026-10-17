from typing import Annotated, Any, Literal

from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel

from ..ref import ConfigurationRef

DeviceSettingsRefName = Literal[
    "deviceSettings:authentication",
    "deviceSettings:backHaulEdge",
    "deviceSettings:clientConnector:config",
    "deviceSettings:css:provider",
    "deviceSettings:css:site",
    "deviceSettings:dataCenter",
    "deviceSettings:dns:backupProvider",
    "deviceSettings:dns:primaryProvider",
    "deviceSettings:dns:privateProviders",
    "deviceSettings:edgeDirectNvs:provider",
    "deviceSettings:edgeDirectNvs:site",
    "deviceSettings:handOffGateways:gateways",
    "deviceSettings:securityVnf:license",
    "deviceSettings:securityVnf:service",
    "deviceSettings:segment",
    "deviceSettings:segment:netflowCollectors",
    "deviceSettings:segment:netflowFilters",
    "deviceSettings:vnfs:edge",
    "deviceSettings:vnfs:vnfImage",
    "deviceSettings:vpn:dataCenter",
    "deviceSettings:vpn:edgeHub",
    "deviceSettings:vpn:edgeHubCluster",
    "deviceSettings:zscaler:iaasSubscription",
    "deviceSettings:zscaler:location",
    "deviceSettings:zscaler:subLocation",
]


# Authentication Refs
class DeviceSettingsAuthenticationRef(ConfigurationRef):
    ref: Literal["deviceSettings:authentication"] | MISSING = MISSING
    data: dict[str, Any] | MISSING = MISSING


# BackHaul Edge Refs
class DeviceSettingsBackHaulEdgeRef(ConfigurationRef):
    ref: Literal["deviceSettings:backHaulEdge"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["edgeHub"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Client Connector Refs
class DeviceSettingsClientConnectorConfigRef(ConfigurationRef):
    ref: Literal["deviceSettings:clientConnector:config"] | MISSING = MISSING
    object_: Annotated[Literal["CLIENT_CONNECTOR"] | MISSING, Alias("object")] = MISSING
    status: str | None | MISSING = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Cloud Security Service (CSS) Refs
class DeviceSettingsCssProviderRef(ConfigurationRef):
    ref: Literal["deviceSettings:css:provider"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[str | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsCssSiteRef(ConfigurationRef):
    ref: Literal["deviceSettings:css:site"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    parent_group_id: int | None | MISSING = MISSING
    type_: Annotated[str | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Data Center Refs
class DeviceSettingsDataCenterRef(ConfigurationRef):
    ref: Literal["deviceSettings:dataCenter"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["cloudSecurityService"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# DNS Refs
class DeviceSettingsDnsPrimaryProviderRef(ConfigurationRef):
    ref: Literal["deviceSettings:dns:primaryProvider"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsDnsBackupProviderRef(ConfigurationRef):
    ref: Literal["deviceSettings:dns:backupProvider"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsDnsPrivateProvidersRef(ConfigurationRef):
    ref: Literal["deviceSettings:dns:privateProviders"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Edge Direct NVS Refs
class DeviceSettingsEdgeDirectNvsProviderRef(ConfigurationRef):
    ref: Literal["deviceSettings:edgeDirectNvs:provider"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsEdgeDirectNvsSiteRef(ConfigurationRef):
    ref: Literal["deviceSettings:edgeDirectNvs:site"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Hand-Off Gateways Refs
class DeviceSettingsHandOffGatewaysRef(ConfigurationRef):
    ref: Literal["deviceSettings:handOffGateways:gateways"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["partnerGateway"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Security VNF Refs
class DeviceSettingsSecurityVnfServiceRef(ConfigurationRef):
    ref: Literal["deviceSettings:securityVnf:service"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["securityVnfService"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsSecurityVnfLicenseRef(ConfigurationRef):
    ref: Literal["deviceSettings:securityVnf:license"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Segment Refs
class DeviceSettingsSegmentRef(ConfigurationRef):
    ref: Literal["deviceSettings:segment"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SEGMENT"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["REGULAR"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsSegmentNetflowCollectorsRef(ConfigurationRef):
    ref: Literal["deviceSettings:segment:netflowCollectors"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsSegmentNetflowFiltersRef(ConfigurationRef):
    ref: Literal["deviceSettings:segment:netflowFilters"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["netflowFilter"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# VNF Refs
class DeviceSettingsVnfsEdgeRef(ConfigurationRef):
    ref: Literal["deviceSettings:vnfs:edge"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["securityVnf"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsVnfsVnfImageRef(ConfigurationRef):
    ref: Literal["deviceSettings:vnfs:vnfImage"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["vnfImage"] | MISSING, Alias("type")] = MISSING
    status: str | None | MISSING = MISSING
    data: dict[str, Any] | MISSING = MISSING


# VPN Refs
class DeviceSettingsVpnDataCenterRef(ConfigurationRef):
    ref: Literal["deviceSettings:vpn:dataCenter"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsVpnEdgeHubRef(ConfigurationRef):
    ref: Literal["deviceSettings:vpn:edgeHub"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["edgeHub"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsVpnEdgeHubClusterRef(ConfigurationRef):
    ref: Literal["deviceSettings:vpn:edgeHubCluster"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Zscaler Refs
class DeviceSettingsZscalerIaasSubscriptionRef(ConfigurationRef):
    ref: Literal["deviceSettings:zscaler:iaasSubscription"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsZscalerLocationRef(ConfigurationRef):
    ref: Literal["deviceSettings:zscaler:location"] | MISSING = MISSING
    object_: Annotated[Literal["ZSCALER_LOCATION"] | MISSING, Alias("object")] = MISSING
    type_: Annotated[Literal["zscaler"] | MISSING, Alias("type")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


class DeviceSettingsZscalerSubLocationRef(ConfigurationRef):
    ref: Literal["deviceSettings:zscaler:subLocation"] | MISSING = MISSING
    object_: Annotated[Literal["NETWORK_SERVICE"] | MISSING, Alias("object")] = MISSING
    data: dict[str, Any] | MISSING = MISSING


# Container class for all DeviceSettings refs
class DeviceSettingsRefs(VcoModel):
    # Authentication
    authentication: Annotated[
        DeviceSettingsAuthenticationRef
        | list[DeviceSettingsAuthenticationRef]
        | MISSING,
        Alias("deviceSettings:authentication"),
    ] = MISSING

    # BackHaul Edge
    back_haul_edge: Annotated[
        DeviceSettingsBackHaulEdgeRef | list[DeviceSettingsBackHaulEdgeRef] | MISSING,
        Alias("deviceSettings:backHaulEdge"),
    ] = MISSING

    # Client Connector
    client_connector_config: Annotated[
        DeviceSettingsClientConnectorConfigRef
        | list[DeviceSettingsClientConnectorConfigRef]
        | MISSING,
        Alias("deviceSettings:clientConnector:config"),
    ] = MISSING

    # Cloud Security Service (CSS)
    css_provider: Annotated[
        DeviceSettingsCssProviderRef | list[DeviceSettingsCssProviderRef] | MISSING,
        Alias("deviceSettings:css:provider"),
    ] = MISSING

    css_site: Annotated[
        DeviceSettingsCssSiteRef | list[DeviceSettingsCssSiteRef] | MISSING,
        Alias("deviceSettings:css:site"),
    ] = MISSING

    # Data Center
    data_center: Annotated[
        DeviceSettingsDataCenterRef | list[DeviceSettingsDataCenterRef] | MISSING,
        Alias("deviceSettings:dataCenter"),
    ] = MISSING

    # DNS
    dns_primary_provider: Annotated[
        DeviceSettingsDnsPrimaryProviderRef
        | list[DeviceSettingsDnsPrimaryProviderRef]
        | MISSING,
        Alias("deviceSettings:dns:primaryProvider"),
    ] = MISSING

    dns_backup_provider: Annotated[
        DeviceSettingsDnsBackupProviderRef
        | list[DeviceSettingsDnsBackupProviderRef]
        | MISSING,
        Alias("deviceSettings:dns:backupProvider"),
    ] = MISSING

    dns_private_providers: Annotated[
        DeviceSettingsDnsPrivateProvidersRef
        | list[DeviceSettingsDnsPrivateProvidersRef]
        | MISSING,
        Alias("deviceSettings:dns:privateProviders"),
    ] = MISSING

    # Edge Direct NVS
    edge_direct_nvs_provider: Annotated[
        DeviceSettingsEdgeDirectNvsProviderRef
        | list[DeviceSettingsEdgeDirectNvsProviderRef]
        | MISSING,
        Alias("deviceSettings:edgeDirectNvs:provider"),
    ] = MISSING

    edge_direct_nvs_site: Annotated[
        DeviceSettingsEdgeDirectNvsSiteRef
        | list[DeviceSettingsEdgeDirectNvsSiteRef]
        | MISSING,
        Alias("deviceSettings:edgeDirectNvs:site"),
    ] = MISSING

    # Hand-Off Gateways
    hand_off_gateways: Annotated[
        DeviceSettingsHandOffGatewaysRef
        | list[DeviceSettingsHandOffGatewaysRef]
        | MISSING,
        Alias("deviceSettings:handOffGateways:gateways"),
    ] = MISSING

    # Security VNF
    security_vnf_service: Annotated[
        DeviceSettingsSecurityVnfServiceRef
        | list[DeviceSettingsSecurityVnfServiceRef]
        | MISSING,
        Alias("deviceSettings:securityVnf:service"),
    ] = MISSING

    security_vnf_license: Annotated[
        DeviceSettingsSecurityVnfLicenseRef
        | list[DeviceSettingsSecurityVnfLicenseRef]
        | MISSING,
        Alias("deviceSettings:securityVnf:license"),
    ] = MISSING

    # Segment
    segment: Annotated[
        DeviceSettingsSegmentRef | list[DeviceSettingsSegmentRef] | MISSING,
        Alias("deviceSettings:segment"),
    ] = MISSING

    segment_netflow_collectors: Annotated[
        DeviceSettingsSegmentNetflowCollectorsRef
        | list[DeviceSettingsSegmentNetflowCollectorsRef]
        | MISSING,
        Alias("deviceSettings:segment:netflowCollectors"),
    ] = MISSING

    segment_netflow_filters: Annotated[
        DeviceSettingsSegmentNetflowFiltersRef
        | list[DeviceSettingsSegmentNetflowFiltersRef]
        | MISSING,
        Alias("deviceSettings:segment:netflowFilters"),
    ] = MISSING

    # VNFs
    vnfs_edge: Annotated[
        DeviceSettingsVnfsEdgeRef | list[DeviceSettingsVnfsEdgeRef] | MISSING,
        Alias("deviceSettings:vnfs:edge"),
    ] = MISSING

    vnfs_vnf_image: Annotated[
        DeviceSettingsVnfsVnfImageRef | list[DeviceSettingsVnfsVnfImageRef] | MISSING,
        Alias("deviceSettings:vnfs:vnfImage"),
    ] = MISSING

    # VPN
    vpn_data_center: Annotated[
        DeviceSettingsVpnDataCenterRef | list[DeviceSettingsVpnDataCenterRef] | MISSING,
        Alias("deviceSettings:vpn:dataCenter"),
    ] = MISSING

    vpn_edge_hub: Annotated[
        DeviceSettingsVpnEdgeHubRef | list[DeviceSettingsVpnEdgeHubRef] | MISSING,
        Alias("deviceSettings:vpn:edgeHub"),
    ] = MISSING

    vpn_edge_hub_cluster: Annotated[
        DeviceSettingsVpnEdgeHubClusterRef
        | list[DeviceSettingsVpnEdgeHubClusterRef]
        | MISSING,
        Alias("deviceSettings:vpn:edgeHubCluster"),
    ] = MISSING

    # Zscaler
    zscaler_iaas_subscription: Annotated[
        DeviceSettingsZscalerIaasSubscriptionRef
        | list[DeviceSettingsZscalerIaasSubscriptionRef]
        | MISSING,
        Alias("deviceSettings:zscaler:iaasSubscription"),
    ] = MISSING

    zscaler_location: Annotated[
        DeviceSettingsZscalerLocationRef
        | list[DeviceSettingsZscalerLocationRef]
        | MISSING,
        Alias("deviceSettings:zscaler:location"),
    ] = MISSING

    zscaler_sub_location: Annotated[
        DeviceSettingsZscalerSubLocationRef
        | list[DeviceSettingsZscalerSubLocationRef]
        | MISSING,
        Alias("deviceSettings:zscaler:subLocation"),
    ] = MISSING
