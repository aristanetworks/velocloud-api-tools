"""An outbound rule edit through ConfigPipeline writes the rule and no override flag.

The VCO and the edge have no ``outboundOverride`` key in a firewall module, so a
save must never send one, whatever the level and whatever the edit.

The regression guard is the absence of the model field (``FirewallSegment`` has no
override flag, see ``test_firewall_markers.py``): without it these tests fail at
the edge level. At the profile level override flags were never written, so the
profile cases are behaviour checks that pin the same payload at both levels. A key
that an older version wrote into the raw data is dropped when the module is
written, and reported (never written just to strip it).
"""

import copy
from typing import Any

import pytest
from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    GetConfiguration,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.testing import make_module_json
from pyvelo_intent.config.pipeline import ConfigPipeline, build_default_pipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


def rule(name: str, verdict: str = "allow") -> dict[str, Any]:
    return {"name": name, "action": {"allow_or_deny": verdict}, "match": {}}


RAW: dict[str, Any] = {
    "segments": [
        {"segment": desc(0, "Global Segment"), "outbound": [rule("keep"), rule("mine")]}
    ]
}
OUTBOUND = 'segments["Global Segment"].outbound'


def has_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(has_key(v, key) for v in value.values())  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return any(has_key(v, key) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return False


def edge_state(raw: dict[str, Any] = RAW) -> ConfigState:
    edge = EdgeConfiguration.model_validate(
        {
            "name": "Edge Specific Profile",
            "id": 10,
            "modules": [
                make_module_json(
                    "firewall",
                    copy.deepcopy(raw),
                    module_id=1,
                    configuration_id=10,
                    version="e1",
                )
            ],
        }
    )
    profile = EnterpriseConfigurationProfile.model_validate(
        {
            "name": "P",
            "id": 20,
            "modules": [
                make_module_json(
                    "firewall", copy.deepcopy(RAW), module_id=2, configuration_id=20
                )
            ],
        }
    )
    state = ConfigState(
        edge_config=edge,
        profile_config=profile,
        target_type="edge",
        resolver_context=ResolverContext(enterprise_id=5),
    )
    state.take_snapshot()
    return state


async def profile_state() -> ConfigState:
    row = make_module_json(
        "firewall", copy.deepcopy(RAW), module_id=2, configuration_id=20, version="p1"
    )

    class Client(MockVcoClient):
        async def get_configuration(
            self, request: GetConfiguration
        ) -> ConfigurationBase:
            return ConfigurationBase.model_validate(
                {"name": "P", "id": 20, "modules": [row]}
            ).for_profile()

    state = await ConfigPipeline(Client()).load_profile(20)
    assert state.target_type == "profile"
    return state


EDITS = [
    pytest.param(
        {"new": {"action": {"allow_or_deny": "deny"}, "match": {}}},
        f"{OUTBOUND}[new]",
        id="add",
    ),
    pytest.param(
        {"mine": {"action": {"allow_or_deny": "deny"}}},
        f"{OUTBOUND}[mine].action.allow_or_deny",
        id="edit",
    ),
    pytest.param({"mine": None}, f"{OUTBOUND}[mine]", id="delete"),
]


@pytest.mark.parametrize("level", ["edge", "profile"])
@pytest.mark.parametrize(("rules", "path"), EDITS)
async def test_apply_of_an_outbound_rule_writes_the_rule_only(
    level: str, rules: dict[str, Any], path: str
):
    state = edge_state() if level == "edge" else await profile_state()
    client = MockVcoClient()
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {"Global Segment": {"outbound": rules}}},
    )

    (plan,) = state.plan().changed
    assert plan.operation == "update"
    assert [str(c.path) for c in plan.changes] == [path]
    assert not has_key(plan.data, "outboundOverride")

    _ = await pipeline.save(state)
    (request,) = client.update_requests
    assert not has_key(request.update.data, "outboundOverride")
    assert request.update.data["segments"][0]["segment"] == desc(0, "Global Segment")


@pytest.mark.parametrize("level", ["edge", "profile"])
async def test_apply_aspect_of_an_outbound_rule_writes_the_rule_only(level: str):
    state = edge_state() if level == "edge" else await profile_state()
    client = MockVcoClient()
    pipeline = build_default_pipeline(client)  # with the built-in aspects
    _ = await pipeline.apply_aspect(
        state,
        "firewall_rules",
        {"new": {"action": {"allow_or_deny": "deny"}, "match": {}}},
        segment="Global Segment",
    )

    (plan,) = state.plan().changed
    assert [str(c.path) for c in plan.changes] == [f"{OUTBOUND}[new]"]
    assert not has_key(plan.data, "outboundOverride")

    _ = await pipeline.save(state)
    (request,) = client.update_requests
    assert not has_key(request.update.data, "outboundOverride")
    assert request.update.data["segments"][0]["segment"] == desc(0, "Global Segment")


# --- a key an older version wrote into the raw data -------------------------

STRAY: dict[str, Any] = {
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "outboundOverride": True,
            "outbound": [rule("keep"), rule("mine")],
        }
    ]
}
STRAY_PATH = 'segments["Global Segment"].outboundOverride'


async def test_a_stray_override_key_with_no_edit_is_reported_and_not_written():
    state, client = edge_state(STRAY), MockVcoClient()
    plan = state.plan()
    assert plan.changed == ()
    assert [str(k) for k in plan.modules[0].legacy_keys] == [STRAY_PATH]
    assert STRAY_PATH in plan.render()

    result = await ConfigPipeline(client).save(state)
    assert result.modules_updated == [] and client.update_requests == []


async def test_a_stray_override_key_is_dropped_when_the_module_is_written():
    state, client = edge_state(STRAY), MockVcoClient()
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {"Global Segment": {"outbound": {"mine": None}}}},
    )

    (plan,) = state.plan().changed
    assert {str(c.path) for c in plan.changes} == {f"{OUTBOUND}[mine]", STRAY_PATH}
    assert f"- {STRAY_PATH}" in plan.render()

    _ = await pipeline.save(state)
    (request,) = client.update_requests
    assert not has_key(request.update.data, "outboundOverride")
    assert [r["name"] for r in request.update.data["segments"][0]["outbound"]] == [
        "keep"
    ]
