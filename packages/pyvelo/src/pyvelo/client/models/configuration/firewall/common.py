from typing import Literal

# Firewall rule actions
FirewallRuleAction = Literal["allow", "deny", "drop", "reject", "skip"]
FirewallInboundRuleActionType = Literal["port_forwarding", "one_to_one_nat"]

# Security feature actions
SecurityFeatureAction = Literal["allow", "block", "log"]
