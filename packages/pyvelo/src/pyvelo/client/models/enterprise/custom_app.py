from typing import Annotated

from pydantic import Field

from pyvelo.core import Alias, Tinyint, VcoModel


class PortMapping(VcoModel):
    tcp_ports: list[int | tuple[int, int]] = Field(default_factory=list)
    udp_ports: list[int | tuple[int, int]] = Field(default_factory=list)


class KnownIpPortMapping(PortMapping):
    subnets: list[str] = Field(default_factory=list)


class FqdnMapping(VcoModel):
    fqdn_list: Annotated[list[str], Alias("FQDNList")] = Field(default_factory=list)


class CustomAppData(VcoModel):
    name: str
    display_name: str
    class_: Annotated[int, Alias("class")]
    description: str
    do_not_slow_learn: Tinyint
    must_not_perform_dpi: int
    known_ip_port_mapping: KnownIpPortMapping
    protocol_port_mapping: PortMapping
    fqdn_mapping: Annotated[FqdnMapping, Alias("FQDNMapping")]
    id_: Annotated[int, Alias("id")]
