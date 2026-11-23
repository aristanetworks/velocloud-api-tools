import copy
from typing import Any

import pytest
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import (
    ConfigConflictError,
    DetachedObjectError,
    ModuleWriteError,
    WriteNotAppliedError,
    WriteOutcomeUnknownError,
)
from pyvelo.client.schemas.configuration import (
    ConfigurationBase,
    EdgeConfiguration,
    EnterpriseConfigurationProfile,
    GetConfiguration,
)
from pyvelo.config.resolver import ResolverContext
from pyvelo.testing import make_module_json
from pyvelo_intent import add, delete
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
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
    assert plan.render() == (
        "edge, enterprise 5, configuration 10\n"
        "firewall: none\n"
        "  missing segments: Guest"
    )


def test_plan_names_its_target():
    state = make_state()
    assert state.plan().target == "edge, enterprise 5, configuration 10"
    state.resolver_context.edge_id = 101
    assert state.plan().target == "edge 101, enterprise 5, configuration 10"
    profile = ConfigState(
        edge_config=None,
        profile_config=state.profile_config,
        target_type="profile",
        resolver_context=ResolverContext(),
    )
    assert profile.plan().render().splitlines()[0] == 'profile "P", configuration 20'


async def test_save_sends_raw_payload_version_and_reconciles():
    state = make_state()
    client = MockVcoClient.for_state(state)
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


async def test_state_keeps_working_after_a_write():
    state = make_state()
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    state.edge.firewall.firewall_enabled = False
    dry = await pipeline.save(state, dry_run=True)
    assert client.calls == []
    assert dry.modules_updated == [ConfigurationModuleName.FIREWALL]
    await pipeline.save(state)
    assert len(client.calls) == 1
    again = await pipeline.save(state)
    assert len(client.calls) == 1
    assert (again.modules_updated, again.modules_inserted) == ([], [])
    await pipeline.apply(state, module="firewall", patch={"firewall_enabled": True})
    assert [m.name for m in state.plan().changed] == ["firewall"]


def test_write_errors_are_exported_from_the_package():
    import pyvelo_intent

    assert pyvelo_intent.ModuleWriteError is ModuleWriteError
    assert pyvelo_intent.ConfigConflictError is ConfigConflictError
    assert pyvelo_intent.WriteNotAppliedError is WriteNotAppliedError
    assert pyvelo_intent.WriteOutcomeUnknownError is WriteOutcomeUnknownError
    assert pyvelo_intent.DetachedObjectError is DetachedObjectError
    assert {
        "ModuleWriteError",
        "ConfigConflictError",
        "WriteNotAppliedError",
        "WriteOutcomeUnknownError",
        "DetachedObjectError",
    } <= set(pyvelo_intent.__all__)
    assert not hasattr(pyvelo_intent, "StateSpentError")


def test_patch_error_is_exported_from_the_package():
    import pyvelo_intent
    from pyvelo.core import PatchError

    assert pyvelo_intent.PatchError is PatchError
    assert "PatchError" in pyvelo_intent.__all__


async def test_no_edit_save_does_not_call():
    state = make_state()
    client = MockVcoClient.for_state(state)
    result = await ConfigPipeline(client).save(state)
    assert (client.calls, result.modules_updated) == ([], [])


@pytest.mark.parametrize(
    ("conditional_check", "raised"),
    [(True, ConfigConflictError), (False, WriteNotAppliedError)],
)
async def test_conflict_propagates(
    conditional_check: bool, raised: type[ConfigConflictError | WriteNotAppliedError]
):
    # Another writer changed the path edited here: a VCO with the check off
    # stores nothing and returns its row, which the check finds a clash in.
    state = make_state()
    client = MockVcoClient.for_state(state, conditional_check=conditional_check)
    client.store.other_writer(1, lambda row: row["data"].update(firewall_enabled=True))
    state.edge.firewall.firewall_enabled = False
    with pytest.raises(raised) as exc:
        await ConfigPipeline(client).save(state)
    (clash,) = [c for c in exc.value.lost_edits if str(c.path) == "firewall_enabled"]
    assert clash.clash


async def test_missing_edge_module_is_inserted_from_an_empty_layer():
    state = make_state()
    client = MockVcoClient.for_state(state)
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
    state = make_state()
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
        segment="Guest",
        path="outbound",
        patch={"block": add({"action": {"allow_or_deny": "deny"}, "match": {}})},
    )
    assert [r.name for r in state.edge.firewall.segment("Guest").outbound] == ["block"]


_DENY: dict[str, object] = {"action": {"allow_or_deny": "deny"}, "match": {}}


async def test_first_patch_to_a_module_the_edge_lacks_keeps_the_profiles_segment_order():
    state = make_state(edge_modules=[])
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    _ = await pipeline.apply(
        state,
        module="firewall",
        segment="Guest",
        path="outbound",
        patch={"tmp": add(_DENY)},
    )
    (module,) = state.plan().changed
    assert module.operation == "insert"
    assert [str(c.path) for c in module.changes] == ["segments[Guest].outbound[tmp]"]
    assert module.reconciled_segments == ("Global Segment", "Guest")
    result = await pipeline.save(state)
    assert result.modules_inserted == [ConfigurationModuleName.FIREWALL]
    (request,) = client.insert_requests
    assert [s["segment"] for s in request.data["segments"]] == [
        desc(0, "Global Segment"),
        desc(2, "Guest"),
    ]
    assert [r["name"] for r in request.data["segments"][1]["outbound"]] == ["tmp"]


async def test_two_patches_that_cancel_out_leave_no_insert_pending():
    state = make_state(edge_modules=[])
    client = MockVcoClient.for_state(state)
    pipeline = ConfigPipeline(client)
    for patch in ({"tmp": add(_DENY)}, {"tmp": delete()}):
        _ = await pipeline.apply(
            state, module="firewall", segment="Guest", path="outbound", patch=patch
        )
    plan = state.plan()
    assert plan.changed == ()
    assert plan.render() == (
        "edge, enterprise 5, configuration 10\n"
        "firewall: none\n"
        "  missing segments: Global Segment, Guest"
    )
    result = await pipeline.save(state)
    assert (result.modules_inserted, result.modules_updated) == ([], [])
    assert client.calls == []
