"""The ``data`` of each enterprise service type.

Authentication (RADIUS, TACACS), DNS, data center, IaaS subscriptions, NetFlow
collectors and filters, local UI credentials, hubs and hub clusters, NVS and
WSS integrations, and prefix tags.
"""

import uuid
from collections.abc import Generator
from typing import Annotated, Any, ClassVar, Literal

from pydantic import MISSING, ConfigDict, Discriminator, Field

from pyvelo.core import Alias, ConfigModel, Secret

from .common import ProviderCategory, RoutingPolicy, TunnelingProtocol, TunnelMode


class RadiusServer(ConfigModel):
    port: int
    secret: Annotated[str | MISSING, Secret()] = MISSING
    server: str | MISSING = MISSING
    accounting_port: int | Literal[""] | MISSING = MISSING


class RadiusAttributeModel(ConfigModel):
    id_: Annotated[int, Alias("id")]
    type_: Annotated[str, Alias("type")]
    value: str | int


class RadiusServiceData(ConfigModel):
    type_: Annotated[Literal["Radius"], Alias("type")] = "Radius"
    primary: RadiusServer
    secondary: RadiusServer | MISSING = MISSING
    radius_attributes: list[RadiusAttributeModel] | MISSING = MISSING


class TacacsServer(ConfigModel):
    port: int
    server: str


class TacacsServiceData(ConfigModel):
    type_: Annotated[Literal["Tacacs"], Alias("type")] = "Tacacs"
    shared_secret: Annotated[str, Secret()]
    servers: list[TacacsServer]


AuthenticationServiceData = Annotated[
    RadiusServiceData | TacacsServiceData,
    Discriminator(discriminator="type_"),
]


class DnsDomain(ConfigModel):
    rule: str
    description: str


class DnsServiceData(ConfigModel):
    is_private: bool | MISSING = MISSING
    domains: list[DnsDomain] | MISSING = MISSING
    ipv4_servers: list[str] | MISSING = MISSING
    ipv6_servers: list[str] | MISSING = MISSING


class DataCenterSubnet(ConfigModel):
    cidr_ip: str | MISSING = MISSING
    cidr_prefix: int | MISSING = MISSING


class DataCenterPrimary(ConfigModel):
    gateway_ip: str | MISSING = MISSING
    provider_gateway_logical_id: uuid.UUID | MISSING = MISSING
    provider_gateway_name: str | MISSING = MISSING


class LoadSharingHashParams(ConfigModel):
    dest_ip: bool | MISSING = MISSING
    dest_port: bool | MISSING = MISSING
    protocol: bool | MISSING = MISSING
    source_ip: Annotated[bool | MISSING, Alias("srcIp")] = MISSING
    source_port: Annotated[bool | MISSING, Alias("srcPort")] = MISSING


class LoadSharingMethod(ConfigModel):
    algo: Literal["FLOW_BASED", "HASH_BASED", ""] | MISSING = MISSING
    hash_params: LoadSharingHashParams | MISSING = MISSING


class DataCenterAuthentication(ConfigModel):
    type_: Annotated[Literal["FQDN", "IPv4", "USER_FQDN"], Alias("type")]
    key: str


class BgpFilter(ConfigModel):
    ids: list[str]


class BgpNeighbor(ConfigModel):
    id_: Annotated[str, Alias("id")]
    neighbor_as: Annotated[str, Alias("neighborAS")]
    neighbor_ip: str
    local_ip: Annotated[str, Alias("localIP")]
    max_hop: str
    data_center_public_ip: str
    inbound_filter: BgpFilter
    outbound_filter: BgpFilter


class DataCenterBgpConfig(ConfigModel):
    disabled: bool
    neighbors: list[BgpNeighbor]
    redundant_neighbors: list[BgpNeighbor]


class BfdNeighbor(ConfigModel):
    detect_multiplier: int
    local_address: str
    multi_hop: bool
    peer_address: str
    receive_interval: int
    transmit_interval: int
    data_center_public_ip: str


class DataCenterBfdConfig(ConfigModel):
    disabled: bool
    neighbors: list[BfdNeighbor]
    redundant_neighbors: list[BfdNeighbor]


class DataCenterServiceData(ConfigModel):
    type_: Annotated[str | MISSING, Alias("type")] = MISSING
    type_alias: str | MISSING = MISSING
    tunnel_mode: str | MISSING = MISSING
    is_service_enabled: bool | MISSING = MISSING
    primary: DataCenterPrimary | MISSING = MISSING
    secondary: DataCenterPrimary | MISSING = MISSING
    bgp_disabled: bool | MISSING = MISSING
    bfd_disabled: bool | MISSING = MISSING
    subnets: list[DataCenterSubnet] | MISSING = MISSING
    custom_source_subnet: list[DataCenterSubnet] | MISSING = MISSING
    datacenter_version: str | MISSING = MISSING
    subnets_version: str | MISSING = MISSING
    login_url: str | MISSING = MISSING
    load_sharing_method: LoadSharingMethod | MISSING = MISSING
    vpn_gateways: list[DataCenterPrimary] | MISSING = MISSING
    authentication: DataCenterAuthentication | MISSING = MISSING
    bgp: DataCenterBgpConfig | MISSING = MISSING
    bfd: DataCenterBfdConfig | MISSING = MISSING
    iaas_provider: dict[str, Any] | MISSING = MISSING
    aggregation: list[Any] | MISSING = MISSING
    l7_health_check: dict[str, Any] | MISSING = MISSING


class IaasSubscription(ConfigModel):
    id_: Annotated[str | MISSING, Alias("id")] = MISSING
    name: str | MISSING = MISSING


class AzureVendorSpecificData(ConfigModel):
    client_id: str
    client_secret: Annotated[str, Secret()]
    tenant_id: str
    subscription: IaasSubscription


class AzureSubscriptionData(ConfigModel):
    vendor: Literal["MicrosoftAzure"] = "MicrosoftAzure"
    vendor_specific_data: AzureVendorSpecificData


class ZscalerVendorSpecificData(ConfigModel):
    api_host: str
    api_path: str
    api_token: Annotated[str, Secret()]
    domain: str
    password: Annotated[str, Secret()]
    username: str


class ZscalerSubscriptionData(ConfigModel):
    validated: bool | MISSING = MISSING
    vendor: Literal["Zscaler"] = "Zscaler"
    vendor_specific_data: ZscalerVendorSpecificData


class AwsVendorSpecificData(ConfigModel):
    access_key: Annotated[str, Secret()]
    secret_key: Annotated[str, Secret()]
    session_token: Annotated[str | MISSING, Secret()] = MISSING


class AwsSubscriptionData(ConfigModel):
    vendor: Literal["AWS"] = "AWS"
    vendor_specific_data: AwsVendorSpecificData


class SseVendorSpecificData(ConfigModel):
    username: str
    password: Annotated[str, Secret()]
    cloud_type: str | MISSING = MISSING
    tenant_id: str | MISSING = MISSING
    expiry: bool | MISSING = MISSING
    expiry_date: str | MISSING = MISSING


class SseSubscriptionData(ConfigModel):
    validated: bool | MISSING = MISSING
    type_alias: str | MISSING = MISSING
    vendor: Literal["sse"] = "sse"
    vendor_specific_data: SseVendorSpecificData


IaasSubscriptionData = Annotated[
    AwsSubscriptionData
    | ZscalerSubscriptionData
    | AzureSubscriptionData
    | SseSubscriptionData,
    Discriminator(discriminator="vendor"),
]


class NetflowCollectorData(ConfigModel):
    address: str
    name: str
    port: str | int


class NetflowFilterMatch(ConfigModel):
    appid: int = -1
    classid: int = -1
    dip: str = "any"
    dport_high: Annotated[int, Alias("dport_high")] = -1
    dport_low: Annotated[int, Alias("dport_low")] = -1
    dscp: int = -1
    dsm: str = "any"
    dvlan: int = -1
    hostname: str = ""
    os_version: Annotated[int, Alias("os_version")] = -1
    proto: int = -1
    sip: str = "any"
    smac: str = "any"
    sport_high: Annotated[int, Alias("sport_high")] = -1
    sport_low: Annotated[int, Alias("sport_low")] = -1
    ssm: str = "any"
    svlan: int = -1
    ip_version: Literal["IPv4", "IPv6"] | MISSING = "IPv4"


class NetflowFilterAction(ConfigModel):
    allow_or_deny: Annotated[Literal["allow", "deny"], Alias("allow_or_deny")]


class NetflowFilterData(ConfigModel):
    name: str
    match: NetflowFilterMatch
    action: NetflowFilterAction


class LocalUiCredentialsData(ConfigModel):
    configuration_id: int | MISSING = MISSING
    password: Annotated[str, Secret()]
    username: str


class EdgeRoles(ConfigModel):
    edge_hub: bool = Field(..., description="Hub acts as a basic hub")
    edge_to_edge_bridge: bool = Field(
        ..., description="Hub acts as a branch-to-branch VPN hub"
    )
    back_haul_edge: bool = Field(
        ...,
        description="Hub can be used as a backhaul target in branch business policies",
    )


class EdgeHubServiceData(ConfigModel):
    logical_id: uuid.UUID
    roles: EdgeRoles


class EdgeHubClusterMemberServiceData(ConfigModel):
    logical_id: uuid.UUID


class EdgeClusterConfig(ConfigModel):
    roles: EdgeRoles


class EdgeHubClusterServiceData(ConfigModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    auto_rebalance: bool | MISSING = MISSING
    route_summary_v4: list[Any] | MISSING = MISSING
    route_summary_v6: list[Any] | MISSING = MISSING

    def iter_config_segment_roles(self) -> Generator[tuple[int, int, EdgeRoles]]:
        if self.model_extra is None:
            return

        segments: dict[str, dict[str, Any]]
        for config_key, segments in self.model_extra.items():
            roles: dict[str, Any]
            for segment_key, roles in segments.items():
                if "roles" in roles:
                    segment_roles: EdgeRoles = roles["roles"]
                    yield (int(config_key), int(segment_key), segment_roles)


# Cloud Security Service Models (provider-specific discriminated unions)


class BaseCloudSecurityCredentials(ConfigModel):
    username: str
    password: Annotated[str, Secret()]
    validated: bool


class SymantecCloudSecurityCredentials(BaseCloudSecurityCredentials):
    api_host: str | MISSING = MISSING


class ZscalerCloudSecurityCredentials(BaseCloudSecurityCredentials):
    api_host: str | None
    api_path: str
    api_token: Annotated[str | MISSING, Secret()] = MISSING


class L7HealthCheck(ConfigModel):
    enabled: bool | MISSING = MISSING
    cloud: str | None | MISSING = MISSING
    num_of_retries: int | MISSING = MISSING
    probe_interval_sec: int | MISSING = MISSING
    rtt_threshold_ms: int | MISSING = MISSING


class BaseCloudSecurityConfig(ConfigModel):
    enable_tunnels: bool
    primary_server: str
    secondary_server: str | None
    shared_ike_auth: bool
    max_tunnels_per_ike_identity: int


class GenericCloudSecurityConfig(BaseCloudSecurityConfig):
    pass


class SymantecCloudSecurityConfig(BaseCloudSecurityConfig):
    automate_deployment: bool
    credentials: SymantecCloudSecurityCredentials


class ZscalerCloudSecurityConfig(BaseCloudSecurityConfig):
    automate_deployment: bool
    credentials: ZscalerCloudSecurityCredentials
    domain: str | MISSING = MISSING
    domestic_preference_enabled: bool | MISSING = MISSING
    l7_health_check: L7HealthCheck | MISSING = MISSING
    login_url: str | None | MISSING = MISSING
    tunneling_protocol: TunnelingProtocol | MISSING = MISSING


class GenericCloudSecurityServiceData(ConfigModel):
    provider: Literal["genericCloudSecurityService"] = "genericCloudSecurityService"
    type_: Annotated[Literal["cloudSecurityService"], Alias("type")] = (
        "cloudSecurityService"
    )
    config: GenericCloudSecurityConfig


class SymantecCloudSecurityServiceData(ConfigModel):
    provider: Literal["symantecCloudSecurityService"] = "symantecCloudSecurityService"
    type_: Annotated[Literal["cloudSecurityService"], Alias("type")] = (
        "cloudSecurityService"
    )
    config: SymantecCloudSecurityConfig


class ZscalerCloudSecurityServiceData(ConfigModel):
    provider: Literal["zscalerWebSecurityService"] = "zscalerWebSecurityService"
    type_: Annotated[Literal["cloudSecurityService"], Alias("type")] = (
        "cloudSecurityService"
    )
    config: ZscalerCloudSecurityConfig


CloudSecurityServiceData = Annotated[
    GenericCloudSecurityServiceData
    | SymantecCloudSecurityServiceData
    | ZscalerCloudSecurityServiceData,
    Discriminator(discriminator="provider"),
]


# NVS Via Edge Service Models (Network Virtual Service - IPSec/GRE tunnels)


class PeerIkeId(ConfigModel):
    ike_id: str | MISSING = MISSING
    ike_id_type: Literal["FQDN", "IP"] | MISSING = MISSING


class IkeProperties(ConfigModel):
    dh_group: Annotated[int | MISSING, Alias("DHGroup")] = MISSING
    pfs: Annotated[int | MISSING, Alias("PFS")] = MISSING
    authentication_algorithm: str
    authentication_method: str
    dpd_timeout_seconds: int
    dpd_type: str
    encryption_algorithm: str
    ikev1_main_mode: bool
    life_time_seconds: int
    protocol_version: Literal[1, 2]
    peer_ike_id: PeerIkeId | MISSING = MISSING


class IpsecProperties(ConfigModel):
    authentication_algorithm: str
    encryption_algorithm: str
    ipsec_tunnel_type: str
    life_time_seconds: int
    protocol: str


class NvsSubnet(ConfigModel):
    cidr_ip: str | MISSING = MISSING
    cidr_prefix: str | int | MISSING = MISSING
    net_mask: str | MISSING = MISSING
    name: str | MISSING = MISSING
    advertise: bool | MISSING = MISSING
    metric: int | MISSING = MISSING


class SubnetList(ConfigModel):
    subnets: list[NvsSubnet]
    always_reachable: bool | MISSING = MISSING
    version: str | MISSING = MISSING


class BgpConfig(ConfigModel):
    enabled: bool


class AwsTransitGatewayVendorData(ConfigModel):
    region_id: str
    transit_gateway_id: str
    transit_vpc_id: str
    vcp_attachment_id: str


class IaasProvider(ConfigModel):
    vendor: Literal["aws"] = "aws"
    subscription_object_logical_id: uuid.UUID
    vendor_specific_data: AwsTransitGatewayVendorData


class Keepalive(ConfigModel):
    type_: Annotated[Literal["GRE", "IPSEC"], Alias("type")]
    enabled: bool
    interval_secs: int
    retries: int


class IkeNvsServer(ConfigModel):
    nvs_public_ip: str
    local_link_ip: str
    peer_link_ip: str
    ikeprop: Annotated[IkeProperties, Alias("IKEPROP")]
    ipsecprop: Annotated[IpsecProperties, Alias("IPSECPROP")]


class IkeNvsBackupServer(IkeNvsServer):
    enabled: bool
    same_tunnel_settings_as_primary: bool | MISSING = MISSING


class AwsNvsServer(ConfigModel):
    nvs_public_ip: str
    address_family: Literal["IPv4", "IPv6"]


class AwsNvsBackupServer(AwsNvsServer):
    enabled: bool
    same_tunnel_settings_as_primary: bool
    # AWS backup server can also have address_family field (legacy)
    address_family_legacy: Annotated[
        Literal["IPv4", "IPv6"] | MISSING, Alias("address_family")
    ] = MISSING


class AwsTransitGatewayServiceData(ConfigModel):
    provider: Literal["AWSTransitGateway"] = "AWSTransitGateway"
    type_: Annotated[Literal["AWSTransitGateway"], Alias("type")] = "AWSTransitGateway"
    automate_deployment: bool
    enabled: bool
    bgp: BgpConfig
    cssp: bool
    keep_backup_server_connected: bool
    peer_subnets: SubnetList
    source_subnets: SubnetList
    provider_category: ProviderCategory = "DATACENTER"
    routing_policy: RoutingPolicy
    shared_ike_auth: bool
    tunneling_protocol: TunnelingProtocol
    type_alias: str
    version: str
    address_family: Literal["IPv4", "IPv6"]
    tunnel_mode: TunnelMode
    primary_server: AwsNvsServer
    backup_server: AwsNvsBackupServer
    iaas_provider: IaasProvider
    keepalive: Keepalive


class GenericIkev1RouterServiceData(ConfigModel):
    provider: Literal["genericIKEv1Router"] = "genericIKEv1Router"
    type_: Annotated[Literal["genericIKEv1Router"], Alias("type")] = (
        "genericIKEv1Router"
    )
    automate_deployment: bool
    enabled: bool
    bgp: BgpConfig
    cssp: bool
    keep_backup_server_connected: bool
    peer_subnets: SubnetList
    source_subnets: SubnetList
    provider_category: ProviderCategory = "DATACENTER"
    routing_policy: RoutingPolicy
    shared_ike_auth: bool
    tunneling_protocol: TunnelingProtocol
    type_alias: str
    version: str
    address_family: Literal["IPv4", "IPv6"] | MISSING = MISSING
    tunnel_mode: TunnelMode | MISSING = MISSING
    primary_server: IkeNvsServer
    backup_server: IkeNvsBackupServer


class GenericIkev2RouterServiceData(ConfigModel):
    provider: Literal["genericIKEv2Router"] = "genericIKEv2Router"
    type_: Annotated[Literal["genericIKEv2Router"], Alias("type")] = (
        "genericIKEv2Router"
    )
    automate_deployment: bool
    enabled: bool
    bgp: BgpConfig
    cssp: bool
    keep_backup_server_connected: bool
    peer_subnets: SubnetList
    source_subnets: SubnetList
    provider_category: ProviderCategory = "DATACENTER"
    routing_policy: RoutingPolicy
    shared_ike_auth: bool
    tunneling_protocol: TunnelingProtocol
    type_alias: str
    version: str
    address_family: Literal["IPv4", "IPv6"] | MISSING = MISSING
    tunnel_mode: TunnelMode | MISSING = MISSING
    primary_server: IkeNvsServer
    backup_server: IkeNvsBackupServer


class GenericNvsViaEdgeServiceData(ConfigModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    provider: Literal["symantecSase", "microsoftAzureVirtualWan"]


NvsViaEdgeServiceData = Annotated[
    AwsTransitGatewayServiceData
    | GenericIkev1RouterServiceData
    | GenericIkev2RouterServiceData
    | GenericNvsViaEdgeServiceData,
    Discriminator(discriminator="provider"),
]


# Prefix Tag Service Models


class PrefixTagData(ConfigModel):
    pass


class WssIaasProvider(ConfigModel):
    subscription_object_name: str
    subscription_object_logical_id: uuid.UUID
    vendor: Literal["sse"] = "sse"


class WssIntegrationData(ConfigModel):
    type_: Annotated[Literal["sse"], Alias("type")] = "sse"
    type_alias: Literal["symantecSase"] = "symantecSase"
    provider: Literal["symantecSase"] = "symantecSase"
    iaas_provider: WssIaasProvider
