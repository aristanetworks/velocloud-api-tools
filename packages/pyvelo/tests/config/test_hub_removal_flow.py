"""The hub-removal flow end to end over the mock store.

A hub taken out of a segment's lists while a QoS rule still backhauls to it:
``apply`` accepts the edit, ``plan()`` refuses the state with the patches that
mend it as data, and the patches of one kind sent as given, or taking the edit
back, mend it.
"""

import dataclasses
import json
from typing import Any

import pytest
from hub_helpers import (
    HUB_A,
    backhaul_entry,
    backhaul_row,
    backhaul_rule,
    ds_with_vpn,
    hub_row,
)
from hub_state import hub_pipeline, hub_profile_state, profile_hub_rows
from pyvelo.client.exceptions import VcoError
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.config import (
    ConfigPipeline,
    ConfigState,
    DanglingReferenceError,
    MendingPatch,
    SaveStepError,
    save_progress,
)
from pyvelo.core import backhaul_hub_mending, delete
from pyvelo.testing import ModuleStore
from refs_helpers import CONFIG, DS_ID, QOS_ID, segment_row
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry

DS = "deviceSettings"
QOS = "QOS"
UPDATE = "configuration/updateConfigurationModule"
DISABLE_HUB = "edge/disableEdgeForEdgeHub"
SERVICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SERVICE_ID = 601
GONE = {HUB_A.name: delete()}
FROM_BACKHAUL: dict[str, Any] = {
    "segments": {"Guest": {"vpn": {"backHaulEdges": GONE}}}
}
FROM_BOTH_LISTS: dict[str, Any] = {
    "segments": {
        "Guest": {
            "vpn": {
                "backHaulEdges": GONE,
                "edgeToEdgeHub": {"enabled": False, "vpnHubs": GONE},
            }
        }
    }
}
RULE_ENTRY = {"Hub-A": {"_op": "delete"}}
OUT_OF_THE_RULE = {
    "segments": {
        "Guest": {
            "rules": {
                "Web": {
                    "action": {
                        "edge2EdgeRouteAction": {
                            "routeCfg": {"backhaulEdges": RULE_ENTRY}
                        }
                    }
                }
            }
        }
    }
}


async def rule_uses_hub() -> ConfigState:
    """Guest has Hub-A as hub and backhaul hub, and its rule ``Web`` backhauls to it."""
    refs = profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        hub_row(
            HUB_A,
            service_id=SERVICE_ID,
            service_logical_id=SERVICE,
            segment=GUEST,
            roles=("hub", "backhaul"),
        ),
    )
    backhaul = backhaul_row(
        HUB_A,
        service_id=SERVICE_ID,
        service_logical_id=SERVICE,
        segment=GUEST,
        module_id=QOS_ID,
    )
    rule = backhaul_rule("Web", backhaul_entry(HUB_A, SERVICE))
    return await hub_profile_state(
        [ds_entry(GLOBAL), ds_with_vpn(GUEST, hubs=(HUB_A,), backhaul=(HUB_A,))],
        [qos_entry(GLOBAL), qos_entry(GUEST, rules=[rule])],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
        qos_refs={BACKHAUL_REF: backhaul},
    )


async def rules_use_hub_two_ways() -> ConfigState:
    """Guest's rules ``Web`` and ``Mail`` backhaul to Hub-A, as read.

    ``Web``'s entry names it by name, ``Mail``'s by logical ID only. Guest has
    Hub-A as hub and backhaul hub.
    """
    refs = profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        hub_row(
            HUB_A,
            service_id=SERVICE_ID,
            service_logical_id=SERVICE,
            segment=GUEST,
            roles=("hub", "backhaul"),
        ),
    )
    backhaul = backhaul_row(
        HUB_A,
        service_id=SERVICE_ID,
        service_logical_id=SERVICE,
        segment=GUEST,
        module_id=QOS_ID,
    )
    named = backhaul_entry(HUB_A, SERVICE)
    unnamed = {key: value for key, value in named.items() if key != "name"}
    mail = backhaul_rule("Mail", unnamed)
    mail["ruleLogicalId"] = "e7e7e7e7-e7e7-4e7e-8e7e-e7e7e7e7e7e7"
    rules = [backhaul_rule("Web", named), mail]
    return await hub_profile_state(
        [ds_entry(GLOBAL), ds_with_vpn(GUEST, hubs=(HUB_A,), backhaul=(HUB_A,))],
        [qos_entry(GLOBAL), qos_entry(GUEST, rules=rules)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
        qos_refs={BACKHAUL_REF: backhaul},
    )


async def edit(state: ConfigState, module: str, patch: dict[str, Any]) -> None:
    _ = await ConfigPipeline(client=None).apply(state, module=module, patch=patch)


def refused(state: ConfigState) -> DanglingReferenceError:
    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()
    return error.value


async def send(state: ConfigState, mending: MendingPatch) -> None:
    """Send *mending* as it is given: its fields are ``apply``'s arguments."""
    _ = await ConfigPipeline(client=None).apply(
        state,
        module=mending.module,
        patch=mending.patch,
        segment=mending.segment,
        path=mending.path,
    )


async def test_apply_accepts_the_removal_and_plan_refuses_with_both_patches() -> None:
    state = await rule_uses_hub()
    await edit(state, DS, FROM_BOTH_LISTS)  # accepted: plan() is the judge

    error = refused(state)

    remove, add = error.mending
    assert (remove.kind, remove.target_type, remove.module) == (
        "remove_reference",
        "profile",
        QOS,
    )
    assert remove.patch == OUT_OF_THE_RULE
    assert add == backhaul_hub_mending("Guest", "Hub-A")
    for mending in error.mending:
        as_json = dataclasses.asdict(mending)
        assert json.loads(json.dumps(as_json)) == as_json
        assert f"send {json.dumps(mending.patch)} to {mending.module}" in str(error)
    assert "`revert`" in str(error)


@pytest.mark.parametrize("kind", ["remove_reference", "add_member"])
@pytest.mark.parametrize(
    "removal", [FROM_BOTH_LISTS, FROM_BACKHAUL], ids=["both lists", "backhaul only"]
)
async def test_the_patches_of_one_kind_sent_as_given_mend_the_state(
    removal: dict[str, Any], kind: str
) -> None:
    state = await rule_uses_hub()
    await edit(state, DS, removal)
    error = refused(state)
    assert {m.kind for m in error.mending} == {"remove_reference", "add_member"}

    for mending in error.mending:
        if mending.kind == kind:
            await send(state, mending)

    _ = state.plan()  # no longer refused


async def test_the_edits_go_in_either_order() -> None:
    state = await rule_uses_hub()
    await edit(state, QOS, OUT_OF_THE_RULE)
    await edit(state, DS, FROM_BOTH_LISTS)

    assert [m.name for m in state.plan().changed] == [QOS, DS]


async def test_taking_the_removal_back_mends_too() -> None:
    state = await rule_uses_hub()
    await edit(state, DS, FROM_BOTH_LISTS)
    _ = refused(state)

    _ = await ConfigPipeline(client=None).revert(state, module=DS)

    assert state.plan().changed == ()


@pytest.mark.parametrize("kind", ["remove_reference", "add_member"])
async def test_two_rules_that_name_the_hub_two_ways_are_mended_by_either_kind(
    kind: str,
) -> None:
    state = await rules_use_hub_two_ways()
    await edit(state, DS, FROM_BOTH_LISTS)
    error = refused(state)
    assert [m.kind for m in error.mending] == [
        "remove_reference",
        "remove_reference",
        "add_member",
    ]

    for mending in error.mending:
        if mending.kind == kind:
            await send(state, mending)

    _ = state.plan()  # no longer refused


def sent(store: ModuleStore) -> list[tuple[str, int]]:
    """The writes and the role calls the store got, each with the ID it names."""
    return [
        (method, params["id"])
        for method, params in store.requests
        if method == UPDATE or method.startswith(("edge/enable", "edge/disable"))
    ]


async def removal_and_its_rule() -> tuple[ConfigPipeline, ModuleStore, ConfigState]:
    state = await rule_uses_hub()
    await edit(state, DS, FROM_BOTH_LISTS)
    await edit(state, QOS, OUT_OF_THE_RULE)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    return pipeline, store, state


async def test_the_plan_lists_the_device_settings_last() -> None:
    _, _, state = await removal_and_its_rule()

    plan = state.plan()

    assert [m.name for m in plan.modules] == [QOS, "firewall", DS]
    lines = plan.render().splitlines()
    assert lines.index("steps:") > lines.index("firewall: none")
    assert lines[lines.index("steps:") + 1 :][:2] == [
        "  disable hub for Hub-A on segment Guest",
        "deviceSettings: update",
    ]


async def test_the_save_writes_the_rule_first() -> None:
    pipeline, store, state = await removal_and_its_rule()

    dry = await pipeline.save(state, dry_run=True)
    result = await pipeline.save(state)

    assert [name.value for name in dry.modules_updated] == [QOS, DS]
    assert sent(store) == [
        (UPDATE, QOS_ID),
        (DISABLE_HUB, HUB_A.id),
        (UPDATE, DS_ID),
    ]
    assert [w.name for w in result.written] == [QOS, DS]
    assert [o.status for o in result.steps] == ["sent"]
    assert (state.plan().changed, state.plan().steps) == ((), ())
    again = await pipeline.load_profile(CONFIG)
    guest = again.profile.device_settings.segments[1].vpn
    assert (guest.hub_edges.vpn_hubs, guest.backhaul_edges) == ([], [])
    rule = again.profile.qos.segments[1].rules[0]
    assert rule.action.edge_to_edge_route_action.route_cfg.backhaul_edges == []
    assert again.plan().changed == ()


async def test_a_rule_write_that_fails_leaves_the_hub_and_the_rule_stored() -> None:
    pipeline, store, state = await removal_and_its_rule()
    store.fail_writes.add(1)  # the QoS write is the first

    with pytest.raises(VcoError) as failed:
        _ = await pipeline.save(state)

    assert not isinstance(failed.value, SaveStepError)
    assert sent(store) == [(UPDATE, QOS_ID)]  # the failed write; no step
    progress = save_progress(failed.value)
    assert progress is not None
    assert (progress.completed, progress.failed, progress.pending) == ((), QOS, (DS,))
    assert [o["name"] for o in store.objects.values() if o["type"] == "edgeHub"] == [
        "Hub-A"
    ]
    result = await pipeline.save(state)
    assert [w.name for w in result.written] == [QOS, DS]
    assert state.plan().changed == ()


async def test_a_second_save_finishes_after_a_failed_last_write() -> None:
    pipeline, store, state = await removal_and_its_rule()
    store.fail_writes.add(2)  # the device settings write, after the step

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    error = failed.value
    assert (error.module_name, error.step, error.written) == (DS, None, (QOS,))
    assert [s.render() for s in error.steps_done] == [
        "disable hub for Hub-A on segment Guest"
    ]
    assert [m.name for m in state.plan().changed] == [DS]
    store.requests.clear()
    result = await pipeline.save(state)
    assert [o.status for o in result.steps] == ["in_place"]
    assert sent(store) == [(UPDATE, DS_ID)]
    assert state.plan().changed == ()
