import uuid
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel

from ..common import AddressType
from .common import (
    BandwidthMeasurement,
    DiscoveryMode,
    LinkType,
    WanMode,
)


class WanLinkStaticSla(VcoModel):
    latency_ms: Literal[0] | str = 0
    jitter_ms: Literal[0] | str = 0
    loss_pct: Literal[0] | str = 0


class WanLinkCosEntry(VcoModel):
    bandwidth_guaranteed: bool = False
    bandwidth_percent: Annotated[float, Alias("bandwidth_pct")] = 100
    default_class_of_service: bool = False
    dscp_tags: list[str]
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    policing: bool = False
    static_sla: WanLinkStaticSla = Field(default_factory=WanLinkStaticSla)


class WanLinkCos(VcoModel):
    class_id: str | None = None
    classes_of_service: list[WanLinkCosEntry] = Field(default_factory=list)


class WanLink(VcoModel):
    logical_id: str | None = None
    internal_id: uuid.UUID | MISSING = MISSING
    addressing_version: AddressType = "IPv4"
    discovery: DiscoveryMode = "AUTO_DISCOVERED"
    mode: WanMode = "PUBLIC"
    type_: Annotated[LinkType, Alias("type")]
    name: str
    isp: str | None = None

    # IPv4 addresses
    source_ip_address: str | None = None
    next_hop_ip_address: str | None = None
    public_ip_address: str | None | MISSING = MISSING
    virtual_ip_address: Literal[""] | None = None

    # IPv6 addresses
    source_ip_address_v6: str | None = None
    next_hop_ip_address_v6: str | None = None
    public_ip_address_v6: str | None = None
    preferred_family: str | None = "IPv4"

    # VLAN settings
    vlan_id: int = 0
    custom_vlan_id: bool = False
    enable8021_p: bool = False
    priority8021_p: int = 0

    # Bandwidth settings
    bw_measurement: BandwidthMeasurement = "SLOW_START"
    upstream_mbps: str | None = None
    downstream_mbps: str | None = None
    dynamic_bw_adjustment_enabled: bool = False

    # Interface configuration
    interfaces: list[str] = Field(default_factory=list)
    mtu: Annotated[int, Alias("MTU")] = 1500
    pmtud_disabled: bool = False
    overhead_bytes: int = 0
    dscp_tag: str = ""

    # SLA and QoS settings (complex nested objects - keep flexible for now)
    static_sla: Annotated[WanLinkStaticSla, Alias("staticSLA")] = Field(
        default_factory=WanLinkStaticSla
    )
    classes_of_service: WanLinkCos = Field(default_factory=WanLinkCos)
    static_sla_enabled: bool = False
    class_of_service_enabled: Annotated[bool, Alias("classesOfServiceEnabled")] = False
    strict_ip_precedence: bool = False

    # Link behavior flags
    backup_only: bool = False
    hot_standby: bool = False
    min_active_links: int = 1
    udp_hole_punching: bool = False
    encrypt_overlay: bool = True

    # Status and reachability
    last_active: int | str | None | MISSING = MISSING
    orchestrator_service_reachable: bool | MISSING = (
        MISSING  # only present for PRIVATE-mode links
    )
    orchestrator_service_reachable_backup: bool | MISSING = (
        MISSING  # only present for PRIVATE-mode links
    )

    # Description
    description: str = ""

    # MPLS network
    mpls_network: str | MISSING = ""
    private_network: dict[str, Any] | None = None

    @field_validator("last_active", mode="before")
    @classmethod
    def handle_empty_last_active(cls, v):
        """"""
        if v == "":
            return None
        return v
