"""The action of an outbound firewall rule and of an inbound (NAT) rule."""

from typing import Annotated

from pydantic import MISSING

from pyvelo.core import Alias, ConfigModel

from .common import FirewallInboundRuleActionType, FirewallRuleAction


class OutboundRuleAction(ConfigModel):
    allow_or_deny: Annotated[FirewallRuleAction, Alias("allow_or_deny")]


class InboundNatSettings(ConfigModel):
    lan_ip: Annotated[str, Alias("lan_ip")]
    lan_port: Annotated[int | None, Alias("lan_port")] = None
    outbound: bool | MISSING = MISSING


class InboundRuleAction(ConfigModel):
    type_: Annotated[FirewallInboundRuleActionType, Alias("type")]
    nat: InboundNatSettings | MISSING = MISSING
    interface: str | MISSING = MISSING
    segment_id: int | MISSING = MISSING
    subinterface_id: int | MISSING = MISSING
