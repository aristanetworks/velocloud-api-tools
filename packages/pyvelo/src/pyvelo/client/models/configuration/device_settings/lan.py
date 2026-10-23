import uuid
from typing import Annotated, Any, Literal

from pydantic import MISSING, BeforeValidator, Discriminator, Field, SerializeAsAny, Tag

from pyvelo.core import Alias, EdgeOnly, NoneIsFalse, ProfileOnly, VcoModel
from pyvelo.core.fields import (
    DefaultingScalar,
    EdgeLocal,
    KeyField,
    OverrideFlag,
    Resolvable,
)
from pyvelo.core.identity import SegmentIdentity

from .common import (
    DhcpServerOption,
    InterfaceL2,
    IPv6AddressingType,
    MulticastConfiguration,
    RadiusAuthentication,
)

VisibilityMode = Literal["IP", "MAC"]


class EdgeManagementTraffic(VcoModel):
    override: Annotated[bool, OverrideFlag] = True
    source_interface: Annotated[str, EdgeOnly] = ""


class EdgeClientVisibility(VcoModel):
    mode: VisibilityMode = "MAC"
    override: Annotated[bool, OverrideFlag] = True


DropUntagged = Literal["drop"]

LanPortMode = Literal["access", "trunk"]


LanInterfaceType = Literal["wired", "wireless"]


class LanInterfaceBase(VcoModel):
    override: Annotated[bool, OverrideFlag] = True
    name: str
    cwp: bool = False
    disabled: bool = False
    l2: InterfaceL2 = Field(default_factory=InterfaceL2)
    vlan_ids: list[int] = Field(default_factory=list)


WirelessSecurityMode = Literal["Open", "WPA2Enterprise", "WPA2Personal"]


class AllowedMac(VcoModel):
    mac_address: Annotated[str, Alias("address")]
    description: str = ""


class MacAllowList(VcoModel):
    enabled: bool = False
    allowed_macs: list[AllowedMac] = Field(default_factory=list)
    enable_mac_filtering_for_ap_probes: Annotated[
        bool, Alias("enableMACFilteringForAPProbes")
    ] = False


class LanWiredInterface(LanInterfaceBase):
    port_mode: Annotated[
        LanPortMode, DefaultingScalar(LanPortMode, default="access")
    ] = "access"
    space: str = "Corporate Network"
    type_: Annotated[Literal["wired"], Alias("type")] = "wired"
    untagged_vlan: DropUntagged | int | None = (
        "drop"  # may be None if interface is disabled
    )


def _none_to_vcsecret(v: Any) -> Any:
    return "vcsecret" if v is None else v


class LanWirelessInterface(LanInterfaceBase):
    type_: Annotated[Literal["wireless"], Alias("type")] = "wireless"
    mac_allowlist: MacAllowList = Field(default_factory=MacAllowList)
    radius_acl_check: bool = False
    broadcast_ssid: bool = True
    passphrase: Annotated[str, BeforeValidator(_none_to_vcsecret)]
    ssid: str
    security_mode: WirelessSecurityMode = "WPA2Personal"


def _lan_interface_discriminator(v: Any) -> str:
    name = v.get("name", "") if isinstance(v, dict) else getattr(v, "name", "")
    return "wireless" if name.startswith("WLAN") else "wired"


LanInterface = Annotated[
    Annotated[LanWiredInterface, Tag("wired")]
    | Annotated[LanWirelessInterface, Tag("wireless")],
    Discriminator(_lan_interface_discriminator),
]


class EdgeLanDhcpRelay(VcoModel):
    enabled: bool = False
    servers: list[str] = Field(default_factory=list)
    source_from_secondary_ip: bool = False


def _none_to_default_lease_time(v: Any) -> Any:
    return 86400 if v is None else v


class EdgeLanDhcp(VcoModel):
    dhcp_relay: EdgeLanDhcpRelay = Field(default_factory=EdgeLanDhcpRelay)
    enabled: bool = True
    lease_time_seconds: Annotated[int, BeforeValidator(_none_to_default_lease_time)] = (
        86400
    )
    options: list[DhcpServerOption] = Field(default_factory=list)
    override: Annotated[bool, OverrideFlag] = True


class LanFixedIP(VcoModel):
    mac_address: str
    lan_ip: str
    description: str = ""


def _none_empty_str_to_true(v: Any) -> Any:
    return True if v is None or (isinstance(v, str) and v == "") else v


class LanOspf(VcoModel):
    enabled: bool = False
    override: Annotated[bool, OverrideFlag] = True
    passive_interface: Annotated[bool, BeforeValidator(_none_empty_str_to_true)] = True
    area: int | str = ""


class LanDhcpRelayV6(VcoModel):
    enabled: bool = False
    servers: list[str] = Field(default_factory=list)


def _none_to_default_netmask(v: Any) -> Any:
    return "ffff:ffff:ffff:ffff:0000:0000:0000:0000" if v is None else v


class LanAddressingV6(VcoModel):
    cidr_ip: Annotated[str | None, EdgeLocal] = None
    cidr_prefix: Annotated[int | None, EdgeLocal] = 64
    interface_address: str | None = None
    netmask: Annotated[str, BeforeValidator(_none_to_default_netmask)] = (
        "ffff:ffff:ffff:ffff:0000:0000:0000:0000"
    )
    tag_logical_id: uuid.UUID | None = None
    type_: Annotated[IPv6AddressingType, Alias("type")] = "STATIC"


class LanPrefixDelegationEntry(VcoModel):
    pool_name: Annotated[str, Alias("poolname")]
    prefix: str
    prefix_length: Annotated[str | int, Alias("prefixLen")]
    target_prefix_length: Annotated[str | int, Alias("targetPrefixLen")]


class LanPrefixDelegation(VcoModel):
    enabled: bool = True
    pd_list: Annotated[list[LanPrefixDelegationEntry], Alias("pdlist")] = Field(
        default_factory=list
    )


class LanDhcpServerV6(VcoModel):
    base_dhcp_address: Annotated[int, Alias("baseDhcpAddr")] = 1
    dhcp_relay: LanDhcpRelayV6 = Field(default_factory=LanDhcpRelayV6)
    enabled: bool = True
    fixed_ip: Annotated[list[LanFixedIP], EdgeLocal] = Field(default_factory=list)
    lease_time_seconds: int = 86400
    dhcp_address_count: Annotated[int, Alias("numDhcpAddr")] = 10
    options: list[DhcpServerOption] = Field(default_factory=list)
    prefix_delegation: LanPrefixDelegation = Field(default_factory=LanPrefixDelegation)
    static_reserved: int = 10


class LanV6Details(VcoModel):
    addressing: LanAddressingV6 = Field(default_factory=LanAddressingV6)
    advertise: bool = True
    bind_edge_address: Literal[False] = False
    dhcp_server: LanDhcpServerV6 = Field(default_factory=LanDhcpServerV6)
    ospf: LanOspf = Field(default_factory=LanOspf)
    override: Annotated[bool, OverrideFlag] = True


class LanSecondaryIpAddress(VcoModel):
    cidr_ip: str
    cidr_prefix: int
    netmask: str


class LanSecondaryIP(VcoModel):
    addressing: LanSecondaryIpAddress
    advertise: bool = True
    override: bool = True
    ping_response: bool = True


def _none_empty_str_to_false(v: Any) -> Any:
    return False if v is None or (isinstance(v, str) and v == "") else v


def _none_to_default_multicast(v: Any) -> Any:
    return MulticastConfiguration() if v is None else v


class LanNetwork(VcoModel):
    advertise: Annotated[bool, NoneIsFalse] = True
    disabled: bool = False
    angular_update: bool = True
    base_dhcp_address: Annotated[int, Alias("baseDhcpAddr")] = 13
    bind_edge_address: bool = False
    cidr_ip: Annotated[str | None, EdgeLocal] = None
    cidr_prefix: Annotated[int | None, EdgeLocal] = None
    cost: int = 10
    dhcp: EdgeLanDhcp = Field(default_factory=EdgeLanDhcp)
    disable_ipv4: Annotated[bool, Alias("disableV4")] = False
    disable_ipv6: Annotated[bool, Alias("disableV6")] = True
    dns_proxy: bool = True
    fixed_ips: Annotated[list[LanFixedIP], Alias("fixedIp"), EdgeLocal] = Field(
        default_factory=list
    )
    interface_address: Annotated[str | None, EdgeLocal] = None
    interfaces: list[str] = Field(default_factory=list)
    multicast: Annotated[
        MulticastConfiguration, BeforeValidator(_none_to_default_multicast)
    ] = Field(default_factory=MulticastConfiguration)
    name: str
    netmask: Annotated[str, EdgeLocal] = "255.255.255.0"
    dhcp_address_count: Annotated[int, Alias("numDhcpAddr")] = 242
    ospf: LanOspf = Field(default_factory=LanOspf)
    override: Annotated[bool, OverrideFlag] = False
    ping_response: bool | None = True
    radius_authentication: RadiusAuthentication = Field(
        default_factory=RadiusAuthentication
    )
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0
    static_reserved: int = 10
    tag_logical_id: uuid.UUID | None = None
    ipv6_details: Annotated[LanV6Details, Alias("v6Detail")] = Field(
        default_factory=LanV6Details
    )
    vlan_id: int
    vnf_insertion: Annotated[bool, BeforeValidator(_none_empty_str_to_false)] = False
    secondary_ips: Annotated[
        list[LanSecondaryIP] | MISSING, Alias("secondaryIp"), ProfileOnly, EdgeLocal
    ] = MISSING


class LanManagement(VcoModel):
    cidr_ip: Annotated[str | None, EdgeLocal] = "192.168.1.1"
    cidr_prefix: int = 32


class LanSettings(VcoModel):
    management: LanManagement = Field(default_factory=LanManagement)
    management_traffic: EdgeManagementTraffic = Field(
        default_factory=EdgeManagementTraffic
    )
    visibility: EdgeClientVisibility | MISSING = MISSING
    interfaces: Annotated[
        list[SerializeAsAny[LanInterface]], EdgeOnly, KeyField("name")
    ] = Field(default_factory=list)
    networks: Annotated[list[LanNetwork], KeyField("vlan_id")] = Field(
        default_factory=list
    )
