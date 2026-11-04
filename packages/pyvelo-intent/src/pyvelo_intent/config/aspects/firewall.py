"""Predefined firewall aspects."""

from pyvelo.client.enums.configuration import ConfigurationModuleName

from pyvelo_intent.config.aspect import Aspect

FIREWALL_ASPECTS: list[Aspect] = [
    Aspect(
        name="firewall_rules",
        module=ConfigurationModuleName.FIREWALL,
        path="outbound",
        description="Per-segment outbound firewall rules",
        tags=["firewall", "rules", "outbound", "security"],
        segment_scoped=True,
        examples=[
            {
                "block-ssh": {
                    "name": "block-ssh",
                    "action": {"allow_or_deny": "deny"},
                    "match": {"proto": 6, "dport_low": 22, "dport_high": 22},
                }
            }
        ],
    ),
]
