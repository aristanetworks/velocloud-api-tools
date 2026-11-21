import copy
from typing import Any

import pytest
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import ConfigConflictError
from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    GetConfiguration,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.testing import make_module_json
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.errors import StateSpentError
from pyvelo_intent.testing import MockVcoClient


def desc(sid: int, name: str) -> dict[str, object]:
    return {
        "segmentId": sid,
        "name": name,
        "type": "REGULAR",
        "segmentLogicalId": f"00000000-0000-0000-0000-{sid:012d}",
    }


EDGE_FW: dict[str, Any] = {
    "segments": [
        {"segment": desc(0, "Global Segment"), "outbound": [], "futureKey": 1}
    ],
    "syslog_forwarding": False,
}
PROFILE_FW: dict[str, Any] = {
    "segments": [
        {
            "segment": desc(0, "Global Segment"),
            "outbound": [
                {"name": "allow-all", "action": {"allow_or_deny": "allow"}, "match": {}}
            ],
        },
        {"segment": desc(2, "Guest"), "outbound": []},
    ]
}
PROFILE_QOS: dict[str, Any] = {
    "segments": [{"segment": desc(0, "Global Segment"), "rules": []}]
}


def make_state(*, edge_modules: list[dict[str, Any]] | None = None) -> ConfigState:
    edge_rows = (
        edge_modules
        if edge_modules is not None
        else [
            make_module_json(
                "firewall",
                copy.deepcopy(EDGE_FW),
                module_id=1,
                configuration_id=10,
                version="e1",
            )
        ]
    )
    edge = EdgeConfiguration.model_validate(
        {"name": "Edge Specific Profile", "id": 10, "modules": edge_rows}
    )
    profile = EnterpriseConfigurationProfile.model_validate(
        {
            "name": "P",
            "id": 20,
            "modules": [
                make_module_json(
                    "firewall",
                    copy.deepcopy(PROFILE_FW),
                    module_id=2,
                    configuration_id=20,
                ),
                make_module_json(
                    "QOS", copy.deepcopy(PROFILE_QOS), module_id=3, configuration_id=20
                ),
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


def test_plan_reports_missing_segments_without_changes():
    plan = make_state().plan()
    assert [(m.name, m.operation, m.reconciled_segments) for m in plan.modules] == [
        ("firewall", "none", ("Guest",))
    ]
    assert plan.changed == ()
    assert plan.render() == "firewall: none\n  missing segments: Guest"


async def test_save_sends_raw_payload_version_and_reconciles():
    client, state = MockVcoClient(), make_state()
    state.edge.firewall.firewall_enabled = False
    result = await ConfigPipeline(client).save(state)
    (req,) = client.update_requests
    assert (req.configuration_module_id, req.version, req.enterprise_id) == (1, "e1", 5)
    assert req.update.data == {
        **EDGE_FW,
        "segments": [
            EDGE_FW["segments"][0],
            {"segment": desc(2, "Guest"), "outbound": []},
        ],
        "firewall_enabled": False,
    }
    assert result.modules_updated == [ConfigurationModuleName.FIREWALL]
    assert result.plan is not None and result.plan.changed[0].name == "firewall"


async def test_state_is_spent_after_a_write_and_not_after_dry_run():
    client, state = MockVcoClient(), make_state()
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    dry = await pipeline.save(state, dry_run=True)
    assert state.spent is False and client.calls == []
    assert dry.modules_updated == [ConfigurationModuleName.FIREWALL]
    await pipeline.save(state)
    assert state.spent is True
    with pytest.raises(StateSpentError):
        await pipeline.save(state)
    with pytest.raises(StateSpentError):
        await pipeline.apply(state, module="firewall", patch={"firewall_enabled": True})


def test_state_spent_error_is_exported_from_the_package():
    import pyvelo_intent

    assert pyvelo_intent.StateSpentError is StateSpentError
    assert "StateSpentError" in pyvelo_intent.__all__


def test_patch_error_is_exported_from_the_package():
    import pyvelo_intent
    from pyvelo.core import PatchError

    assert pyvelo_intent.PatchError is PatchError
    assert "PatchError" in pyvelo_intent.__all__


async def test_no_edit_save_does_not_spend_or_call():
    client, state = MockVcoClient(), make_state()
    result = await ConfigPipeline(client).save(state)
    assert (client.calls, state.spent, result.modules_updated) == ([], False, [])


async def test_conflict_propagates_and_spends_the_state():
    client, state = MockVcoClient(conflict=True), make_state()
    state.edge.firewall.firewall_enabled = False
    with pytest.raises(ConfigConflictError):
        await ConfigPipeline(client).save(state)
    assert state.spent is True


async def test_missing_edge_module_is_inserted_from_an_empty_layer():
    client, state = MockVcoClient(), make_state()
    qos = state.edge.qos  # no QOS row on the edge
    assert qos.segments == []
    assert state.plan().changed == ()  # reading it plans nothing
    await ConfigPipeline(client).apply(
        state,
        module="qos",
        patch={
            "service_rate_limit": {
                "enabled": True,
                "input_type": "percent",
                "value": 50,
            }
        },
    )
    result = await ConfigPipeline(client).save(state)
    (req,) = client.insert_requests
    assert req.configuration_id == 10 and req.name == "QOS"
    assert req.data["segments"] == [{"segment": desc(0, "Global Segment"), "rules": []}]
    assert set(req.data) == {"segments", "serviceRateLimit"}
    assert result.modules_inserted == [ConfigurationModuleName.QOS]
    assert client.update_requests == []


def test_views_and_module_errors():
    state = make_state()
    assert state.profile.firewall.segments[1].segment.name == "Guest"
    with pytest.raises(ValueError):
        _ = state.edge.device_settings  # not insertable, not present
    profile_only = ConfigState(
        edge_config=None,
        profile_config=state.profile_config,
        target_type="profile",
        resolver_context=ResolverContext(),
    )
    with pytest.raises(ValueError, match="no edge layer"):
        _ = profile_only.edge


async def test_load_profile_keeps_raw_snapshots():
    raw = make_module_json(
        "firewall", copy.deepcopy(PROFILE_FW), module_id=2, configuration_id=20
    )

    class Client(MockVcoClient):
        async def get_configuration(
            self, request: GetConfiguration
        ) -> ConfigurationBase:
            return ConfigurationBase.model_validate(
                {"name": "P", "id": 20, "modules": [raw]}
            ).for_profile()

    state = await ConfigPipeline(Client()).load_profile(20)
    assert isinstance(state.profile_config, EnterpriseConfigurationProfile)
    assert state.profile_config.firewall.raw_data == PROFILE_FW
    assert state.plan().changed == ()


def test_state_binds_edge_layers_and_untouched_placeholder_is_no_change():
    state = make_state()
    guest = state.edge.firewall.segment("Guest")
    assert guest.outbound == []
    assert state.plan().changed == ()


async def test_rule_added_in_a_reconciled_segment():
    client, state = MockVcoClient(), make_state()
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
    state.edge.firewall.segment("Guest")
    await ConfigPipeline(client).save(state)
    segs = client.update_requests[0].update.data["segments"]
    # no profile rule copied in
    assert [r["name"] for r in segs[0]["outbound"]] == ["block-ssh"]
    assert segs[1] == {"segment": desc(2, "Guest"), "outbound": []}


def test_new_empty_module_is_bound():
    state = make_state()
    assert state.edge.qos.segment("Global Segment").rules == []


async def test_apply_creates_the_edge_segment_entry_on_write_only():
    pipeline = ConfigPipeline(MockVcoClient())
    with pytest.raises(ValueError):
        make_state().edge.firewall.get_segment(name="Guest")
    state = make_state()
    await pipeline.apply(
        state,
        module="firewall",
        patch={
            "segments": {
                "Guest": {
                    "outbound": {
                        "block": {"action": {"allow_or_deny": "deny"}, "match": {}}
                    }
                }
            }
        },
    )
    assert [r.name for r in state.edge.firewall.segment("Guest").outbound] == ["block"]
