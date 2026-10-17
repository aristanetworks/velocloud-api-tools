from typing import Annotated, Any, override

from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel
from pyvelo.core.fields import Normalizer, Resolvable
from pyvelo.core.identity import ApplicationIdentity

from ..common import (
    AddressGroupRef,
    AddressType,
    RuleMatchType,
    ServiceGroupRef,
    VlanType,
)


def _subnet_mask_normalizer(v: Any) -> Any:
    return "any" if isinstance(v, str) and v == "255.255.255.255" else v


class OutboundRuleMatch(VcoModel):
    # IP addressing
    ip_version: AddressType = "IPv4"
    source_ip: Annotated[str | MISSING, Alias("sip")] = "any"
    source_ip_v6: Annotated[str | MISSING, Alias("sipV6")] = "any"
    destination_ip: Annotated[str | MISSING, Alias("dip")] = "any"
    destination_ip_v6: Annotated[str | MISSING, Alias("dipV6")] = "any"

    @override
    def model_post_init(self, __context: Any) -> None:
        """Strip IP fields that must be absent for the configured ip_version.

        VCO validation requires:
        - IPv4: sipv6/dipv6 must be undefined (absent)
        - IPv6: sip/dip must be undefined (absent)
        - IPv4v6: all four must be present as "any"
        """
        if self.ip_version == "IPv4":
            self.source_ip_v6 = MISSING
            self.destination_ip_v6 = MISSING
        elif self.ip_version == "IPv6":
            self.source_ip = MISSING
            self.destination_ip = MISSING

    # Subnet masks
    source_subnet_mask: Annotated[
        str, Alias("ssm"), Normalizer(_subnet_mask_normalizer)
    ] = "any"
    destination_subnet_mask: Annotated[
        str, Alias("dsm"), Normalizer(_subnet_mask_normalizer)
    ] = "any"

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

    # MAC address
    source_mac: Annotated[str, Alias("smac")] = "any"

    # Optional device and group matching
    os_version: Annotated[int, Alias("os_version")] = -1
    source_address_group: Annotated[AddressGroupRef, Alias("sAddressGroup")] = MISSING
    destination_address_group: Annotated[AddressGroupRef, Alias("dAddressGroup")] = (
        MISSING
    )
    source_port_group: Annotated[ServiceGroupRef, Alias("sPortGroup")] = MISSING
    destination_port_group: Annotated[ServiceGroupRef, Alias("dPortGroup")] = MISSING

    vlan_type: VlanType = "end-to-end"


class InboundRuleMatch(VcoModel):
    # IP addressing
    ip_version: AddressType = "IPv4"
    source_ip: Annotated[str, Alias("sip")] = "any"
    source_subnet_mask: Annotated[str, Alias("ssm")] = "any"
    destination_ip: Annotated[str, Alias("dip")] = "any"
    destination_subnet_mask: Annotated[str, Alias("dsm")] = "any"

    # Port range
    destination_port_low: Annotated[int, Alias("dport_low")] = -1
    destination_port_high: Annotated[int, Alias("dport_high")] = -1

    # Protocol
    proto: int = -1

    # Application classification
    application_id: Annotated[
        int, Alias("appid"), Resolvable(ApplicationIdentity, "id")
    ] = -1
    application_class_id: Annotated[int, Alias("classid")] = -1
    dscp: int = -1

    # VLAN
    destination_vlan: Annotated[int, Alias("dvlan")] = -1

    # Optional fields
    hostname: str | MISSING = MISSING
    destination_interface: Annotated[str | MISSING, Alias("d_interface")] = MISSING
    destination_rule_type: Annotated[RuleMatchType | MISSING, Alias("d_rule_type")] = (
        MISSING
    )
    destination_address_group: Annotated[AddressGroupRef, Alias("d_address_group")] = (
        MISSING
    )
    destination_port_group: Annotated[ServiceGroupRef, Alias("d_port_group")] = MISSING


class InboundRuleMatchV6(VcoModel):
    # IP addressing
    ip_version: AddressType = "IPv6"
    destination_ip_v6: Annotated[str, Alias("dip_v6")] = "any"

    # Port range
    destination_port_low: Annotated[int, Alias("dport_low")] = -1
    destination_port_high: Annotated[int, Alias("dport_high")] = -1

    # Protocol
    proto: int = -1

    # Application classification
    application_id: Annotated[
        int, Alias("appid"), Resolvable(ApplicationIdentity, "id")
    ] = -1
    application_class_id: Annotated[int, Alias("classid")] = -1
    dscp: int = -1

    # VLAN
    destination_vlan: Annotated[int, Alias("dvlan")] = -1

    # Optional fields
    hostname: str | MISSING = MISSING
    destination_interface: Annotated[str | MISSING, Alias("d_interface")] = MISSING
    destination_rule_type: Annotated[RuleMatchType | MISSING, Alias("d_rule_type")] = (
        MISSING
    )
    destination_address_group: Annotated[AddressGroupRef, Alias("d_address_group")] = (
        MISSING
    )
    destination_port_group: Annotated[ServiceGroupRef, Alias("d_port_group")] = MISSING
