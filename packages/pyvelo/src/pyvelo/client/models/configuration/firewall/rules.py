import uuid
from typing import Any

from pydantic import MISSING

from pyvelo.core import VcoModel

from .rule_action import InboundRuleAction, OutboundRuleAction
from .rule_match import InboundRuleMatch, InboundRuleMatchV6, OutboundRuleMatch


class FirewallOutboundRule(VcoModel):
    name: str
    match: OutboundRuleMatch
    action: OutboundRuleAction
    rule_logical_id: str | MISSING = MISSING
    logging_enabled: bool = False

    security_service_group: uuid.UUID | None = None

    comments: list[dict[str, Any]] | None = None


class FirewallInboundRule(VcoModel):
    name: str
    match: InboundRuleMatch
    action: InboundRuleAction
    rule_logical_id: str | MISSING = MISSING
    logging_enabled: bool = False


class FirewallInboundRuleV6(VcoModel):
    name: str
    match: InboundRuleMatchV6
    action: InboundRuleAction
    rule_logical_id: str | MISSING = MISSING
    logging_enabled: bool = False
