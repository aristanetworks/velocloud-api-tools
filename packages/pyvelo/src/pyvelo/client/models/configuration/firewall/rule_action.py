from typing import Annotated

from pydantic_core import MISSING

from pyvelo.core import Alias, VcoModel

from .common import FirewallInboundRuleActionType, FirewallRuleAction


class OutboundRuleAction(VcoModel):
    allow_or_deny: Annotated[FirewallRuleAction, Alias("allow_or_deny")]


class InboundNatSettings(VcoModel):
    lan_ip: Annotated[str, Alias("lan_ip")]
    lan_port: Annotated[int | None, Alias("lan_port")] = None
    outbound: bool | MISSING = MISSING


class InboundRuleAction(VcoModel):
    type_: Annotated[FirewallInboundRuleActionType, Alias("type")]
    nat: InboundNatSettings | MISSING = MISSING
    interface: str | MISSING = MISSING
    segment_id: int | MISSING = MISSING
    subinterface_id: int | MISSING = MISSING
