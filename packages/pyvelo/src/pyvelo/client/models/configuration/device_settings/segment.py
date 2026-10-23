import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import (
    MISSING,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    RootModel,
    Tag,
    field_validator,
    model_validator,
)

from pyvelo.core import (
    Alias,
    EdgeOnly,
    GatewayLogicalId,
    NoneIsFalse,
    VcoModel,
)
from pyvelo.core.fields import (
    DefaultingModel,
    DefaultingScalar,
    KeyField,
    NoneIsTrue,
    OverrideFlag,
    ProfileOnly,
)

from ..common import SegmentMetadata
from .common import (
    AdvertiseDefaultRoutes,
    AdvertiseDefaultRoutesMethod,
    DeviceSettingsRef,
    LanSideNatRuleType,
    OspfAreaType,
    OspfExternalRouteType,
    ProviderRef,
)


class BfdRuleBase(VcoModel):
    detect_multiplier: int = 3
    receive_interval: int = 300
    transmit_interval: int = 300
    local_address: str
    peer_address: str
    multi_hop: bool = False


class BfdRuleNsd(BfdRuleBase):
    data_center_public_ip: str


class SegmentBfd(VcoModel):
    enabled: bool = False
    override: Annotated[bool, OverrideFlag] = True
    rules: list[BfdRuleBase] = Field(default_factory=list)
    rules_v6: list[BfdRuleBase] = Field(default_factory=list)


class SegmentEdgeDirectProviderConfig(VcoModel):
    enabled: bool = False
    use_all_public_wan_links: bool = True


class SegmentEdgeDirectProvider(VcoModel):
    config: SegmentEdgeDirectProviderConfig
    logical_id: uuid.UUID


class SegmentEdgeDirect(VcoModel):
    enabled: bool = False
    override: Annotated[bool, OverrideFlag] = True
    provider: ProviderRef = Field(
        default_factory=lambda: ProviderRef(ref="deviceSettings:edgeDirectNvs:provider")
    )
    providers: list[SegmentEdgeDirectProvider] = Field(default_factory=list)


class SegmentHubInterconnect(VcoModel):
    enabled: bool


class GatewayListItem(VcoModel):
    logical_id: GatewayLogicalId
    ipsec_detail: Any | MISSING = MISSING


class SegmentHandoffGateways(VcoModel):
    auto_select: bool = False
    gateway_list: list[GatewayListItem] = Field(default_factory=list)
    override: Annotated[bool, OverrideFlag] = True
    gateways: list[GatewayListItem] = Field(default_factory=list)


SegmentVrrp = RootModel[dict[str, Any] | None]


class VpnHub(VcoModel):
    name: str
    type_: Annotated[Literal["edgeHub", "edgeHubCluster"], Alias("type")]
    logical_id: uuid.UUID


class IsolationConfig(VcoModel):
    enabled: bool = False


class DynamicVpnConfig(VcoModel):
    enabled: bool = True
    type_: Annotated[Literal["DIRECT", "RELAY"], Alias("type")] = "DIRECT"
    timeout: int = 180
    isolation: IsolationConfig = Field(default_factory=IsolationConfig)


class EdgeToEdgeDetail(VcoModel):
    isolation: IsolationConfig = Field(default_factory=IsolationConfig)
    use_cloud_gateway: bool = True
    encryption_protocol: Literal["GROUP_IPSEC", "AES_128_GCM", "AES_256_GCM"] = (
        "GROUP_IPSEC"
    )
    dynamic: DynamicVpnConfig = Field(default_factory=DynamicVpnConfig)
    vpn_hubs: list[VpnHub] = Field(default_factory=list)
    auto_select_vpn_hubs: bool = False
    isolation_group_id: None = None
    isolation_groups: list[Any] = Field(default_factory=list)


class EdgeToEdgeHub(VcoModel):
    enabled: bool = False
    ref: DeviceSettingsRef = "deviceSettings:vpn:edgeHub"
    vpn_hubs: list[VpnHub] = Field(default_factory=list)


class SegmentVpn(VcoModel):
    enabled: bool = False
    edge_to_data_center: bool = False
    conditional_backhaul: bool = False
    ref: DeviceSettingsRef = "deviceSettings:vpn:dataCenter"
    hub_edges: Annotated[EdgeToEdgeHub, Alias("edgeToEdgeHub")] = Field(
        default_factory=EdgeToEdgeHub
    )
    edge_to_edge: bool = False
    edge_to_edge_detail: EdgeToEdgeDetail = Field(default_factory=EdgeToEdgeDetail)
    backhaul_edges: Annotated[list[VpnHub], Alias("backHaulEdges")] = Field(
        default_factory=list
    )


class SegmentStaticGroup(VcoModel):
    multicast_groups: list[str] = Field(default_factory=list)
    rp_address: str


class MulticastRp(VcoModel):
    type_: Annotated[Literal["STATIC"], Alias("type")] = "STATIC"
    static_groups: list[SegmentStaticGroup] = Field(default_factory=list)


class PimOnWanOverlay(VcoModel):
    enabled: bool = False
    type_: Annotated[Literal["SOURCE_IP"], Alias("type")] = "SOURCE_IP"
    source_cidr_ip: str | None = None
    source_interface: str | None = None


class SegmentMulticast(VcoModel):
    enabled: bool = False
    rp: MulticastRp = Field(default_factory=MulticastRp)
    pim_on_wan_overlay: PimOnWanOverlay = Field(default_factory=PimOnWanOverlay)
    pim_keep_alive_timer_seconds: Annotated[
        int, BeforeValidator(lambda v: 210 if v is None else v)
    ] = 210
    pim_prune_interval_seconds: Annotated[
        int, BeforeValidator(lambda v: 5 if v is None else v)
    ] = 5
    igmp_host_query_interval_seconds: None = None
    igmp_max_query_response: None = None
    override: Annotated[bool, OverrideFlag] = True


class OspfAggregation(VcoModel):
    prefix: str
    prefix_length: int
    tag: int | None = None
    no_advertise: bool = False
    metric: int | None = None
    metric_type: OspfExternalRouteType = "E1"


class OspfBgpConfig(VcoModel):
    enabled: bool = False
    metric: int = 20
    metric_type: OspfExternalRouteType = "E2"


class SegmentOspfArea(VcoModel):
    id_: Annotated[int | str, Alias("id")]
    name: Annotated[str, DefaultingScalar(str, default="")] = ""
    type_: Annotated[OspfAreaType, Alias("type")] = "normal"


class SegmentOspfBase(VcoModel):
    aggregation: list[OspfAggregation] = Field(default_factory=list)
    enabled: bool = False
    areas: list[SegmentOspfArea] = Field(default_factory=list)
    default_routes: AdvertiseDefaultRoutes = "NONE"
    default_prefixes: bool = False
    bgp: OspfBgpConfig = Field(default_factory=OspfBgpConfig)
    default_route_advertise: AdvertiseDefaultRoutesMethod = "NONE"


class SegmentOspf(SegmentOspfBase):
    v6_detail: SegmentOspfBase = Field(default_factory=SegmentOspfBase)


class BgpNetwork(VcoModel):
    cidr_ip: str
    prefix_length: Annotated[int, Alias("cidrPrefix")]


class BgpAggregation(VcoModel):
    prefix: str
    prefix_length: int
    as_set: bool = False
    summary_only: bool = False


BgpFilterMatchType = Literal["PREFIX", "COMMUNITY", "PREFIXV6"]
BgpFilterActionType = Literal["PERMIT", "DENY"]
BgpFilterActionValueType = Literal[
    "NONE", "COMMUNITY", "AS_PATH_PREPEND", "LOCAL_PREFERENCE", "METRIC"
]


class BgpFilterMatch(VcoModel):
    type_: Annotated[BgpFilterMatchType, Alias("type")] = "PREFIX"
    value: str
    exact_match: bool = True


class BgpFilterActionValue(VcoModel):
    type_: Annotated[BgpFilterActionValueType, Alias("type")] = "NONE"
    value: str = ""
    community_additive: bool = False


class BgpFilterAction(VcoModel):
    type_: Annotated[BgpFilterActionType, Alias("type")] = "DENY"
    values: list[BgpFilterActionValue] = Field(default_factory=list)


class BgpFilterRule(VcoModel):
    match: BgpFilterMatch
    action: BgpFilterAction


class BgpFilter(VcoModel):
    id_: Annotated[uuid.UUID, Alias("id")] = Field(default_factory=uuid.uuid4)
    name: str
    is_locked_by_profile: Annotated[bool, ProfileOnly] = False
    rules: list[BgpFilterRule] = Field(default_factory=list)
    version: str | MISSING = MISSING


class BgpNeighbor(VcoModel):
    neighbor_ip: str
    neighbor_as: Annotated[str, Alias("neighborAS")]
    inbound_filter: dict[str, list[str]] = Field(default_factory=lambda: {"ids": []})
    outbound_filter: dict[str, list[str]] = Field(default_factory=lambda: {"ids": []})
    max_hop: str = "1"
    local_ip: Annotated[str | MISSING, Alias("localIP")] = MISSING
    source_interface: str = ""
    uplink: bool = False
    allow_as: Annotated[bool, Alias("allowAS")] = False
    default_route: bool = False
    default_route_filter_ids: Annotated[
        Literal[""] | tuple[Literal["sameAsOutbound"]] | tuple[uuid.UUID] | list[Any],
        DefaultingScalar(
            Literal[""]
            | tuple[Literal["sameAsOutbound"]]
            | tuple[uuid.UUID]
            | list[Any],
            default=("sameAsOutbound",),
        ),
    ] = Field(default_factory=lambda: ("sameAsOutbound",))
    enable_bfd: bool = False
    keepalive: str = "60"
    holdtime: str = "180"
    connect: str = "120"
    enable_md5: bool = False
    md5_password: str = ""
    id_: Annotated[uuid.UUID, Alias("id")] = Field(default_factory=uuid.uuid4)


class BgpDefaultRoute(VcoModel):
    enabled: bool = False
    advertise: Literal["CONDITIONAL"] = "CONDITIONAL"
    default_route_filter_ids: Annotated[
        Literal[""] | tuple[Literal["sameAsOutbound"]] | tuple[uuid.UUID] | list[Any],
        DefaultingModel(
            tuple[Literal["sameAsOutbound"]]
            | Literal[""]
            | tuple[uuid.UUID]
            | list[Any],
            default_factory=lambda: ("sameAsOutbound",),
        ),
    ] = Field(default_factory=lambda: ("sameAsOutbound",))


class BgpOspfConfig(VcoModel):
    enabled: Annotated[bool, DefaultingScalar(bool, default=False)] = False
    metric: Annotated[int, DefaultingScalar(int, default=20)] = 20


def _none_to_empty_object(value: Any) -> Any:
    """Normalize nullable effective-configuration objects to their defaults."""
    return {} if value is None else value


class BgpBase(VcoModel):
    aggregation: list[BgpAggregation] = Field(default_factory=list)
    propagate_uplink: Annotated[bool, NoneIsFalse] = False
    overlay_prefix: Annotated[bool, NoneIsTrue] = True
    connected_routes: Annotated[bool, NoneIsTrue] = True
    ospf: Annotated[BgpOspfConfig, BeforeValidator(_none_to_empty_object)] = Field(
        default_factory=BgpOspfConfig
    )
    disable_as_path_carry_over: Annotated[
        bool, NoneIsFalse, Alias("disableASPathCarryOver")
    ] = False
    default_route: Annotated[
        BgpDefaultRoute, BeforeValidator(_none_to_empty_object)
    ] = Field(default_factory=BgpDefaultRoute)
    neighbors: Annotated[list[BgpNeighbor], KeyField("neighbor_ip")] = Field(
        default_factory=list
    )
    filters: Annotated[list[BgpFilter], KeyField("name")] = Field(default_factory=list)
    networks: Annotated[
        list[BgpNetwork], DefaultingModel(list[BgpNetwork], default_factory=list)
    ] = Field(default_factory=list)


class SegmentBgp(BgpBase):
    enabled: bool = False
    override: Annotated[bool, OverrideFlag] = True
    asn: Annotated[str | None, Alias("ASN")] = None
    router_id: str | None = None
    uplink_community: str | None = None
    is_edge: Annotated[bool, EdgeOnly] = True
    keepalive: Annotated[str, DefaultingScalar(str, default="60")] = "60"
    holdtime: Annotated[str, DefaultingScalar(str, default="180")] = "180"
    enable_graceful_restart: bool = False
    gr_restart_time: Annotated[str, Alias("restarttime")] = "120"
    gr_stale_path_time: Annotated[str, Alias("stalepathtime")] = "300"
    v6_detail: BgpBase = Field(default_factory=BgpBase)


class EcmpHashParams(VcoModel):
    src_ip: bool = True
    dest_ip: bool = True
    src_port: bool = True
    dest_port: bool = True
    protocol: bool = True


class EcmpLoadSharing(VcoModel):
    hash_params: EcmpHashParams


class SegmentEcmp(VcoModel):
    override: Annotated[bool, OverrideFlag] = True
    enabled_for_nsd: Annotated[bool, Alias("enabledForNSD")] = False
    enabled_for_interface: bool = False
    maximum_paths: int = 2
    load_sharing: EcmpLoadSharing
    as_path_multipath_relax: bool = False


class StaticRoute(VcoModel):
    destination: str
    netmask: str
    source_ip: str | None = None
    gateway: str
    cost: int = 0
    preferred: bool = True
    description: str | None = None
    cidr_prefix: str | int
    wan_interface: str = ""
    icmp_probe_logical_id: uuid.UUID | None = None
    vlan_id: int | None = None
    advertise: bool = True
    subinterface_id: int = -1


IcmpProbeType = Literal["NONE", "802.1Q"]


class ICMPProbe(VcoModel):
    destination_ip: str
    enabled: bool = True
    frequency_seconds: str | int
    logical_id: uuid.UUID
    name: str
    next_hop_ip: str | None = ""
    probe_type: IcmpProbeType = "NONE"
    source_ip: str | None = ""
    threshold: str | int
    vlan_id: int = -1

    @model_validator(mode="after")
    def _validate_vlan_source_nexthop(self) -> Self:
        has_vlan = self.vlan_id not in (-1, None)
        has_source = bool(self.source_ip)
        has_nexthop = bool(self.next_hop_ip)
        if any([has_vlan, has_source, has_nexthop]) and not all(
            [has_vlan, has_source, has_nexthop]
        ):
            missing = []
            if not has_vlan:
                missing.append("vlan_id")
            if not has_source:
                missing.append("source_ip")
            if not has_nexthop:
                missing.append("next_hop_ip")
            raise ValueError(
                f"When any of vlan_id/source_ip/next_hop_ip are set, all three "
                f"are required. Missing: {', '.join(missing)}"
            )
        return self


class ICMPResponder(VcoModel):
    enabled: bool = True
    ip_address: str
    name: str
    logical_id: uuid.UUID
    mode: str


NsdType = Literal["dataCenter", "nvsViaEdgeService"]


def _int_to_str(v: Any) -> Any:
    return str(v) if isinstance(v, int) else v


class NsdRouteBase(VcoModel):
    name: str | None = None
    advertise: bool = False
    cidr_ip: str
    cidr_prefix: Annotated[str, BeforeValidator(_int_to_str)]
    netmask: Annotated[str, Alias("netMask")]
    cost: int = 5
    destination: str
    is_static: bool = True
    nsd_logical_id: uuid.UUID
    nsd: str = ""
    ip_version: Literal["IPv4", "IPv6"] = "IPv4"
    preferred: bool = True


class NsdEdgeRoute(NsdRouteBase):
    nsd_type: Literal["nvsViaEdgeService"] = "nvsViaEdgeService"
    gateway: Literal["PRIMARY", "BACKUP0"] | None = None
    metric: int = 5
    tunnel_protocol: Literal["IPSEC"] = "IPSEC"


class NsdVcgRoute(NsdRouteBase):
    nsd_type: Literal["dataCenter"] = "dataCenter"
    next_hop: list[str] = Field(default_factory=list)
    gateway_logical_id: GatewayLogicalId | None = None
    gateway_name: str | MISSING = MISSING


def _nsd_route_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        for key in ("next_hop", "nextHop", "gateway_logical_id", "gatewayLogicalId"):
            if key in v:
                return "dataCenter"
        return "nvsViaEdgeService"
    return getattr(v, "nsd_type", "nvsViaEdgeService")


NsdRoute = Annotated[
    Annotated[NsdEdgeRoute, Tag("nvsViaEdgeService")]
    | Annotated[NsdVcgRoute, Tag("dataCenter")],
    Discriminator(_nsd_route_discriminator),
]


class SegmentRoutes(VcoModel):
    model_config = ConfigDict(extra="allow")

    icmp_probes: Annotated[list[ICMPProbe], KeyField("name")] = Field(
        default_factory=list
    )
    icmp_responders: Annotated[list[ICMPResponder], KeyField("name")] = Field(
        default_factory=list
    )
    static: list[StaticRoute] = Field(default_factory=list)
    static_v6: Annotated[list[StaticRoute], Alias("staticV6")] = Field(
        default_factory=list
    )
    nsd: list[NsdRoute] = Field(default_factory=list)
    nsd_v6: Annotated[list[NsdRoute], Alias("nsdV6")] = Field(default_factory=list)


class SegmentAuthentication(VcoModel):
    # Effective configurations use an empty object when no authentication
    # service is resolved. Keep that object round-trippable instead of
    # converting it to MISSING, because this model is also used for updates.
    ref: DeviceSettingsRef | MISSING = MISSING
    source_interface: str = ""


class NatRule(VcoModel):
    type_: Annotated[LanSideNatRuleType, Alias("type")]
    dest_cidr_ip: str | None = None
    dest_cidr_prefix: int | None = None
    dest_netmask: str | None = None
    src_cidr_ip: str | None = None
    src_cidr_prefix: int | None = None
    src_netmask: str | None = None
    description: str | None = None
    inside_cidr_ip: str
    inside_cidr_prefix: int
    inside_netmask: str
    inside_port: int = -1
    outside_cidr_ip: str
    outside_cidr_prefix: int
    outside_netmask: str
    outside_port: int = -1

    @field_validator("dest_cidr_prefix", "src_cidr_prefix", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        return None if v == "" else v


class NatDualRule(VcoModel):
    description: str | None = None
    src_inside_cidr_ip: str
    src_inside_cidr_prefix: int
    src_outside_cidr_ip: str
    src_outside_cidr_prefix: int
    dest_inside_cidr_ip: str
    dest_inside_cidr_prefix: int
    dest_outside_cidr_ip: str
    dest_outside_cidr_prefix: int
    src_inside_netmask: str
    src_outside_netmask: str
    dest_inside_netmask: str
    dest_outside_netmask: str


class SegmentNat(VcoModel):
    override: Annotated[bool, OverrideFlag] = True
    rules: list[NatRule] = Field(default_factory=list)
    dual_rules: list[NatDualRule] = Field(default_factory=list)


class NetflowCollectorFilter(VcoModel):
    logical_id: uuid.UUID
    name: str
    type_: Annotated[str, Alias("type")]


class NetflowCollector(VcoModel):
    allow_all_segment: bool = False
    filter_list: list[NetflowCollectorFilter] = Field(default_factory=list)
    logical_id: uuid.UUID
    name: str
    source_interface: str = ""
    type_: Annotated[str, Alias("type")]


type NetflowInterval = Annotated[int, Field(ge=60, le=300)]


class SegmentNetflowIntervals(VcoModel):
    flow_stats: NetflowInterval = 60
    flow_link_stats: NetflowInterval = 60
    tunnel_stats: NetflowInterval = 60
    vrf_table: NetflowInterval = 300
    application_table: NetflowInterval = 300
    interface_table: NetflowInterval = 300
    link_table: NetflowInterval = 300


class SegmentNetflow(VcoModel):
    enabled: bool
    override: Annotated[bool, OverrideFlag] = True
    version: int = 10
    collectors: list[NetflowCollector] = Field(default_factory=list)
    intervals: SegmentNetflowIntervals = Field(default_factory=SegmentNetflowIntervals)

    @model_validator(mode="after")
    def _validate_collectors(self) -> Self:
        if self.enabled and not self.collectors:
            raise ValueError(
                "At least one collector is required when netflow is enabled"
            )
        return self


SyslogSeverity = Literal[
    "INFO",
    "DEBUG",
    "ERR",
    "CRIT",
    "WARNING",
    "EMERG",
    "ALERT",
    "NOTICE",
]

SyslogRole = Literal["EDGE EVENT", "FIREWALL EVENT", "EDGE AND FIREWALL EVENT"]


class SyslogCollector(VcoModel):
    host: str
    port: int = 514
    roles: list[str] = Field(default_factory=lambda: ["EDGE AND FIREWALL EVENT"])
    all_segments: bool = True
    source_interface: str = ""
    protocol: Literal["UDP", "TCP"] = "UDP"
    severity: SyslogSeverity = "INFO"
    tag: str = ""


class SegmentSyslog(VcoModel):
    facility_code: str = "local0"
    enabled: bool = False
    collectors: list[SyslogCollector] = Field(default_factory=list)
    override: Annotated[bool, OverrideFlag] = True


class DnsProvider(VcoModel):
    # Effective configurations may expand a provider inline rather than return
    # its configuration ref. Preserve those VCO-specific fields for callers.
    model_config = ConfigDict(extra="allow")

    ref: DeviceSettingsRef | MISSING = MISSING


class LocalDomainItem(VcoModel):
    fqdn: str
    ipv4_address: list[str] = Field(default_factory=list)
    ipv6_address: list[str] = Field(default_factory=list)


class SegmentDns(VcoModel):
    primary_provider: DnsProvider | list[DnsProvider]
    backup_provider: DnsProvider | list[DnsProvider]
    private_providers: DnsProvider | list[DnsProvider]
    source_interface: str = ""
    override: Annotated[bool, OverrideFlag] = True
    local_dns: Annotated[list[LocalDomainItem], Alias("localDNS")] = Field(
        default_factory=list
    )


class CssProviderRef(VcoModel):
    logical_id: uuid.UUID | Literal[""] = ""
    ref: DeviceSettingsRef = "deviceSettings:css:provider"


class CssConfigBase(VcoModel):
    redirect: Literal["policy", "web", "all"] = "policy"


class CssConfigIpsecIkePropZscaler(VcoModel):
    protocol_version: Literal[2] = 2


class CssConfigZscalerIpsec(CssConfigBase):
    authentication_algorithm: Literal["SHA_1", "SHA_256"] = "SHA_1"
    encryption_algorithm: Literal["none", "AES_128_CBC", "AES_256_CBC"] = "none"
    tunneling_protocol: Literal["IPSEC"] = "IPSEC"
    ike_proposal: Annotated[CssConfigIpsecIkePropZscaler, Alias("IKEPROP")] = Field(
        default_factory=CssConfigIpsecIkePropZscaler
    )


class CssConfigIpsecIkeProp(VcoModel):
    dh_group: Annotated[Literal[2], Alias("DHGroup")] = 2
    pfs: Annotated[Literal[0], Alias("PFS")] = 0
    authentication_algorithm: Literal["Any"] = "Any"
    authentication_method: Literal["PSK"] = "PSK"
    encryption_algorithm: Literal["Any"] = "Any"
    id_protect: Literal[False] = False
    life_time_seconds: Literal[86400] = 86400
    protocol_version: Literal[1, 2] = 2


class CssConfigIpsecIpsecProp(VcoModel):
    life_time_seconds: int = 28800


class CssConfigGenericIpsec(CssConfigBase):
    authentication_algorithm: Literal["SHA_1", "SHA_256", "MD5"] = "SHA_1"
    encryption_algorithm: Literal["none", "AES_128_CBC", "AES_256_CBC"] = "none"
    tunneling_protocol: Literal["IPSEC"] = "IPSEC"
    ike_proposal: Annotated[CssConfigIpsecIkeProp, Alias("IKEPROP")] = Field(
        default_factory=CssConfigIpsecIkeProp
    )
    ipsec_proposal: Annotated[CssConfigIpsecIpsecProp, Alias("IPSECPROP")] = Field(
        default_factory=CssConfigIpsecIpsecProp
    )


CssConfigIpsec = CssConfigZscalerIpsec | CssConfigGenericIpsec


class CssGreProp(VcoModel):
    keepalive_interval_secs: int = 5
    keepalive_retries: int = 3
    links: list[str] = Field(default_factory=list)


class CssConfigGre(CssConfigBase):
    tunneling_protocol: Literal["GRE"] = "GRE"
    greprop: Annotated[CssGreProp, Alias("GREPROP")] = Field(default_factory=CssGreProp)


CssConfig = CssConfigGre | CssConfigZscalerIpsec | CssConfigGenericIpsec


class CssIpsecSiteData(VcoModel):
    ike_id: str
    psk: str
    ike_id_type: Literal["FQDN"] = "FQDN"
    psk_type: Literal["alpha"] = "alpha"


class CssIpsecSite(VcoModel):
    logical_id: uuid.UUID | MISSING = MISSING
    data: CssIpsecSiteData


class CssGreSiteAddressing(VcoModel):
    internal_router_ip: str
    internal_router_mask: str
    internal_zen_ip: str
    internal_zen_mask: str


class CssGreSiteData(VcoModel):
    use_custom_source_ip: bool = False
    custom_source_ip: str = ""
    link_internal_logical_id: uuid.UUID
    primary_addressing: CssGreSiteAddressing
    secondary_addressing: CssGreSiteAddressing


class CssGreSite(VcoModel):
    logical_id: uuid.UUID | MISSING = MISSING
    data: CssGreSiteData


class SegmentCss(VcoModel):
    enabled: bool | MISSING = MISSING
    override: Annotated[bool, OverrideFlag] = False
    provider: CssProviderRef | MISSING = MISSING
    config: CssConfig | MISSING = MISSING
    sites: Annotated[list[CssIpsecSite] | list[CssGreSite], EdgeOnly] = Field(
        default_factory=list
    )


class DeviceSegment(VcoModel):
    bfd: SegmentBfd | MISSING = MISSING
    edge_direct: SegmentEdgeDirect | MISSING = MISSING
    hub_interconnect: SegmentHubInterconnect | MISSING = MISSING
    handoff_controllers: SegmentHandoffGateways | MISSING = MISSING
    handoff_gateways: SegmentHandoffGateways | MISSING = MISSING
    segment: SegmentMetadata
    vrrp: SegmentVrrp | MISSING = MISSING
    vpn: Annotated[SegmentVpn, ProfileOnly] = Field(default_factory=SegmentVpn)
    multicast: SegmentMulticast | MISSING = MISSING
    ospf: SegmentOspf | MISSING = MISSING
    bgp: SegmentBgp | MISSING = MISSING
    ecmp: SegmentEcmp | MISSING = MISSING
    routes: SegmentRoutes | MISSING = Field(default_factory=SegmentRoutes)
    authentication: SegmentAuthentication | MISSING = MISSING
    nat: SegmentNat | MISSING = MISSING
    netflow: SegmentNetflow | MISSING = MISSING
    syslog: SegmentSyslog | MISSING = MISSING
    dns: SegmentDns | MISSING = MISSING
    css: SegmentCss | MISSING = MISSING
