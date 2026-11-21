"""End-to-end saves: what reaches the VCO is the raw data as read plus the caller's edit."""

import copy
from typing import Any

import pytest
from pyvelo.client.exceptions import LossyFieldError
from pyvelo.client.schemas.configuration import ConfigurationBase, GetConfiguration
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.testing import make_module_json, make_segment_resolver
from pyvelo_intent.config.pipeline import ConfigPipeline, build_default_pipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from test_state_plan import (  # pyright: ignore[reportImplicitRelativeImport]
    PROFILE_FW,
    desc,
    make_state,
)


def rule(name: str, **extra: object) -> dict[str, Any]:
    return {"name": name, "action": {"allow_or_deny": "allow"}, "match": {}, **extra}


def edge_state(raw: dict[str, Any]) -> ConfigState:
    """A state whose edge has a firewall module with *raw* as its data."""
    row = make_module_json(
        "firewall", copy.deepcopy(raw), module_id=1, configuration_id=10, version="e1"
    )
    return make_state(edge_modules=[row])


async def segment_resolvers() -> ResolverRegistry:
    """Resolvers that know the profile's segments, as ``ConfigPipeline.load`` leaves them."""
    registry = ResolverRegistry()
    registry.register(
        await make_segment_resolver(
            [
                {
                    "name": "Global Segment",
                    "segment_id": 0,
                    "segment_logical_id": desc(0, "Global Segment")["segmentLogicalId"],
                },
                {
                    "name": "Guest",
                    "segment_id": 2,
                    "segment_logical_id": desc(2, "Guest")["segmentLogicalId"],
                },
            ]
        )
    )
    return registry


def global_segment(*rules: dict[str, Any]) -> dict[str, Any]:
    return {"segment": desc(0, "Global Segment"), "outbound": list(rules)}


async def test_inherited_section_survives_unrelated_edit():
    raw: dict[str, Any] = {
        "segments": [global_segment()],
        "syslog_forwarding": False,
    }
    client, state = MockVcoClient(), edge_state(raw)
    await ConfigPipeline(client).apply(
        state, module="firewall", patch={"firewall_enabled": False}
    )
    await ConfigPipeline(client).save(state)
    payload = client.update_requests[0].update.data
    assert set(payload) == {*raw, "firewall_enabled"}
    assert "services" not in payload
    assert "stateful_firewall_enabled" not in payload


async def test_explicit_local_false_survives():
    raw: dict[str, Any] = {"segments": [global_segment()], "syslog_forwarding": False}
    client, state = MockVcoClient(), edge_state(raw)
    await ConfigPipeline(client).apply(
        state, module="firewall", patch={"firewall_enabled": False}
    )
    await ConfigPipeline(client).save(state)
    payload = client.update_requests[0].update.data
    assert payload["syslog_forwarding"] is False


async def test_snake_case_logging_key_is_edited_in_place():
    raw: dict[str, Any] = {
        "segments": [global_segment()],
        "firewall_logging_enabled": False,
    }
    client, state = MockVcoClient(), edge_state(raw)
    await ConfigPipeline(client).apply(
        state, module="firewall", patch={"firewall_logging_enabled": True}
    )
    await ConfigPipeline(client).save(state)
    payload = client.update_requests[0].update.data
    assert payload["firewall_logging_enabled"] is True
    assert "firewallLoggingEnabled" not in payload


async def test_edge_rules_add_delete_reorder():
    raw: dict[str, Any] = {
        "segments": [global_segment(rule("a", keep=1), rule("b", keep=2))]
    }
    client, state = MockVcoClient(), edge_state(raw)
    await ConfigPipeline(client).apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "c": {
                            "_position": 0,
                            "action": {"allow_or_deny": "deny"},
                            "match": {},
                        },
                        "a": None,
                    }
                }
            }
        },
    )
    await ConfigPipeline(client).save(state)
    payload = client.update_requests[0].update.data
    outbound = payload["segments"][0]["outbound"]
    assert [r["name"] for r in outbound] == ["c", "b"]
    assert outbound[1] == rule("b", keep=2)
    assert "allow-all" not in str(payload)  # the profile's rule is not copied in


async def test_missing_module_insert_has_only_local_content():
    client, state = MockVcoClient(), make_state(edge_modules=[])
    await ConfigPipeline(client).apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "block-ssh": {"action": {"allow_or_deny": "deny"}, "match": {}}
                    }
                }
            }
        },
    )
    await ConfigPipeline(client).save(state)
    (req,) = client.insert_requests
    assert client.update_requests == []
    segments = req.data["segments"]
    assert [r["name"] for r in segments[0]["outbound"]] == ["block-ssh"]
    assert segments[1] == {"segment": desc(2, "Guest"), "outbound": []}
    assert "allow-all" not in str(req.data)


async def test_segment_key_in_another_case_edits_the_segment_it_names():
    client, state = MockVcoClient(), make_state()
    pipeline = ConfigPipeline(client, resolvers=await segment_resolvers())
    await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {"global segment": {"outbound": {"x": rule("x")}}}},
    )
    await pipeline.save(state)
    segments = client.update_requests[0].update.data["segments"]
    assert [s["segment"] for s in segments] == [
        desc(0, "Global Segment"),
        desc(2, "Guest"),  # reconciled from the profile, not added twice
    ]
    assert [r["name"] for r in segments[0]["outbound"]] == ["x"]


async def test_setting_a_default_on_an_inherited_key_is_refused():
    raw: dict[str, Any] = {"segments": [global_segment()]}
    client, state = MockVcoClient(), edge_state(raw)
    await ConfigPipeline(client).apply(
        state, module="firewall", patch={"atp_enabled": False}
    )
    with pytest.raises(LossyFieldError) as refused:
        await ConfigPipeline(client).save(state)
    assert refused.value.fields == ["atp_enabled"]
    assert client.calls == []
    assert state.spent is False


def profile_client(raw: dict[str, Any] = PROFILE_FW) -> MockVcoClient:
    """A client whose profile 20 has a firewall module with *raw* as its data."""
    row = make_module_json(
        "firewall", copy.deepcopy(raw), module_id=2, configuration_id=20, version="p1"
    )

    class Client(MockVcoClient):
        async def get_configuration(
            self, request: GetConfiguration
        ) -> ConfigurationBase:
            return ConfigurationBase.model_validate(
                {"name": "P", "id": 20, "modules": [row]}
            ).for_profile()

    return Client()


async def test_profile_target_save():
    client = profile_client()
    state = await ConfigPipeline(client).load_profile(20)
    state.profile.firewall.firewall_enabled = False
    await ConfigPipeline(client).save(state)
    (req,) = client.update_requests
    assert (req.configuration_module_id, req.version) == (2, "p1")
    assert req.update.data == {**PROFILE_FW, "firewall_enabled": False}
    assert client.insert_requests == []


async def test_profile_target_apply_adds_a_rule():
    client = profile_client()
    pipeline = ConfigPipeline(client)
    state = await pipeline.load_profile(20)
    await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {"Global Segment": {"outbound": {"block": rule("block")}}}},
    )
    await pipeline.save(state)
    (req,) = client.update_requests
    first = req.update.data["segments"][0]
    assert [r["name"] for r in first["outbound"]] == ["allow-all", "block"]
    assert set(first) == {"segment", "outbound"}  # no override flag at profile level
    assert req.update.data["segments"][1] == PROFILE_FW["segments"][1]


async def test_profile_target_apply_aspect_adds_a_rule():
    client = profile_client()
    pipeline = build_default_pipeline(client)
    state = await pipeline.load_profile(20)
    await pipeline.apply_aspect(
        state,
        "firewall_rules",
        {"block-ssh": rule("block-ssh")},
        segment="Global Segment",
    )
    result = await pipeline.save(state, dry_run=True)
    assert result.plan is not None
    assert result.plan.render().startswith(
        'firewall: update\n  + segments["Global Segment"].outbound[block-ssh]: '
    )
    await pipeline.save(state)
    (req,) = client.update_requests
    first = req.update.data["segments"][0]
    assert [r["name"] for r in first["outbound"]] == ["allow-all", "block-ssh"]
    assert set(first) == {"segment", "outbound"}


async def test_dry_run_reports_the_plan():
    client, state = MockVcoClient(), make_state()
    state.edge.firewall.firewall_enabled = False
    result = await ConfigPipeline(client).save(state, dry_run=True)
    assert result.plan is not None
    rendered = result.plan.render()
    assert "+ firewall_enabled: false" in rendered
    assert "reconciled segments: Guest" in rendered
    assert client.calls == []
