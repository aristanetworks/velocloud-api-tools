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
    HUB_B,
    Hub,
    backhaul_entry,
    backhaul_row,
    backhaul_rule,
    ds_with_vpn,
    hub_row,
)
from hub_state import (
    DS,
    FROM_BOTH_LISTS,
    GONE,
    OUT_OF_THE_RULE,
    QOS,
    SERVICE,
    SERVICE_ID,
    edit,
    guest_backhaul_state,
    hub_backhaul_rule,
    hub_pipeline,
    hub_profile_state,
    profile_hub_rows,
    removal_and_its_rule,
    rule_uses_hub,
)
from pyvelo.client.exceptions import VcoError, WriteNotAppliedError
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.config import (
    ConfigPipeline,
    ConfigState,
    DanglingReferenceError,
    MendingPatch,
    SaveStepError,
    save_progress,
)
from pyvelo.core import add, backhaul_hub_mending, delete
from pyvelo.testing import ModuleStore
from refs_helpers import CONFIG, DS_ID, QOS_ID, segment_row
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry
from shown_paths import round_trips

UPDATE = "configuration/updateConfigurationModule"
DISABLE_HUB = "edge/disableEdgeForEdgeHub"
ENABLE_BACKHAUL = "edge/enableEdgeForBackhaul"
SERVICE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SERVICE_B_ID = 602
FROM_BACKHAUL: dict[str, Any] = {
    "segments": {"Guest": {"vpn": {"backHaulEdges": GONE}}}
}


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


LINE = (
    "  business_policy segments[Guest].rules[Web].action.edge_to_edge_route_action"
    ".route_cfg.backhaul_edges[Hub-A].edge_logical_id: "
    "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1 is not a backhaul_hub of segment Guest"
)
TAKE_OUT = {
    "segments": {
        "Guest": {
            "rules": {
                "Web": {
                    "action": {
                        "edge_to_edge_route_action": {
                            "route_cfg": {
                                "backhaul_edges": {"Hub-A": {"_op": "delete"}}
                            }
                        }
                    }
                }
            }
        }
    }
}
PUT_BACK = {
    "segments": {
        "Guest": {
            "vpn": {
                "enabled": True,
                "hub_edges": {
                    "enabled": True,
                    "vpn_hubs": {"+": {"_op": "add", "name": "Hub-A"}},
                },
                "backhaul_edges": {"+": {"_op": "add", "name": "Hub-A"}},
            }
        }
    }
}


async def test_apply_accepts_the_removal_and_plan_refuses_with_both_patches() -> None:
    state = await rule_uses_hub()
    await edit(state, DS, FROM_BOTH_LISTS)  # accepted: plan() is the judge

    error = refused(state)

    remove, add = error.mending
    assert (remove.kind, remove.target_type, remove.module) == (
        "remove_reference",
        "profile",
        "business_policy",
    )
    assert remove.patch == TAKE_OUT
    assert add == backhaul_hub_mending("Guest", "Hub-A")
    for mending in error.mending:
        as_json = dataclasses.asdict(mending)
        assert json.loads(json.dumps(as_json)) == as_json
        assert f"send {json.dumps(mending.patch)} to {mending.module}" in str(error)
    assert "`revert`" in str(error)


async def test_the_refusal_and_its_patches_are_in_names() -> None:
    state = await rule_uses_hub()
    await edit(state, "device_settings", FROM_BOTH_LISTS)
    error = refused(state)
    assert str(error).splitlines()[1] == LINE
    assert "`vpn.backhaul_edges`" in str(error)
    assert "backHaulEdges" not in str(error)
    (reference,) = error.references
    assert (reference.module, reference.segment_name) == ("business_policy", "Guest")
    round_trips(reference.path)
    remove, add = error.mending
    assert (remove.module, remove.patch) == ("business_policy", TAKE_OUT)
    assert (add.module, add.patch) == ("device_settings", PUT_BACK)
    assert f"send {json.dumps(TAKE_OUT)} to business_policy" in str(error)


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


async def test_the_plan_lists_the_device_settings_last() -> None:
    _, _, state = await removal_and_its_rule()

    plan = state.plan()

    assert [m.name for m in plan.modules] == [QOS, "firewall", DS]
    lines = plan.render().splitlines()
    assert lines.index("steps:") > lines.index("firewall: none")
    assert lines[lines.index("steps:") + 1 :][:2] == [
        "  disable hub for Hub-A on segment Guest",
        "device_settings: update",
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
    # revert would plan nothing while the VCO has the role change: save again.
    assert "Do not take the device settings' edits back with `revert`" in str(error)
    assert [m.name for m in state.plan().changed] == [DS]
    store.requests.clear()
    result = await pipeline.save(state)
    assert [o.status for o in result.steps] == ["in_place"]
    assert sent(store) == [(UPDATE, DS_ID)]
    assert state.plan().changed == ()


async def test_a_step_that_fails_after_the_rule_write_is_a_save_step_error() -> None:
    pipeline, store, state = await removal_and_its_rule()
    store.fail_calls.add(DISABLE_HUB)

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    error = failed.value
    assert error.step is not None
    assert error.step.render() == "disable hub for Hub-A on segment Guest"
    assert (error.module_name, error.written) == (None, (QOS,))
    assert sent(store) == [(UPDATE, QOS_ID), (DISABLE_HUB, HUB_A.id)]
    assert [m.name for m in state.plan().changed] == [DS]
    result = await pipeline.save(state)
    assert [o.status for o in result.steps] == ["sent"]
    assert [w.name for w in result.written] == [DS]
    assert state.plan().changed == ()


async def hub_swap() -> tuple[ConfigPipeline, ModuleStore, ConfigState]:
    """Guest's rule ``Web`` moves from Hub-A to Hub-B, and Hub-A leaves both lists.

    Hub-B is a hub of Guest as read and becomes its backhaul hub in the same save.
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
        hub_row(
            HUB_B,
            service_id=SERVICE_B_ID,
            service_logical_id=SERVICE_B,
            segment=GUEST,
        ),
    )
    backhaul = backhaul_row(
        HUB_A,
        service_id=SERVICE_ID,
        service_logical_id=SERVICE,
        segment=GUEST,
        module_id=QOS_ID,
    )
    state = await hub_profile_state(
        [
            ds_entry(GLOBAL),
            ds_with_vpn(GUEST, hubs=(HUB_A, HUB_B), backhaul=(HUB_A,)),
        ],
        [
            qos_entry(GLOBAL),
            qos_entry(
                GUEST, rules=[backhaul_rule("Web", backhaul_entry(HUB_A, SERVICE))]
            ),
        ],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
        qos_refs={BACKHAUL_REF: backhaul},
    )
    lists = {
        "backHaulEdges": {HUB_A.name: delete(), "+": add({"name": HUB_B.name})},
        "edgeToEdgeHub": {"vpnHubs": GONE},
    }
    await edit(state, DS, {"segments": {"Guest": {"vpn": lists}}})
    entries = {HUB_A.name: delete(), "+": add({"name": HUB_B.name})}
    route = {"edge2EdgeRouteAction": {"routeCfg": {"backhaulEdges": entries}}}
    rule = {"Web": {"action": route}}
    await edit(state, QOS, {"segments": {"Guest": {"rules": rule}}})
    pipeline, store = hub_pipeline(state, vpn_check=True)
    return pipeline, store, state


async def test_a_hub_swap_that_stops_after_the_rule_write_leaves_it_ahead() -> None:
    pipeline, store, state = await hub_swap()
    assert [s.render() for s in state.plan().steps] == [
        "disable hub for Hub-A on segment Guest",
        "enable backhaul for Hub-B on segment Guest",
    ]
    store.fail_calls.add(DISABLE_HUB)

    with pytest.raises(SaveStepError):
        _ = await pipeline.save(state)

    stored = await pipeline.load_profile(CONFIG)
    rule = stored.profile.qos.segments[1].rules[0]
    route = rule.action.edge_to_edge_route_action.route_cfg
    assert isinstance(route.backhaul_edges, list)
    assert [e.edge_name for e in route.backhaul_edges] == [HUB_B.name]
    guest = stored.profile.device_settings.segments[1].vpn
    assert [h.name for h in guest.backhaul_edges] == [HUB_A.name]
    result = await pipeline.save(state)
    assert [o.status for o in result.steps] == ["sent", "sent"]
    assert state.plan().changed == ()
    again = await pipeline.load_profile(CONFIG)
    guest = again.profile.device_settings.segments[1].vpn
    assert [h.name for h in guest.backhaul_edges] == [HUB_B.name]
    assert again.plan().changed == ()


def segments_stored(store: ModuleStore) -> dict[str, list[str]]:
    """The segment names of each module the store holds, by module name."""
    return {
        row["name"]: [entry["segment"]["name"] for entry in row["data"]["segments"]]
        for row in store.rows.values()
    }


@pytest.mark.parametrize("how", ["request", "lost"])
async def test_a_segment_assigned_in_a_removal_save_is_stored_in_qos_first(
    how: str,
) -> None:
    pipeline, store, state = await removal_and_its_rule()
    _ = await pipeline.assign_segment(state, "Voice")
    if how == "request":
        store.fail_writes.add(3)  # the device settings write, the last
    else:
        read = pipeline.client.get_enterprise_services
        reads = 0

        async def another_writer_after_the_step(request: Any) -> Any:
            nonlocal reads
            reads += 1
            if reads == 3:  # the services read after the step
                store.other_writer(DS_ID, lambda row: row["data"].update(future=2))
            return await read(request)

        pipeline.client.get_enterprise_services = another_writer_after_the_step

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    assert (failed.value.module_name, failed.value.written) == (DS, (QOS, "firewall"))
    stored = segments_stored(store)
    assert "Voice" not in stored[DS]
    assert "Voice" in stored[QOS]
    assert "Voice" in stored["firewall"]
    plan = state.plan()  # no SegmentSetError either way
    if how == "lost":
        cause = failed.value.__cause__
        assert isinstance(cause, WriteNotAppliedError)
        assert plan.changed == ()
        assert [w.code for w in plan.warnings] == ["segments_disagree"]
        _ = await pipeline.apply(state, module=DS, patch=cause.redo_patch())
    assert [m.name for m in state.plan().changed] == [DS]
    result = await pipeline.save(state)
    assert [w.name for w in result.written] == [DS]
    assert "Voice" in segments_stored(store)[DS]
    assert state.plan().changed == ()


# -- a removal that would leave a hub backhaul with no hub ---------------------------

ONLY_HUB = "the only hub of a rule that backhauls through hubs, or one of all the hubs"


def out_of_guest_backhaul(*hubs: Hub) -> dict[str, Any]:
    """The device settings patch that takes *hubs* out of Guest's backhaul hubs."""
    gone = {hub.name: delete() for hub in hubs}
    return {"segments": {"Guest": {"vpn": {"backHaulEdges": gone}}}}


async def rules_backhaul_to(*rules: tuple[str, tuple[Hub, ...]]) -> ConfigState:
    """Guest's rules, each by name with the hubs its Edge to cloud route backhauls to.

    Guest has Hub-A and Hub-B as hubs and backhaul hubs.
    """
    return await guest_backhaul_state(
        *(hub_backhaul_rule(name, *hubs) for name, hubs in rules)
    )


def kinds(error: DanglingReferenceError) -> list[str]:
    return [m.kind for m in error.mending]


async def test_no_patch_takes_the_only_hub_out_of_a_rule() -> None:
    state = await rules_backhaul_to(("Web", (HUB_A,)))
    await edit(state, DS, out_of_guest_backhaul(HUB_A))

    error = refused(state)

    assert kinds(error) == ["add_member"]
    assert error.only_hubs == error.references
    text = str(error)
    assert ONLY_HUB in text
    assert (
        "give the rule another hub or change its `route_policy`, and the next" in text
    )
    (member,) = error.mending
    await send(state, member)
    _ = state.plan()  # the hub is a backhaul hub again


async def test_no_patch_takes_all_the_hubs_out_of_a_rule() -> None:
    state = await rules_backhaul_to(("Web", (HUB_A, HUB_B)))
    await edit(state, DS, out_of_guest_backhaul(HUB_A, HUB_B))

    error = refused(state)

    assert kinds(error) == ["add_member", "add_member"]
    assert len(error.only_hubs) == 2
    assert error.only_hubs == error.references
    assert ONLY_HUB in str(error)


async def test_a_patch_takes_one_hub_of_two_out_of_a_rule() -> None:
    state = await rules_backhaul_to(("Web", (HUB_A, HUB_B)))
    await edit(state, DS, out_of_guest_backhaul(HUB_A))

    error = refused(state)

    assert kinds(error) == ["remove_reference", "add_member"]
    assert error.only_hubs == ()
    assert ONLY_HUB not in str(error)
    remove, _ = error.mending
    await send(state, remove)
    _ = state.plan()  # Web backhauls to Hub-B alone


async def test_a_rule_that_would_be_left_with_no_hub_withholds_every_removal() -> None:
    state = await rules_backhaul_to(("Web", (HUB_A,)), ("Mail", (HUB_A, HUB_B)))
    await edit(state, DS, out_of_guest_backhaul(HUB_A))

    error = refused(state)

    assert kinds(error) == ["add_member"]  # the removal for Mail is left out too
    (only,) = error.only_hubs
    assert "rules[Web]" in str(only.path)
    assert len(error.references) == 2


async def test_a_rule_that_does_not_backhaul_keeps_its_removal_patch() -> None:
    rule = hub_backhaul_rule("Web", HUB_A)
    rule["action"]["edge2CloudRouteAction"]["routePolicy"] = "gateway"
    state = await guest_backhaul_state(rule)
    await edit(state, DS, out_of_guest_backhaul(HUB_A))

    error = refused(state)

    assert kinds(error) == ["remove_reference", "add_member"]
    assert error.only_hubs == ()
    assert ONLY_HUB not in str(error)
