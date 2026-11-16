"""Convenience re-exports for firewall configuration types.

Usage:
    from pyvelo_intent.config.firewall import FirewallOutboundRule, FirewallRuleAction
"""

from pyvelo.client.models.configuration.firewall.common import (
    FirewallInboundRuleActionType,
    FirewallRuleAction,
)
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rule_action import (
    InboundRuleAction,
    OutboundRuleAction,
)
from pyvelo.client.models.configuration.firewall.rule_match import (
    InboundRuleMatch,
    InboundRuleMatchV6,
    OutboundRuleMatch,
)
from pyvelo.client.models.configuration.firewall.rules import (
    FirewallInboundRule,
    FirewallInboundRuleV6,
    FirewallOutboundRule,
)
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment

__all__ = [
    "FirewallData",
    "FirewallInboundRule",
    "FirewallInboundRuleActionType",
    "FirewallInboundRuleV6",
    "FirewallOutboundRule",
    "FirewallRuleAction",
    "FirewallSegment",
    "InboundRuleAction",
    "InboundRuleMatch",
    "InboundRuleMatchV6",
    "OutboundRuleAction",
    "OutboundRuleMatch",
]
