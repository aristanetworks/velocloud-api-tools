import uuid
from typing import Annotated, Any, Literal

from pydantic import MISSING, Field

from pyvelo.core import Alias, Normalizer, VcoModel
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import ApplicationIdentity

from ..common import (
    AddressGroupRef,
    AddressType,
    RuleMatchType,
    ServiceGroupRef,
    VlanType,
)
from .common import (
    BackhaulEdgeType,
    QosLinkPolicy,
    RouteConfigType,
    RoutePolicy,
    RouteType,
    WANLinkServiceGroup,
)
from .scheduler import QosSettings


def _subnet_mask_normalizer(v: Any) -> Any:
    return "any" if isinstance(v, str) and v == "255.255.255.255" else v


class QosRuleMatch(VcoModel):
    # IP addressing
    ip_version: AddressType = "IPv4v6"
    source_ip: Annotated[str, Alias("sip")] = "any"
    source_ip_pre_nat_enabled: Annotated[int, Alias("sip_prenat_enabled")] = 0
    source_ip_v6: Annotated[str, Alias("sipV6")] = "any"
    source_ip_v6_pre_nat_enabled: Annotated[int, Alias("sip_prenat_enabledV6")] = 0
    destination_ip: Annotated[str, Alias("dip")] = "any"
    destination_ip_pre_nat_enabled: Annotated[int, Alias("dip_prenat_enabled")] = 0
    destination_ip_v6: Annotated[str, Alias("dipV6")] = "any"
    destination_ip_v6_pre_nat_enabled: Annotated[int, Alias("dip_prenat_enabledV6")] = 0

    # Subnet masks
    source_subnet_mask: Annotated[
        str, Alias("ssm"), Normalizer(_subnet_mask_normalizer)
    ] = "255.255.255.255"
    destination_subnet_mask: Annotated[
        str, Alias("dsm"), Normalizer(_subnet_mask_normalizer)
    ] = "255.255.255.255"

    # Port ranges
    source_port_low: Annotated[int, Alias("sport_low")] = -1
    source_port_high: Annotated[int, Alias("sport_high")] = -1
    destination_port_low: Annotated[int, Alias("dport_low")] = -1
    destination_port_high: Annotated[int, Alias("dport_high")] = -1

    # Application and traffic classification
    application_id: Annotated[
        int, Alias("appid"), Resolvable(ApplicationIdentity, "id")
    ] = -1
    application_class_id: Annotated[int, Alias("classid")] = -1
    dscp: int = -1

    # Network attributes
    hostname: str = ""
    proto: int = -1
    source_vlan: Annotated[int, Alias("svlan")] = -1
    destination_vlan: Annotated[int, Alias("dvlan")] = -1

    # Interface matching
    source_interface: Annotated[str, Alias("sInterface")] = ""
    destination_interface: Annotated[str, Alias("dInterface")] = ""

    # Rule types for source/destination
    source_rule_type: Annotated[RuleMatchType, Alias("s_rule_type")] = "prefix"
    destination_rule_type: Annotated[RuleMatchType, Alias("d_rule_type")] = "prefix"

    os_version: Annotated[int, Alias("os_version")] = -1

    source_address_group: Annotated[AddressGroupRef, Alias("sAddressGroup")] = MISSING
    destination_address_group: Annotated[AddressGroupRef, Alias("dAddressGroup")] = (
        MISSING
    )

    source_port_group: Annotated[ServiceGroupRef, Alias("sPortGroup")] = MISSING
    destination_port_group: Annotated[ServiceGroupRef, Alias("dPortGroup")] = MISSING

    vlan_type: VlanType = "end-to-end"


class BackhaulEdge(VcoModel):
    type_: Annotated[BackhaulEdgeType, Alias("type")] = "edgeHub"
    logical_id: uuid.UUID
    edge_logical_id: uuid.UUID | MISSING = MISSING
    edge_name: Annotated[str | MISSING, Alias("name")] = MISSING


class RouteConfig(VcoModel):
    type_: Annotated[RouteConfigType | MISSING, Alias("type")] = MISSING
    name: str | None = None
    logical_id: uuid.UUID | None = None
    bypass_gateway: bool = False
    backhaul_edges: list[BackhaulEdge] | MISSING = MISSING
    backhaul_edges_auto_selection: bool = False


class RouteAction(VcoModel):
    interface: str = "auto"
    subinterface_id: int = -1
    link_internal_logical_id: uuid.UUID | Literal["auto"] = "auto"
    link_policy: QosLinkPolicy = "auto"
    route_cfg: RouteConfig = Field(default_factory=RouteConfig)
    route_policy: RoutePolicy = "auto"
    service_group: WANLinkServiceGroup = "ALL"
    vlan_id: int = -1
    wan_link: Annotated[str, Alias("wanlink")] = "auto"
    wan_link_name: str = ""
    link_cos_logical_id: uuid.UUID | None = None
    link_outer_dscp_tag: str | None = None
    link_inner_dscp_tag: str | None = None
    icmp_logical_id: uuid.UUID | None = None


class SlaSettings(VcoModel):
    latency_ms: str = "0"
    jitter_ms: str = "0"
    loss_percent: Annotated[str, Alias("loss_pct")] = "0.0"


class QosNatSettings(VcoModel):
    enabled: bool = False
    source_ip: str = "no"
    destination_ip: Annotated[str, Alias("destIp")] = "no"


class QosRuleAction(VcoModel):
    route_type: RouteType = "edge2Any"
    route_policy: str | MISSING = MISSING  # only in profiles - maybe legacy

    allow_conditional_bh: bool = False
    user_disable_conditional_bh: bool = False

    # Route actions for different route types
    edge_to_edge_route_action: Annotated[RouteAction, Alias("edge2EdgeRouteAction")]
    edge_to_data_center_route_action: Annotated[
        RouteAction, Alias("edge2DataCenterRouteAction")
    ]
    edge_to_cloud_route_action: Annotated[RouteAction, Alias("edge2CloudRouteAction")]

    # QoS and SLA settings
    qos: Annotated[QosSettings, Alias("QoS")]
    sla: SlaSettings | MISSING = MISSING

    # NAT configuration
    nat_ip_version: AddressType | None = None
    nat: QosNatSettings = Field(default_factory=QosNatSettings)
    nat_v6: Annotated[QosNatSettings, Alias("natV6")] = Field(
        default_factory=QosNatSettings
    )

    # Security settings
    edge_to_edge_encryption: bool = True


class QosRule(VcoModel):
    name: str
    match: QosRuleMatch
    action: QosRuleAction
    rule_logical_id: uuid.UUID = Field(default_factory=uuid.uuid4)
