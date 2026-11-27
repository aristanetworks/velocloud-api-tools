"""The Edge services the firewall allows: SSH, SNMP, the local UI, the console.

The Edge does not use the ICMP service, so no model reads ``icmp``: a stored one is
kept as read.
"""

from typing import Annotated

from pydantic import MISSING

from pyvelo.core import Alias, ServerOwned, VcoModel


class SshService(VcoModel):
    """SSH access to the Edge, limited to the addresses in one allow list.

    The one list, ``allow_selected_ip``, holds IPv4 and IPv6 addresses alike.
    SSH has no separate IPv6 list: the Edge reads none for SSH.
    """

    enabled: bool
    allow_selected_ip: list[str] | MISSING = MISSING
    rule_logical_id: Annotated[str | None | MISSING, ServerOwned()] = MISSING


class FirewallServicesGroup(VcoModel):
    enabled: bool
    allow_selected_ip: list[str] | MISSING = MISSING
    allow_selected_ip_v6: list[str] | MISSING = MISSING
    rule_logical_id: Annotated[str | None | MISSING, ServerOwned()] = MISSING


class LocalUiService(VcoModel):
    enabled: bool
    allow_selected_ip: list[str] | MISSING = MISSING
    allow_selected_ip_v6: list[str] | MISSING = MISSING
    port_number: int | MISSING = MISSING
    rule_logical_id: Annotated[str | None | MISSING, ServerOwned()] = MISSING


class ConsoleService(VcoModel):
    enabled: bool
    rule_logical_id: Annotated[str | None | MISSING, ServerOwned()] = MISSING


class FirewallServices(VcoModel):
    logging_enabled: bool | MISSING = MISSING
    ssh: SshService | MISSING = MISSING
    snmp: FirewallServicesGroup | MISSING = MISSING
    local_ui: LocalUiService | MISSING = MISSING
    console: ConsoleService | MISSING = MISSING
    post: ConsoleService | MISSING = MISSING
    usb_disabled: Annotated[bool | MISSING, Alias("usb.disabled")] = MISSING
