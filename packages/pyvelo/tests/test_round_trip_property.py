"""With no edit, the planned payload is the raw data as read. Always."""

import copy
import json
import random
from collections.abc import Callable

import pytest
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import ConfigurationModuleList
from pyvelo.core import ConfigContext
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
        "firewall_enabled": [True, False],
        "syslog_forwarding": [True, False, None],
        "firewall_logging_enabled": [True, False],
        "stateful_firewall_enabled": [False],
    }.items():
        if rng.random() < 0.5:
            doc[key] = rng.choice(values)
    return doc


def qos(rng: random.Random) -> dict[str, object]:
    return {
        "segments": [
            {"segment": desc(i), "rules": []} for i in range(rng.randint(0, 3))
        ]
    }


@pytest.mark.parametrize("seed", range(200))
@pytest.mark.parametrize(
    ("name", "build"),
    [("firewall", firewall), ("QOS", qos)],
)
@pytest.mark.parametrize("level", [ConfigContext.EDGE, ConfigContext.PROFILE])
def test_no_edit_round_trip(
    seed: int,
    name: str,
    build: Callable[[random.Random], dict[str, object]],
    level: ConfigContext,
):
    rng = random.Random(seed)
    raw = build(rng)
    sprinkle(rng, raw)
    row = make_module_json(name, copy.deepcopy(raw))
    for module in (
        ConfigurationModuleList.model_validate([copy.deepcopy(row)]).root[0],
        ConfigurationModuleList.model_validate_json(json.dumps([row])).root[0],
    ):
        plan = plan_module(module, level=level)
        assert plan.operation == "none"
        assert plan.data == raw
        assert module.raw_data == raw
