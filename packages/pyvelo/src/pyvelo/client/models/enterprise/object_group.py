from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, Tag

from pyvelo.core import Alias, VcoModel


def _address_entry_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        if "domain" in v:
            return "domain"
        elif "ip" in v:
            return "ip"
    # Fallback for already-parsed objects
    if hasattr(v, "domain"):
        return "domain"
    return "ip"


class IpAddressEntry(VcoModel):
    entry_type: Literal["ip"] = Field(default="ip", exclude=True)
    ip: str
    rule_type: Annotated[
        Literal["exact", "prefix", "netmask", "wildcard"], Alias("rule_type")
    ]
    mask: str


class DomainAddressEntry(VcoModel):
    entry_type: Literal["domain"] = Field(default="domain", exclude=True)
    domain: str


# Discriminated union of address entry types
AddressGroupEntry = Annotated[
    Annotated[IpAddressEntry, Tag("ip")] | Annotated[DomainAddressEntry, Tag("domain")],
    Discriminator(_address_entry_discriminator),
]


def _port_entry_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        if "port_low" in v or "port_high" in v:
            return "port"
        elif "type" in v or "code_low" in v:
            return "icmp"
    # Fallback for already-parsed objects
    if hasattr(v, "port_low"):
        return "port"
    return "icmp"


class TcpUdpPortEntry(VcoModel):
    entry_type: Literal["port"] = Field(default="port", exclude=True)
    proto: Literal[6, 17] = 6
    port_low: Annotated[int, Alias("port_low")]
    port_high: Annotated[int, Alias("port_high")]


class IcmpPortEntry(VcoModel):
    entry_type: Literal["icmp"] = Field(default="icmp", exclude=True)
    proto: Literal[1, 58] = 1
    type_: Annotated[int | None, Alias("type")] = None
    code_low: Annotated[int | None, Alias("code_low")] = None
    code_high: Annotated[int | None, Alias("code_high")] = None


# Discriminated union of port entry types
PortGroupEntry = Annotated[
    Annotated[TcpUdpPortEntry, Tag("port")] | Annotated[IcmpPortEntry, Tag("icmp")],
    Discriminator(_port_entry_discriminator),
]
