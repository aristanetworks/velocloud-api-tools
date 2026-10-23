import uuid
from typing import Annotated, Any, Literal

from pydantic import (
    MISSING,
    BeforeValidator,
    Discriminator,
    Field,
    Tag,
    model_validator,
)

from pyvelo.core import (
    Alias,
    ContextDefault,
    EdgeLocal,
    EdgeOnly,
    NoneIsFalse,
    NoneIsZero,
    Normalizer,
    OverrideFlag,
    Resolvable,
    VcoModel,
)
from pyvelo.core.identity import SegmentIdentity

from ..common import OverlayAddressPreferenceType
from .common import (
    DhcpServerOption,
    InterfaceL2,
    IPv4AddressingType,
    IPv6AddressingType,
    MulticastConfiguration,
    OspfMode,
    OspfRouteAction,
    PrefixDelegation,
    RadiusAuthentication,
    ReversePathFilter,
    SubInterfaceType,
    WanOverlay,
)


class RoutedInterfaceAddressing(VcoModel):
    type_: Annotated[IPv4AddressingType, Alias("type")] = "DHCP"
    username: str | None | MISSING = MISSING
    password: str | None | MISSING = MISSING
    cidr_ip: Annotated[str | None, EdgeLocal] = None
    cidr_prefix: Annotated[Literal[""] | int | None, EdgeLocal] = None
    netmask: Annotated[str | None, EdgeLocal] = None
    gateway: Annotated[str | None, EdgeLocal] = None

    @model_validator(mode="after")
    def _strip_non_pppoe_credentials(self):
        if self.type_ != "PPPOE":
            self.username = MISSING
            self.password = MISSING
        return self


class RoutedInterfaceAddressingV6(VcoModel):
    type_: Annotated[IPv6AddressingType, Alias("type")] = "DHCP_STATELESS"
    username: str | None | MISSING = MISSING
    password: str | None | MISSING = MISSING
    cidr_ip: str | None = None
    cidr_prefix: int | None = None
    netmask: str | None = None
    gateway: str | None = None
    interface_address: str | None = None
    tag: str | None = None
    tag_logical_id: uuid.UUID | None = None


class OspfRouteFilter(VcoModel):
    cidr_ip: str
    cidr_prefix: str
    exact_match: bool = True
    action: OspfRouteAction


class OspfRoute(VcoModel):
    default_action: OspfRouteAction
    filters: list[OspfRouteFilter] = Field(default_factory=list)


def _normalize_ospf_area(v: Any) -> Any:
    if v in ("", "0", 0, [], [0]):
        return [0]
    if isinstance(v, (int, str)):
        return [v]
    return v


class RoutedInterfaceOspfBase(VcoModel):
    enabled: bool = False
    area: Annotated[
        list[int] | list[str],
        BeforeValidator(_normalize_ospf_area),
        Normalizer(lambda v: [0] if v in ("", "0", 0, [], [0]) else v),
    ] = Field(default_factory=lambda: [0])
    hello_timer: int = 10
    dead_timer: int = 40
    mode: OspfMode = "BCAST"
    cost: int = 1
    mtu: Annotated[int, Alias("MTU")] = 1380
    passive: bool = False
    inbound_route_learning: OspfRoute = Field(
        default_factory=lambda: OspfRoute(default_action="LEARN")
    )
    outbound_route_advertisement: OspfRoute = Field(
        default_factory=lambda: OspfRoute(default_action="IGNORE")
    )
    exclusion_routes: list[OspfRouteFilter] = Field(default_factory=list)
    enable_bfd: bool = False


class RoutedInterfaceOspf(RoutedInterfaceOspfBase):
    authentication: bool = False
    authentication_id: Annotated[int | str, Alias("authId")] = 0
    authentication_passphrase: Annotated[str, Alias("authPassphrase")] = ""
    md5_authentication: bool = False


class RoutedInterfaceDhcpServerV6(VcoModel):
    base_dhcp_address: Annotated[int, Alias("baseDhcpAddr")] = 2
    enabled: bool = False
    lease_time_seconds: int = 3600
    dhcp_address_count: Annotated[int, Alias("numDhcpAddr")] = 10
    options: list[DhcpServerOption] | None = None
    static_reserved: int = 10
    prefix_delegation: PrefixDelegation = Field(default_factory=PrefixDelegation)


class RoutedInterfaceClientPrefixDelegation(VcoModel):
    enabled: bool = False
    tag: str | None = None
    tag_logical_id: uuid.UUID | None = None


class RouterAdvertisementHostSettings(VcoModel):
    mtu: bool = True
    default_routes: bool = True
    specific_routes: bool = True
    nd6_timers: bool = True


class RoutedInterfaceV6Detail(VcoModel):
    addressing: RoutedInterfaceAddressingV6 = Field(
        default_factory=RoutedInterfaceAddressingV6
    )
    client_prefix_delegation: RoutedInterfaceClientPrefixDelegation = Field(
        default_factory=RoutedInterfaceClientPrefixDelegation
    )
    dhcp_server: Annotated[RoutedInterfaceDhcpServerV6, EdgeOnly, EdgeLocal] = Field(
        default_factory=RoutedInterfaceDhcpServerV6
    )
    wan_overlay: WanOverlay | None = "AUTO_DISCOVERED"
    trusted: bool = False
    rpf: ReversePathFilter = "SPECIFIC"
    router_advertisement_host_settings: RouterAdvertisementHostSettings | MISSING = (
        MISSING
    )
    advertise: bool = False
    nat_direct: bool = True
    # IPv6 OSPF lacks the auth fields that the v4 variant carries.
    ospf: RoutedInterfaceOspfBase = Field(default_factory=RoutedInterfaceOspfBase)


CellularNetworks = Literal["ATT", "SPRINT", "VERIZON", "VODAFONE", "TELSTRA", "GENERIC"]
CellularIpType = Literal["IPv4", "IPv4v6"]


class RoutedInterfaceCellular(VcoModel):
    sim_pin: str
    network: CellularNetworks | Literal[""] = ""
    apn: str
    username: str
    password: str | int
    ip_type: CellularIpType | MISSING = MISSING


SfpType = Literal["dsl", "gpon", "standard"]


class Adsl2Properties(VcoModel):
    pvc: str
    vpi: str
    vci: str
    vlan: str
    vlanidtx: str
    vlanidrx: str
    vlanoptx: str
    vlanoprx: str


class AdslSettings(VcoModel):
    mode: Literal["adsl2"]
    properties: Adsl2Properties


Vdsl2Profile = Literal["17a", "30a"]


class Vdsl2Properties(VcoModel):
    profile: Vdsl2Profile


class VdslSettings(VcoModel):
    mode: Literal["vdsl2"]
    properties: Vdsl2Properties


DslSettings = Annotated[
    AdslSettings | VdslSettings, Discriminator(discriminator="mode")
]

GponLocationMode = Literal["ASCII", "HEX"]


class GponSettings(VcoModel):
    subscriber_location_id: str
    subscriber_location_mode: GponLocationMode


class RoutedInterfaceDhcpRelay(VcoModel):
    servers: Annotated[list[str], EdgeOnly] = Field(default_factory=list)


class RoutedInterfaceDhcpServer(VcoModel):
    base_dhcp_address: Annotated[
        int | Literal[""], Alias("baseDhcpAddr"), ContextDefault(profile=MISSING)
    ] = 13
    enabled: Annotated[bool, EdgeOnly] = True
    lease_time_seconds: Annotated[int, EdgeOnly] = 3600
    dhcp_address_count: Annotated[
        int, Alias("numDhcpAddr"), NoneIsZero, ContextDefault(profile=MISSING)
    ] = 10
    options: Annotated[list[DhcpServerOption] | None, EdgeOnly] = Field(
        default_factory=list
    )
    static_reserved: Annotated[int, EdgeOnly] = 10
    dhcp_relay: RoutedInterfaceDhcpRelay | MISSING = MISSING


LagMode = Literal["Active", "Passive"]


class Lacp(VcoModel):
    mode: LagMode = "Active"
    sys_priority: int = 65535
    lacp_rate: int = 0


class SlaveInterface(VcoModel):
    name: str
    port_priority: int | MISSING = MISSING


def _none_is_missing_dhcp_server(v: Any) -> Any:
    return MISSING if v is None else v


class RoutedInterfaceBase(VcoModel):
    advertise: bool = False
    disabled: bool = False
    disable_ipv4: Annotated[bool, Alias("disableV4")] = False
    disable_ipv6: Annotated[bool, Alias("disableV6")] = True
    addressing: RoutedInterfaceAddressing = Field(
        default_factory=RoutedInterfaceAddressing
    )
    nat_direct: bool = True
    ping_response: bool = True
    trusted: bool = False
    rpf: ReversePathFilter = "SPECIFIC"
    ospf: RoutedInterfaceOspf = Field(default_factory=RoutedInterfaceOspf)
    vlan_id: int | None = None
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = -1
    v6_detail: RoutedInterfaceV6Detail = Field(default_factory=RoutedInterfaceV6Detail)
    multicast: MulticastConfiguration = Field(default_factory=MulticastConfiguration)
    override: Annotated[bool, OverrideFlag] = False
    dhcp_server: Annotated[
        RoutedInterfaceDhcpServer | MISSING,
        BeforeValidator(_none_is_missing_dhcp_server),
        EdgeOnly,
        EdgeLocal,
    ] = MISSING
    description: Annotated[str | MISSING, EdgeOnly, EdgeLocal] = MISSING
    dns_proxy: bool = False
    edge_to_edge_encryption: bool = True


class RoutedSubInterface(RoutedInterfaceBase):
    subinterface_id: int
    subinterface_type: SubInterfaceType | MISSING


class RoutedInterfaceCommon(RoutedInterfaceBase):
    name: str
    overlay_preference: OverlayAddressPreferenceType = "IPv4"
    wan_overlay: WanOverlay = "AUTO_DISCOVERED"
    encrypt_overlay: bool = True
    l2: InterfaceL2 = Field(default_factory=InterfaceL2)
    radius_authentication: RadiusAuthentication = Field(
        default_factory=RadiusAuthentication
    )
    underlay_accounting: bool = True
    subinterfaces: list[RoutedSubInterface] | MISSING = MISSING


class RoutedInterfaceGe(RoutedInterfaceCommon):
    slave: Annotated[bool | MISSING, EdgeOnly] = MISSING
    evdsl_modem_attached: Annotated[bool, NoneIsFalse] = False


class RoutedInterfaceSfpStandard(RoutedInterfaceCommon):
    sfp_type: Literal["standard"] | MISSING = MISSING
    slave: Annotated[bool | MISSING, EdgeOnly] = MISSING
    evdsl_modem_attached: Annotated[bool, NoneIsFalse] = False


class RoutedInterfaceSfpDsl(RoutedInterfaceCommon):
    sfp_type: Literal["dsl"]
    dsl_settings: DslSettings
    slave: Annotated[bool | MISSING, EdgeOnly] = MISSING
    evdsl_modem_attached: Annotated[bool, NoneIsFalse] = False


class RoutedInterfaceSfpGpon(RoutedInterfaceCommon):
    sfp_type: Literal["gpon"]
    gpon_settings: GponSettings
    slave: Annotated[bool | MISSING, EdgeOnly] = MISSING
    evdsl_modem_attached: Annotated[bool, NoneIsFalse] = False


def _sfp_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        sfp_type = v.get("sfpType", v.get("sfp_type"))
    elif hasattr(v, "sfp_type"):
        sfp_type = v.sfp_type
    else:
        sfp_type = None

    if sfp_type == "dsl":
        return "dsl"
    if sfp_type == "gpon":
        return "gpon"
    return "standard"


RoutedInterfaceSfp = Annotated[
    Annotated[RoutedInterfaceSfpStandard, Tag("standard")]
    | Annotated[RoutedInterfaceSfpDsl, Tag("dsl")]
    | Annotated[RoutedInterfaceSfpGpon, Tag("gpon")],
    Discriminator(_sfp_discriminator),
]


class RoutedInterfaceCell(RoutedInterfaceCommon):
    cellular: RoutedInterfaceCellular | None | MISSING = MISSING


class RoutedInterfaceLag(RoutedInterfaceCommon):
    lacp: Lacp = Field(default_factory=Lacp)
    slave_interfaces: list[SlaveInterface] = Field(default_factory=list)
    evdsl_modem_attached: Annotated[bool, NoneIsFalse] = False


class RoutedInterfaceUsb(RoutedInterfaceCommon):
    pass


def _routed_interface_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        name = v.get("name", "")
    elif hasattr(v, "name"):
        name = v.name
    else:
        name = ""

    if isinstance(name, str):
        prefix = name.rstrip("0123456789")
        if prefix == "LAG":
            return "lag"
        elif prefix == "SFP":
            return _sfp_discriminator(v)
        elif prefix in ("CELL", "CELL_"):
            return "cell"
        elif prefix == "USB":
            return "usb"
    return "ge"


RoutedInterface = Annotated[
    Annotated[RoutedInterfaceGe, Tag("ge")]
    | Annotated[RoutedInterfaceSfpStandard, Tag("standard")]
    | Annotated[RoutedInterfaceSfpDsl, Tag("dsl")]
    | Annotated[RoutedInterfaceSfpGpon, Tag("gpon")]
    | Annotated[RoutedInterfaceCell, Tag("cell")]
    | Annotated[RoutedInterfaceLag, Tag("lag")]
    | Annotated[RoutedInterfaceUsb, Tag("usb")],
    Discriminator(_routed_interface_discriminator),
]
