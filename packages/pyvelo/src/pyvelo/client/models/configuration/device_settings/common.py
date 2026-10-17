from typing import Annotated, Literal

from pydantic import Field
from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel
from pyvelo.core.fields import DefaultingScalar

RadioMode = Literal["n", "ac", "ax"]
RadioBand = Literal["2.4", "5"]

IPv4AddressingType = Literal["STATIC", "DHCP", "PPPOE"]
IPv6AddressingType = Literal["STATIC", "DHCP_STATELESS", "DHCP_STATEFUL", "DHCP_PD"]

SnmpV3AuthenticationAlgorithm = Literal[
    "MD5", "SHA1", "SHA224", "SHA256", "SHA384", "SHA512"
]
SnmpV3EncryptionAlgorithm = Literal["AES", "DES"]

WanOverlay = Literal["DISABLED", "AUTO_DISCOVERED", "USER_DEFINED"]

ReversePathFilter = Literal["SPECIFIC", "LOOSE", "DISABLED"]

OspfMode = Literal["BCAST", "P2P"]
OspfRouteAction = Literal["LEARN", "IGNORE", "ADVERTISE"]
AdvertiseDefaultRoutes = Literal["OE1", "OE2", "NONE"]
AdvertiseDefaultRoutesMethod = Literal["ALWAYS", "CONDITIONAL", "NONE"]
OspfExternalRouteType = Literal["E1", "E2"]
OspfAreaType = Literal["normal"]

LanSideNatRuleType = Literal["source", "destination"]


class IgmpConfiguration(VcoModel):
    enabled: bool = False
    type_: Annotated[Literal["IGMP_V2"], Alias("type")] = "IGMP_V2"


class PimConfiguration(VcoModel):
    enabled: bool = False
    type_: Annotated[Literal["PIM_SM"], Alias("type")] = "PIM_SM"


class MulticastConfiguration(VcoModel):
    igmp: IgmpConfiguration = Field(default_factory=IgmpConfiguration)
    igmp_host_query_interval_seconds: int | None = None
    igmp_max_query_response: int | None = None
    pim: PimConfiguration = Field(default_factory=PimConfiguration)
    pim_hello_timer_seconds: int | None = None
    pim_keep_alive_timer_seconds: int | None = None
    pim_prune_interval_seconds: int | None = None


class PrefixDelegationEntry(VcoModel):
    pool_name: Annotated[str, Alias("poolname")]
    prefix: str
    prefix_length: Annotated[str | int, Alias("prefixLen")]
    target_prefix_length: Annotated[str | int, Alias("targetPrefixLen")]


class PrefixDelegation(VcoModel):
    enabled: bool = True
    pd_list: Annotated[list[PrefixDelegationEntry], Alias("pdlist")] = Field(
        default_factory=list
    )


DhcpOptionDataType = Literal[
    "ip",
    "int",
    "ipv4",
    "ipv6",
    "uint",
    "name",
    "text",
    "bytes",
    "number",
    "enum",
    "option",
    "dnssearchname",
    "domain",
]


class DhcpOptionMetadata(VcoModel):
    data_type: DhcpOptionDataType
    data_length: int | MISSING = MISSING
    description: str | MISSING = MISSING
    display: bool | MISSING = MISSING
    list_: Annotated[bool | MISSING, Alias("list")] = MISSING
    name: str | MISSING = MISSING
    option: int | MISSING = MISSING


class DhcpServerOption(VcoModel):
    option: int
    value: str | list[str] | None
    type_: Annotated[str, Alias("type")]
    metadata: Annotated[DhcpOptionMetadata | MISSING, Alias("metaData")] = MISSING


PortDuplex = Literal["FULL", "HALF"]

PortSpeed = Literal["10M", "100M", "1000M", "2500M"]

ProbeInterval = Literal["1", "3", "5", "10"]


class InterfaceL2(VcoModel):
    autonegotiation: bool = True
    speed: Annotated[PortSpeed, DefaultingScalar(PortSpeed, default="100M")] = "100M"
    duplex: Annotated[PortDuplex, DefaultingScalar(PortDuplex, default="FULL")] = "FULL"
    mtu: Annotated[int, Alias("MTU")] = 1500
    los_detection: bool = False
    probe_interval: ProbeInterval | None = "3"


class MacBypassEntry(VcoModel):
    address: str
    description: str = ""


class RadiusAuthentication(VcoModel):
    enabled: bool = False
    mac_bypass: list[MacBypassEntry] = Field(default_factory=list)
    acl_check: bool = False


SubInterfaceType = Literal["SECONDARY_IP", "SUB_INTERFACE"]

EdgeModel = Literal[
    "edge500",
    "edge505",
    "edge5X0",
    "edge510",
    "edge515",
    "edge510lte",
    "edge520",
    "edge520-v",
    "edge540",
    "edge540-poe",
    "edge540-v",
    "edge6X0",
    "edge610",
    "edge610lte",
    "edge610-lte",
    "edge620",
    "me6200",
    "edge640",
    "edge680",
    "edge710",
    "edge7105g",
    "edge710-5g",
    "edge7X0",
    "edge720",
    "edge740",
    "edge840",
    "edge1000",
    "edge1000qat",
    "edge3X00",
    "edge3X10",
    "edge3400",
    "edge3800",
    "edge3810",
    "edge4100",
    "edge5100",
    "virtual",
    "vc_dmz_gateway",
    "cloudEdge",
    "VIRTUAL_EDGE",
    "VC_VMDK",
    "VC_KVM_GUEST",
    "VC_XEN_AWS",
    "xen",
    "ec2",
    "azure",
    "aliyun",
    "gce",
    "hyperv",
    "vmware",
    "kvm",
    "aws",
    "PW_DEV",
    "edge-nexcom",
    "VE1000",
    "EDGE500",
    "edge510-n",
    "edge610-n",
    "edge620-n",
    "edge640-n",
    "edge680-n",
]

DeviceSettingsRef = Literal[
    "deviceSettings:segment:netflowFilters",
    "deviceSettings:css:provider",
    "deviceSettings:segment",
    "deviceSettings:edgeDirectNvs:provider",
    "deviceSettings:vpn:edgeHub",
    "deviceSettings:vpn:edgeHubCluster",
    "deviceSettings:vpn:dataCenter",
    "deviceSettings:dataCenter",
    "deviceSettings:backHaulEdge",
    "deviceSettings:css:site",
    "deviceSettings:tacacs",
    "deviceSettings:vnfs:edge",
    "deviceSettings:securityVnf:license",
    "deviceSettings:securityVnf:service",
    "deviceSettings:vnfs:vnfImage",
    "deviceSettings:dns:primaryProvider",
    "deviceSettings:dns:privateProviders",
    "deviceSettings:dns:backupProvider",
    "deviceSettings:authentication",
    "deviceSettings:edgeDirectNvs:site",
    "deviceSettings:lan:allocation",
    "deviceSettings:segment:netflowCollectors",
    "deviceSettings:cciSubscription",
    "deviceSettings:webProxy:provider",
    "deviceSettings:wssIntegration",
    "deviceSettings:zscaler:iaasSubscription",
    "deviceSettings:zscaler:location",
    "deviceSettings:zscaler:subLocation",
    "deviceSettings:zscaler:mtgreSite",
    "deviceSettings:interface:prefixTag",
]


class ProviderRef(VcoModel):
    ref: DeviceSettingsRef
