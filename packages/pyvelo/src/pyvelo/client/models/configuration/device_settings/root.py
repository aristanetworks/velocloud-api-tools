import uuid
from typing import Annotated, Any, Literal, get_args

from pydantic import MISSING, BeforeValidator, Field, PrivateAttr, SerializeAsAny

from pyvelo.core import (
    Alias,
    ContextDefault,
    EdgeOnly,
    NoneIsFalse,
    NoneIsMissing,
    Positional,
    ProfileOnly,
    VcoModel,
)
from pyvelo.core.fields import KeyField, OverrideFlag

from ..common import (
    copy_profile_segments,
    find_segment,
    get_or_create_segment,
    profile_segment_id,
    profile_segment_name,
)
from .common import (
    DeviceSettingsRef,
    RadioBand,
    RadioMode,
    SnmpV3AuthenticationAlgorithm,
    SnmpV3EncryptionAlgorithm,
)
from .lan import LanInterface, LanSettings
from .routed_interface import RoutedInterface
from .segment import DeviceSegment


class EdgeHaNode(VcoModel):
    serial_number: str | None = None
    instance_id: Literal[1, 2]


# for edge, most fields are filled
# for profile, this is just an empty object - all fields missing
class EdgeHaSettings(VcoModel):
    enabled: Annotated[bool, EdgeOnly] = False
    interface: Annotated[str, EdgeOnly] = "GE1"
    unique_lan_macs: Annotated[bool, Alias("vmacoverride"), EdgeOnly] = False
    unique_wan_macs: Annotated[bool, Alias("wanmacoverride"), EdgeOnly] = False
    nodes: Annotated[list[EdgeHaNode] | MISSING, EdgeOnly, Positional()] = MISSING
    ha_graceful_switchover: Annotated[bool, EdgeOnly] = False
    ha_failover_count: Annotated[float, EdgeOnly] = 7
    preempt_switchover: Annotated[bool, Alias("preEmptSwitchover"), EdgeOnly] = False


class EdgeBfdRule(VcoModel):
    peer_address: str | None = None
    local_address: str | None = None
    multihop: bool | None = None
    detect_multiplier: int | None = None
    receive_interval: int | None = None
    transmit_interval: int | None = None


class EdgeBfdSettings(VcoModel):
    enabled: bool | None = None
    override: bool | None = None
    rules: list[EdgeBfdRule] | None = None
    rules_v6: list[EdgeBfdRule] | None = None


class LoopbackOspf(VcoModel):
    enabled: bool = False
    area: tuple[int | str] | list[Any] = Field(default_factory=list)


class LoopbackV6Detail(VcoModel):
    cidr_ip: str = ""
    cidr_prefix: Literal[128] = 128
    advertise: Annotated[bool, NoneIsFalse] = True
    ospf: LoopbackOspf = Field(default_factory=LoopbackOspf)


class LoopbackInterface(VcoModel):
    description: str | None | MISSING = MISSING
    cidr_ip: str
    cidr_prefix: Literal[32] = 32
    advertise: bool = True
    ping_response: bool = True
    segment_id: int = 0
    ospf: LoopbackOspf = Field(default_factory=LoopbackOspf)
    disable_ipv4: Annotated[bool, Alias("disableV4")] = False
    disable_ipv6: Annotated[bool, Alias("disableV6")] = True
    v6_detail: LoopbackV6Detail = Field(default_factory=LoopbackV6Detail)


class NtpServerEntry(VcoModel):
    server: str


class Ntp(VcoModel):
    override: Annotated[bool, OverrideFlag] = True
    enabled: bool = False
    servers: Annotated[list[NtpServerEntry], KeyField("server")] = Field(
        default_factory=list
    )
    source_interface: Annotated[str | MISSING, NoneIsMissing] = MISSING


class Md5AuthKey(VcoModel):
    key_number: int | None = None
    key: str


class NtpServer(VcoModel):
    override: Annotated[bool, OverrideFlag] = True
    enabled: bool = False
    authentication: Literal["NONE", "MD5"] = "NONE"
    keys: list[Md5AuthKey] = Field(default_factory=list)


class RadioSettingsItem(VcoModel):
    radio_id: int
    is_enabled: bool
    name: str
    mode: RadioMode
    band: RadioBand
    channel: Literal["auto"] | int
    width: str


class RadioSettings(VcoModel):
    country: str
    radios: Annotated[list[RadioSettingsItem], Positional()] = Field(
        default_factory=list
    )


class Layer2Settings(VcoModel):
    override_arp_timeout: Annotated[bool, Alias("overrideARPTimeout")] = False
    arp_stale_timeout_minutes: int = 2
    arp_dead_timeout_minutes: int = 25
    arp_cleanup_timeout_minutes: int = 240
    neighbor_discovery_reachable_time: int = 30


class GlobalIpv6SettingsAdvanced(VcoModel):
    disable_all_ipv6_traffic: Annotated[bool, Alias("disableAllIPv6Traffic")] = False
    drop_routing_header_type_zero_packets: bool = False
    drop_and_log_packets_for_rfc_reserved_fields: Annotated[
        bool, Alias("dropnLogPacketsForRFCReservedFields")
    ] = False
    enforce_extension_header_order_check: bool = False
    enforce_extension_header_validation: bool = False


class IcmpV6Messages(VcoModel):
    disable_destination_unreachable: bool = False
    disable_parameter_problem: bool = False
    disable_redirect: bool = False
    disable_time_exceeded: bool = False


class GlobalIpv6Settings(VcoModel):
    advanced_configuration: GlobalIpv6SettingsAdvanced = Field(
        default_factory=GlobalIpv6SettingsAdvanced
    )
    icmp_v6_messages: IcmpV6Messages = Field(default_factory=IcmpV6Messages)


class EdgeSnmpV2c(VcoModel):
    enabled: bool = False
    community: str = ""
    community_list: Annotated[list[str], Positional()] = Field(default_factory=list)
    allowed_ips: list[str] = Field(default_factory=list)


def _none_to_md5(v: Any) -> Any:
    return "MD5" if v is None or (isinstance(v, str) and v == "") else v


class SnmpV3User(VcoModel):
    name: str
    passphrase: str | None = None
    authentication_algorithm: Annotated[
        SnmpV3AuthenticationAlgorithm, BeforeValidator(_none_to_md5), Alias("authAlg")
    ] = "MD5"
    encryption_algorithm: Annotated[SnmpV3EncryptionAlgorithm, Alias("encrAlg")] = "AES"
    authentication: bool | MISSING = MISSING
    privacy: bool = False


class EdgeSnmpV3(VcoModel):
    enabled: bool = False
    users: Annotated[list[SnmpV3User], Positional()] = Field(default_factory=list)


class EdgeSnmp(VcoModel):
    port: int = 161
    v2c: Annotated[EdgeSnmpV2c, Alias("snmpv2c")] = Field(default_factory=EdgeSnmpV2c)
    v3: Annotated[EdgeSnmpV3, Alias("snmpv3")] = Field(default_factory=EdgeSnmpV3)


class EdgeZscaler(VcoModel):
    class ZscalerConfig(VcoModel):
        class Provider(VcoModel):
            ref: str | None = None
            logical_id: uuid.UUID | None = None

        class Location(VcoModel):
            name: str | None = None
            gateway_properties: Annotated[
                dict[str, Any] | Literal[""] | MISSING, Alias("gwProperties")
            ] = MISSING

        class SubLocation(VcoModel):
            gateway_properties: Annotated[
                dict[str, Any] | Literal[""] | MISSING, Alias("gwProperties")
            ] = MISSING
            internal_id: str
            name: str | None = None
            rule_id: str = ""
            vlans: list[int] | MISSING = MISSING
            lan_routed_interfaces: list[str] | MISSING = MISSING
            ip_addresses: list[str] | MISSING = MISSING
            ip_address_selection_manual: bool | MISSING = MISSING
            include_all_vlans: bool | MISSING = MISSING
            include_all_lan_interfaces: bool | MISSING = MISSING
            other_sub_location: bool | MISSING = MISSING

        override: Annotated[bool | MISSING, OverrideFlag] = True
        enabled: bool = False
        vendor: Literal["zscaler"] = "zscaler"
        cloud: str = ""
        provider: Provider | MISSING = Field(default_factory=Provider)
        location: Location | MISSING = Field(default_factory=Location)
        sub_locations: Annotated[
            list[SubLocation], KeyField("name", alt="internal_id")
        ] = Field(default_factory=list)

    config_: Annotated[ZscalerConfig, Alias("config")] = Field(
        default_factory=ZscalerConfig
    )
    deployment: dict[str, Any] | MISSING = MISSING


class EdgeAutoSimSwitchover(VcoModel):
    enabled: bool = False
    switchover_interval: int = 60


class EdgeCcFirewall(VcoModel):
    enabled: bool = False


class EdgeWirelessLinkManagement(VcoModel):
    limit_control_traffic_frequency: bool = False


class ModelLanInterfaces(VcoModel):
    interfaces: Annotated[list[SerializeAsAny[LanInterface]], KeyField("name")]


class ModelInterfaces(VcoModel):
    routed_interfaces: Annotated[
        list[SerializeAsAny[RoutedInterface]], KeyField("name")
    ]
    lan: ModelLanInterfaces


EdgeModelClass = Literal[
    "edge500",
    "edge5X0",
    "edge510",
    "edge515",
    "edge510lte",
    "edge6X0",
    "edge610lte",
    "me6200",
    "edge710",
    "edge7105g",
    "edge7X0",
    "edge840",
    "edge1000",
    "edge1000qat",
    "edge3X00",
    "edge3X10",
    "edge4100",
    "edge5100",
    "virtual",
]

EDGE_MODEL_CLASSES: set[str] = set(get_args(EdgeModelClass))


def _empty_string_to_zscaler(v: Any) -> Any:
    return EdgeZscaler() if v == "" else v


class EdgeVnfRef(VcoModel):
    ref: DeviceSettingsRef = "deviceSettings:vnfs:edge"
    logical_id: uuid.UUID | None = None


class EdgeVnfs(VcoModel):
    has_vnfs: bool = False
    edge: EdgeVnfRef = Field(default_factory=EdgeVnfRef)


class DeviceSettingsData(VcoModel):
    ha: Annotated[EdgeHaSettings, ContextDefault(profile=EdgeHaSettings)] = Field(
        default_factory=EdgeHaSettings
    )
    lan: LanSettings
    loopback_interfaces: Annotated[dict[str, LoopbackInterface], EdgeOnly] = Field(
        default_factory=dict
    )
    ntp: Annotated[Ntp | MISSING, ContextDefault(edge=MISSING, profile=Ntp)] = MISSING
    ntp_server: Annotated[
        NtpServer | MISSING, ContextDefault(edge=MISSING, profile=NtpServer)
    ] = MISSING
    radio_settings: Annotated[
        RadioSettings | MISSING, ContextDefault(edge=MISSING, profile=RadioSettings)
    ] = MISSING
    l2_settings: Annotated[
        Layer2Settings | MISSING, ContextDefault(edge=MISSING, profile=Layer2Settings)
    ] = MISSING
    global_ipv6_settings: Annotated[
        GlobalIpv6Settings | MISSING,
        Alias("globalIPv6Settings"),
        ContextDefault(edge=MISSING, profile=GlobalIpv6Settings),
    ] = MISSING
    routed_interfaces: Annotated[
        list[SerializeAsAny[RoutedInterface]] | None, EdgeOnly, KeyField("name")
    ] = Field(default_factory=list)
    models: Annotated[dict[EdgeModelClass, ModelInterfaces], ProfileOnly] = Field(
        default_factory=dict
    )
    segments: Annotated[
        list[DeviceSegment], KeyField("segment.name", alt="segment.segment_id")
    ]
    snmp: Annotated[
        EdgeSnmp | MISSING, ContextDefault(edge=MISSING, profile=EdgeSnmp)
    ] = MISSING
    zscaler: Annotated[EdgeZscaler, BeforeValidator(_empty_string_to_zscaler)] = Field(
        default_factory=EdgeZscaler
    )
    auto_sim_switchover: Annotated[EdgeAutoSimSwitchover | MISSING, EdgeOnly] = MISSING
    cc_firewall: Annotated[
        EdgeCcFirewall | MISSING, ContextDefault(edge=MISSING, profile=EdgeCcFirewall)
    ] = MISSING
    vnfs: Annotated[
        EdgeVnfs | MISSING, ContextDefault(edge=MISSING, profile=EdgeVnfs)
    ] = MISSING
    wireless_link_management: Annotated[
        EdgeWirelessLinkManagement | MISSING,
        ContextDefault(edge=MISSING, profile=EdgeWirelessLinkManagement),
    ] = MISSING

    _profile_segments: list[dict[str, Any]] | None = PrivateAttr(default=None)

    def bind_profile(self, profile_data: object) -> None:
        """Remember the segments of the profile module this layer inherits from.

        *profile_data* is that module's raw ``data``. It is what :meth:`segment`
        copies a missing segment's descriptor from.
        """
        self._profile_segments = copy_profile_segments(profile_data)

    def segment(self, name: str) -> DeviceSegment:
        """The segment called *name*; a layer that lacks it gets an empty entry.

        The new entry has the profile segment's descriptor and inherits all of
        its content. Raises ``UnboundError`` if the data is not bound to a
        profile (see :meth:`bind_profile`) and ``ValueError`` if the profile has
        no such segment. Use :meth:`get_segment` to look up without creating.
        """
        return get_or_create_segment(
            self.segments,
            name,
            self._profile_segments,
            "deviceSettings",
            DeviceSegment.model_validate,
            self.config_context,
        )

    def profile_segment_name(self, selector: object) -> str | None:
        """The bound profile's name for the segment *selector* names, or None.

        See :func:`~pyvelo.client.models.configuration.common.profile_segment_name`.
        Raises ``UnboundError`` if the data is not bound to a profile.
        """
        return profile_segment_name(self._profile_segments, selector)

    def profile_segment_id(self, selector: object) -> object | None:
        """The ``segmentId`` of the bound profile's segment *selector* names, or None.

        See :func:`~pyvelo.client.models.configuration.common.profile_segment_id`.
        Raises ``UnboundError`` if the data is not bound to a profile.
        """
        return profile_segment_id(self._profile_segments, selector)

    def get_segment(
        self, *, name: str | None = None, segment_id: int | None = None
    ) -> DeviceSegment:
        return find_segment(self.segments, name=name, segment_id=segment_id)
