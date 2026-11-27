"""The outbound, inbound and inbound IPv6 firewall rules: a match and an action."""

import uuid
from typing import Annotated, Any

from pydantic import MISSING

from pyvelo.core import ConfigModel, ServerOwned

from .rule_action import InboundRuleAction, OutboundRuleAction
from .rule_match import InboundRuleMatch, InboundRuleMatchV6, OutboundRuleMatch


class FirewallOutboundRule(ConfigModel):
    name: str
    match: OutboundRuleMatch
    action: OutboundRuleAction
    rule_logical_id: Annotated[str | MISSING, ServerOwned()] = MISSING
    logging_enabled: bool = False
    hosted_logging_enabled: bool | MISSING = MISSING
    syslog_forwarding_enabled: bool | MISSING = MISSING

    security_service_group: uuid.UUID | None = None

    comments: list[dict[str, Any]] | None = None


class FirewallInboundRule(ConfigModel):
    name: str
    match: InboundRuleMatch
    action: InboundRuleAction
    rule_logical_id: Annotated[str | MISSING, ServerOwned()] = MISSING
    logging_enabled: bool = False


class FirewallInboundRuleV6(ConfigModel):
    name: str
    match: InboundRuleMatchV6
    action: InboundRuleAction
    rule_logical_id: Annotated[str | MISSING, ServerOwned()] = MISSING
    logging_enabled: bool = False
