"""With no edit, the planned payload is the raw data as read. Always."""

import copy
import json
import random
import uuid
from collections.abc import Callable

import pytest
from pyvelo.client.models.configuration.profile_segments import module_template_value
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import ConfigurationModuleList
from pyvelo.core import ConfigContext
from pyvelo.core.layers import Edge, Profile
from pyvelo.testing import make_module_json

SCALARS = [None, True, False, 0, 1, -3, 2.5, 1e3, "", "x", "0", "REGULAR"]


def junk(rng: random.Random, depth: int = 0) -> object:
    roll = rng.random()
    if depth > 2 or roll < 0.5:
        return rng.choice(SCALARS)
    if roll < 0.75:
        return [junk(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {
        f"k{rng.randint(0, 9)}": junk(rng, depth + 1) for _ in range(rng.randint(0, 3))
    }


def sprinkle(rng: random.Random, node: object) -> None:
    """Add unknown keys at every dict level of a valid document."""
    if isinstance(node, dict):
        for value in list(node.values()):
            sprinkle(rng, value)
        for _ in range(rng.randint(0, 2)):
            node[f"future{rng.randint(0, 99)}"] = junk(rng)
    elif isinstance(node, list):
        for item in node:
            sprinkle(rng, item)


def desc(sid: int) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": f"seg{sid}",
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


def firewall(rng: random.Random) -> dict[str, object]:
    doc: dict[str, object] = {
        "segments": [
            {
                "segment": desc(i),
                "outbound": [
                    {
                        "name": f"r{j}",
                        "action": {"allow_or_deny": rng.choice(["allow", "deny"])},
                        "match": {},
                    }
                    for j in range(rng.randint(0, 3))
                ],
            }
            for i in range(rng.randint(0, 3))
        ]
    }
    for key, values in {
        "firewall_enabled": [True, False, None],
        "syslog_forwarding": [True, False, None],
        "firewall_logging_enabled": [True, False, None],
        "stateful_firewall_enabled": [False, None],
        "atp_enabled": [True, False, None],
    }.items():
        if rng.random() < 0.5:
            doc[key] = rng.choice(values)
    for key in OBJECT_KEYS:
        roll = rng.random()
        if roll < 0.25:
            doc[key] = vary(rng, module_template_value("firewall", key))
        elif roll < 0.4:
            doc[key] = None
    return doc


# The firewall's objects, which the edge inherits or stores whole.
OBJECT_KEYS = (
    "services",
    "statefulFirewallSettings",
    "statefulFirewallSettingsV6",
    "networkProtectionSettings",
    "networkProtectionSettingsV6",
    "securityFeatures",
)

# The keys whose null is read at the edge only (``syslog_forwarding`` takes one
# at the profile too).
INHERITED_ON_NULL = (
    *OBJECT_KEYS,
    "firewall_enabled",
    "firewall_logging_enabled",
    "stateful_firewall_enabled",
    "atp_enabled",
)


def vary(rng: random.Random, node: object) -> object:
    """A copy of a valid object with some of its booleans flipped.

    And some of its numbers changed.
    """
    if isinstance(node, dict):
        return {key: vary(rng, value) for key, value in node.items()}
    if isinstance(node, list):
        return [vary(rng, item) for item in node]
    if isinstance(node, bool):
        return node if rng.random() < 0.7 else not node
    if isinstance(node, int) and rng.random() < 0.3:
        return node + rng.randint(1, 5)
    return node


def uuid_text(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def maybe_id(rng: random.Random, key: str) -> dict[str, object]:
    """An ``{key: uuid}`` or nothing.

    Documents that lack the key get a random default.
    """
    return {key: uuid_text(rng)} if rng.random() < 0.5 else {}


QOS_MATCHES: list[dict[str, object]] = [
    {},
    {"ipVersion": "IPv4", "sip": "any", "dip": "any", "sip_prenat_enabled": 0},
    {"ipVersion": "IPv4", "sip": "any", "sipV6": "any", "sip_prenat_enabledV6": 0},
    {"ipVersion": "IPv6", "sipV6": "any", "dipV6": "any"},
    {"ipVersion": "IPv6", "sip": "any", "sipV6": "any"},
    {"ipVersion": "IPv4v6", "sip": "any", "sipV6": "any"},
]


def qos_rule(rng: random.Random, name: str) -> dict[str, object]:
    return {
        "name": name,
        "match": dict(rng.choice(QOS_MATCHES)),
        "action": {
            "edge2EdgeRouteAction": {},
            "edge2DataCenterRouteAction": {},
            "edge2CloudRouteAction": {},
            "QoS": {},
        },
        **maybe_id(rng, "ruleLogicalId"),
    }


def qos(rng: random.Random) -> dict[str, object]:
    return {
        "segments": [
            {
                "segment": desc(i),
                "rules": [qos_rule(rng, f"r{j}") for j in range(rng.randint(0, 3))],
            }
            for i in range(rng.randint(0, 3))
        ]
    }


def device_settings(rng: random.Random) -> dict[str, object]:
    def bgp() -> dict[str, object]:
        return {
            "enabled": rng.choice([True, False]),
            "ASN": "65000",
            "neighbors": [
                {
                    "neighborIp": f"10.0.0.{j}",
                    "neighborAS": "65001",
                    **maybe_id(rng, "id"),
                }
                for j in range(rng.randint(0, 3))
            ],
            "filters": [
                {"name": f"f{j}", "rules": [], **maybe_id(rng, "id")}
                for j in range(rng.randint(0, 3))
            ],
        }

    return {
        "lan": {"networks": [], "interfaces": []},
        "segments": [
            {"segment": desc(i), **({"bgp": bgp()} if rng.random() < 0.7 else {})}
            for i in range(rng.randint(1, 3))
        ],
    }


def wan(rng: random.Random) -> dict[str, object]:
    return {
        "links": [
            {
                "name": f"l{i}",
                "type": "WIRED",
                "classesOfService": {
                    "classesOfService": [
                        {"dscpTags": ["CS0"], **maybe_id(rng, "id")}
                        for _ in range(rng.randint(0, 3))
                    ]
                },
            }
            for i in range(rng.randint(0, 3))
        ]
    }


@pytest.mark.parametrize("seed", range(200))
@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("firewall", firewall),
        ("QOS", qos),
        ("deviceSettings", device_settings),
        ("WAN", wan),
    ],
)
@pytest.mark.parametrize("level", [ConfigContext.EDGE, ConfigContext.PROFILE])
def test_no_edit_round_trip(
    seed: int,
    name: str,
    build: Callable[[random.Random], dict[str, object]],
    level: ConfigContext,
) -> None:
    rng = random.Random(seed)
    raw = build(rng)
    if level is ConfigContext.PROFILE:
        # A null at one of these keys is the edge's way to inherit; a profile
        # has nothing to inherit from, and does not read one.
        for key in INHERITED_ON_NULL:
            if raw.get(key, 0) is None:
                del raw[key]
    sprinkle(rng, raw)
    row = make_module_json(name, copy.deepcopy(raw))
    rows = (
        ConfigurationModuleList[Edge]
        if level is ConfigContext.EDGE
        else ConfigurationModuleList[Profile]
    )
    for module in (
        rows.model_validate([copy.deepcopy(row)]).root[0],
        rows.model_validate_json(json.dumps([row])).root[0],
    ):
        plan = plan_module(module, level=level)
        assert plan.operation == "none"
        assert plan.data == raw
        assert module.raw_data == raw
