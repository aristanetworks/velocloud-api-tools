"""The ``with`` options of ``firewall/getFirewallLogs``."""

from enum import StrEnum


class GetFirewallLogsWith(StrEnum):
    RULES = "rules"
