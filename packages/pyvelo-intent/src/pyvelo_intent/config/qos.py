"""Convenience re-exports for QoS configuration types.

Usage:
    from pyvelo_intent.config.qos import QosRule, QosRuleMatch, QosRuleAction
"""

from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import (
    QosRule,
    QosRuleAction,
    QosRuleMatch,
    RouteAction,
    RouteConfig,
    SlaSettings,
)
from pyvelo.client.models.configuration.qos.scheduler import QosSettings
from pyvelo.client.models.configuration.qos.segment import (
    CosMapping,
    QosSegment,
)

__all__ = [
    "CosMapping",
    "QosData",
    "QosRule",
    "QosRuleAction",
    "QosRuleMatch",
    "QosSegment",
    "QosSettings",
    "RouteAction",
    "RouteConfig",
    "SlaSettings",
]
