from typing import Annotated

from pydantic import MISSING

from pyvelo.core import Alias, VcoModel


class FirewallServicesGroup(VcoModel):
    enabled: bool
    allow_selected_ip: list[str] | MISSING = MISSING
    allow_selected_ip_v6: list[str] | MISSING = MISSING
    rule_logical_id: str | None = None


class LocalUiService(VcoModel):
    enabled: bool
    allow_selected_ip: list[str] | MISSING = MISSING
    allow_selected_ip_v6: list[str] | MISSING = MISSING
    port_number: int | MISSING = MISSING
    rule_logical_id: str | None = None


class ConsoleService(VcoModel):
    enabled: bool
    rule_logical_id: str | None = None


class FirewallServices(VcoModel):
    logging_enabled: bool | MISSING = MISSING
    ssh: FirewallServicesGroup | MISSING = MISSING
    snmp: FirewallServicesGroup | MISSING = MISSING
    local_ui: LocalUiService | MISSING = MISSING
    icmp: FirewallServicesGroup | MISSING = MISSING
    console: ConsoleService | MISSING = MISSING
    post: ConsoleService | MISSING = MISSING
    usb_disabled: Annotated[bool | MISSING, Alias("usb.disabled")] = MISSING
