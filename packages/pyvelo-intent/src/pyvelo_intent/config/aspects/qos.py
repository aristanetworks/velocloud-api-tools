"""Predefined QoS aspects."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

from pyvelo_intent.config.aspect import Aspect

QOS_ASPECTS: list[Aspect] = [
    Aspect(
        name="business_policy",
        module=ConfigurationModuleName.QOS,
        path="rules",
        description="Per-segment QoS/business policy rules",
        tags=["qos", "business policy", "rules", "routing", "traffic"],
        segment_scoped=True,
        examples=[
            {
                "voip-priority": {
                    "name": "voip-priority",
                    "match": {
                        "appid": -1,
                        "proto": 17,
                        "dport_low": 5060,
                        "dport_high": 5061,
                    },
                    "action": {"route_type": "edge2Any", "QoS": {"type": "realtime"}},
                }
            }
        ],
    ),
]
