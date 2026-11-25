"""End-to-end saves: what reaches the VCO is the raw data as read plus the caller's edit."""

import copy
from typing import Any

import pytest
from pyvelo.client.exceptions import GroupOwnershipError, LossyFieldError
from pyvelo.client.schemas.configuration import (
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
)
from pyvelo.config import PatchError, add, delete
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.config.state import ConfigState
from pyvelo.testing import MockVcoClient, make_module_json, make_segment_resolver
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
    """Resolvers that know the profile's segments, as ``ConfigPipeline.load`` gives a state."""
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
    state = edge_state(raw)
    client = MockVcoClient.for_state(state)
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
    state = edge_state(raw)
    client = MockVcoClient.for_state(state)
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
    state = edge_state(raw)
    client = MockVcoClient.for_state(state)
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
    state = edge_state(raw)
    client = MockVcoClient.for_state(state)
    await ConfigPipeline(client).apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "c": add(
                            {"action": {"allow_or_deny": "deny"}, "match": {}},
                            first=True,
                        ),
                        "a": delete(),
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
    state = make_state(edge_modules=[])
    client = MockVcoClient.for_state(state)
    await ConfigPipeline(client).apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Global Segment": {
                    "outbound": {
                        "block-ssh": add(
                            {"action": {"allow_or_deny": "deny"}, "match": {}}
                        )
                    }
                }
            }
        },
    )
    await ConfigPipeline(client).save(state)
    (req,) = client.insert_requests
    assert client.update_requests == []
    segments = req.data["segments"]
    assert [s["segment"]["name"] for s in segments] == ["Global Segment", "Guest"]
    assert segments[0]["segment"] == desc(0, "Global Segment")
    assert [r["name"] for r in segments[0]["outbound"]] == ["block-ssh"]
    assert segments[1] == {"segment": desc(2, "Guest"), "outbound": []}
    assert "allow-all" not in str(req.data)


async def test_segment_key_in_another_case_edits_the_segment_it_names():
    state = make_state()
    client = MockVcoClient.for_state(state)
    state.resolvers = await segment_resolvers()
    pipeline = ConfigPipeline(client)
    await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {"global segment": {"outbound": {"x": add(rule("x"))}}}},
    )
    await pipeline.save(state)
    segments = client.update_requests[0].update.data["segments"]
    assert [s["segment"] for s in segments] == [
        desc(0, "Global Segment"),
        desc(2, "Guest"),  # reconciled from the profile, not added twice
    ]
    assert [r["name"] for r in segments[0]["outbound"]] == ["x"]


@pytest.mark.parametrize("key", ["Guest", "guest"])
@pytest.mark.parametrize("with_resolver", [False, True])
async def test_new_segment_by_name_takes_the_profiles_descriptor(
    key: str, with_resolver: bool
):
    state = make_state()
    client = MockVcoClient.for_state(state)
    if with_resolver:
        state.resolvers = await segment_resolvers()
    pipeline = ConfigPipeline(client)
    await pipeline.apply(
        state,
        module="firewall",
        patch={"segments": {key: {"outbound": {"x": add(rule("x"))}}}},
    )
    await pipeline.save(state)
    segments = client.update_requests[0].update.data["segments"]
    assert [s["segment"] for s in segments] == [
        desc(0, "Global Segment"),
        desc(2, "Guest"),
    ]
    assert [r["name"] for r in segments[1]["outbound"]] == ["x"]


async def test_segment_the_profile_lacks_is_refused_without_a_resolver():
    client, state = MockVcoClient(), make_state()
    pipeline = ConfigPipeline(client)
    with pytest.raises(LookupError, match="Nowhere"):
        await pipeline.apply(
            state,
            module="firewall",
            patch={"segments": {"Nowhere": add({"outbound": {"x": add(rule("x"))}})}},
        )
    assert client.calls == []


async def test_a_patch_that_names_one_segment_twice_is_refused():
    state = make_state()
    with pytest.raises(PatchError, match="Guest"):
        await ConfigPipeline(MockVcoClient()).apply(
            state,
            module="firewall",
            patch={"segments": {"Guest": {}, "guest": {}}},
        )


async def test_deleting_an_edge_segment_the_profile_has_leaves_an_empty_one():
    raw: dict[str, Any] = {
        "segments": [
            global_segment(),
            {"segment": desc(2, "Guest"), "outbound": [rule("g")]},
        ]
    }
    state = edge_state(raw)
    client = MockVcoClient.for_state(state)
    await ConfigPipeline(client).apply(
        state, module="firewall", patch={"segments": {"Guest": delete()}}
    )
    await ConfigPipeline(client).save(state)
    segments = client.update_requests[0].update.data["segments"]
    assert segments == [
        global_segment(),
        {"segment": desc(2, "Guest"), "outbound": []},
    ]


async def test_setting_a_default_on_a_key_the_edge_lacks_is_refused():
    raw: dict[str, Any] = {"segments": [global_segment()]}
    state = edge_state(raw)
    client = MockVcoClient.for_state(state)
    state.edge.firewall.inbound = []
    with pytest.raises(LossyFieldError) as refused:
        await ConfigPipeline(client).save(state)
    assert refused.value.fields == ["inbound"]
    assert client.calls == []


async def test_setting_one_member_of_an_inherited_group_is_refused_at_once():
    raw: dict[str, Any] = {"segments": [global_segment()]}
    state = edge_state(raw)
    with pytest.raises(GroupOwnershipError, match="atp"):
        state.edge.firewall.atp_enabled.set_local(False)
    assert state.plan().changed == ()
    # The group, made local, writes every member: the settings as the profile
    # (here the display default) has them.
    state.edge.firewall.atp.localize()
    state.edge.firewall.atp_enabled.set_local(False)
    (plan,) = state.plan().changed
    assert [str(c.path) for c in plan.changes] == ["atp_enabled", "securityFeatures"]


def profile_client(raw: dict[str, Any] = PROFILE_FW) -> MockVcoClient:
    """A client whose profile 20 has a firewall module with *raw* as its data."""
    row = make_module_json(
        "firewall", copy.deepcopy(raw), module_id=2, configuration_id=20, version="p1"
    )
    return MockVcoClient().seed(
        EnterpriseConfigurationProfile.model_validate(
            {"name": "P", "id": 20, "modules": [row]}
        )
    )


async def test_edit_to_a_section_the_edge_dump_omits_is_refused():
    raw: dict[str, Any] = {
        "lan": {"networks": [], "interfaces": []},
        "segments": [{"segment": desc(0, "Global Segment")}],
        "ntp": {"override": True, "enabled": False, "servers": []},
    }
    row = make_module_json(
        "deviceSettings", raw, module_id=7, configuration_id=10, version="d1"
    )
    state = make_state(edge_modules=[row])
    client = MockVcoClient.for_state(state)
    _ = state.edge.device_settings.merge_patch({"ntp": {"enabled": True}})
    with pytest.raises(LossyFieldError) as refused:
        await ConfigPipeline(client).save(state)
    assert refused.value.fields == ["ntp"]
    assert client.calls == []


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
        segment="Global Segment",
        path="outbound",
        patch={"block": add(rule("block"))},
    )
    result = await pipeline.save(state, dry_run=True)
    assert result.plan is not None
    assert result.plan.render().startswith(
        'profile "P", configuration 20\nfirewall: update\n'
        '  + segments["Global Segment"].outbound[block] '
        "(after allow-all): "
    )
    await pipeline.save(state)
    (req,) = client.update_requests
    first = req.update.data["segments"][0]
    assert [r["name"] for r in first["outbound"]] == ["allow-all", "block"]
    assert set(first) == {"segment", "outbound"}  # no override flag at profile level
    assert req.update.data["segments"][1] == PROFILE_FW["segments"][1]


async def test_dry_run_reports_the_plan():
    client, state = MockVcoClient(), make_state()
    state.edge.firewall.firewall_enabled.set_local(False)
    result = await ConfigPipeline(client).save(state, dry_run=True)
    assert result.plan is not None
    rendered = result.plan.render()
    assert "+ firewall_enabled: false" in rendered
    assert "reconciled segments: Guest" in rendered
    assert client.calls == []


# --- the edge layer is bound to the profile's firewall data ------------------

PROFILE_SERVICES: dict[str, Any] = {"loggingEnabled": True, "ssh": {"enabled": True}}


def state_over_profile_with_services(
    edge_modules: list[dict[str, Any]],
) -> ConfigState:
    """An edge state with *edge_modules*, whose profile's firewall stores ``services``."""
    state = make_state(edge_modules=edge_modules)
    profile_row = make_module_json(
        "firewall",
        {**copy.deepcopy(PROFILE_FW), "services": PROFILE_SERVICES},
        module_id=2,
        configuration_id=20,
    )
    profile = EnterpriseConfigurationProfile.model_validate(
        {"name": "P", "id": 20, "modules": [profile_row]}
    )
    state = ConfigState(
        edge_config=state.edge_config,
        profile_config=profile,
        target_type="edge",
        resolver_context=state.resolver_context,
    )
    state.take_snapshot()
    return state


async def test_a_loaded_edge_localizes_from_the_profiles_firewall_data():
    state = state_over_profile_with_services(
        [
            make_module_json(
                "firewall",
                {"segments": [global_segment()]},
                module_id=1,
                configuration_id=10,
                version="e1",
            )
        ]
    )
    services = state.edge.firewall.services.localize()
    assert services.logging_enabled is True  # pyright: ignore[reportOptionalMemberAccess]
    assert state.profile.firewall.services.logging_enabled is True  # pyright: ignore[reportOptionalMemberAccess]
    (plan,) = state.plan().changed
    assert [str(c.path) for c in plan.changes] == ["services"]


async def test_a_created_module_localizes_from_the_profiles_firewall_data():
    state = state_over_profile_with_services([])
    client = MockVcoClient.for_state(state)
    await ConfigPipeline(client).apply(
        state, module="firewall", patch={"services": {"ssh": {"enabled": False}}}
    )
    (plan,) = state.plan().changed
    assert plan.operation == "insert"
    services = plan.data["services"]
    # The profile's services, with the edit: not the display default.
    assert services["loggingEnabled"] is True
    assert services["ssh"]["enabled"] is False


async def test_a_replaced_module_localizes_from_the_profiles_firewall_data():
    row = make_module_json(
        "firewall",
        {"segments": [global_segment()]},
        module_id=1,
        configuration_id=10,
        version="e1",
    )
    state = state_over_profile_with_services([row])
    stored = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "id": 10, "modules": [row]}
    ).firewall
    state.replace_module(state.module("firewall"), stored)
    services = state.edge.firewall.services.localize()
    assert services.logging_enabled is True  # pyright: ignore[reportOptionalMemberAccess]
