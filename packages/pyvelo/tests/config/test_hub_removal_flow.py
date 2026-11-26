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
from hub_state import hub_profile_state, profile_hub_rows
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.config import (
    ConfigPipeline,
    ConfigState,
    DanglingReferenceError,
    MendingPatch,
)
from pyvelo.core import backhaul_hub_mending, delete
from refs_helpers import QOS_ID, segment_row
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry

DS = "deviceSettings"
QOS = "QOS"
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

    assert [m.name for m in state.plan().changed] == [DS, QOS]


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
