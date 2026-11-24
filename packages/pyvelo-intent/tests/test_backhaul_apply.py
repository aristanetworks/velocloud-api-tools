"""``apply`` gives a QoS rule's new backhaul hub its service for the segment, and refuses a hub that has none yet.

The states are those of ``test_backhaul_refs_state``: a profile whose Global
Segment lists ``HUB_A`` for backhaul with a service row, and an edge over it.
"""

import uuid
from typing import Any

import pytest
from hub_helpers import HUB_A, HUB_B, backhaul_entry, backhaul_rule
from hub_state import hub_pipeline, hub_profile_state  # pyright: ignore[reportImplicitRelativeImport]
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.client.models.configuration.qos.rule import BackhaulEdge
from pyvelo.core.identity import HubServiceNotFoundError
from pyvelo.core.patch import add
from segment_helpers import GLOBAL, GUEST, firewall_entry, qos_entry
from test_backhaul_refs_state import (  # pyright: ignore[reportImplicitRelativeImport]
    SERVICE_A,
    SERVICE_A_ID,
    a_profile,
    an_edge,
    profile_ds,
    profile_refs,
)


def hub_in_rule(name: str, *, rule: str = "r1") -> dict[str, Any]:
    """The patch to the rules of a segment that adds the hub *name* to the backhaul hubs of *rule*."""
    return {
        rule: {
            "action": {
                "edge2EdgeRouteAction": {
                    "routeCfg": {"backhaulEdges": {"+": add({"name": name})}}
                }
            }
        }
    }


def backhaul_of(qos: Any, rule: str = "r1") -> list[BackhaulEdge]:
    """The backhaul entries of rule *rule* in the Global Segment of *qos*."""
    (segment,) = qos.segments
    (held,) = [r for r in segment.rules if r.name == rule]
    return list(held.action.edge_to_edge_route_action.route_cfg.backhaul_edges)


def add_hub_b_to_the_lists() -> dict[str, Any]:
    return {
        "vpn": {
            "edgeToEdgeHub": {"vpnHubs": {"+": add({"name": HUB_B.name})}},
            "backHaulEdges": {"+": add({"name": HUB_B.name})},
        }
    }


async def test_a_hub_named_in_a_profile_rule_gets_its_service():
    state = await a_profile()
    pipeline, _ = hub_pipeline(state)

    _ = await pipeline.apply(
        state,
        module="qos",
        segment=GLOBAL[1],
        path="rules",
        patch=hub_in_rule(HUB_A.name),
    )

    (entry,) = backhaul_of(state.profile.qos)
    assert str(entry.logical_id) == SERVICE_A
    assert str(entry.edge_logical_id) == HUB_A.logical_id
    assert (entry.edge_id, entry.edge_name, entry.type_) == (
        HUB_A.id,
        HUB_A.name,
        "edgeHub",
    )
    (qos,) = [m for m in state.plan().modules if m.name == "QOS"]
    assert qos.refs is not None
    (row,) = qos.refs[BACKHAUL_REF]
    assert row["enterpriseObjectId"] == SERVICE_A_ID
    assert row["segmentObjectId"] == GLOBAL[3]


async def test_a_hub_named_in_an_edge_rule_gets_the_profiles_service():
    state = await an_edge()
    pipeline, _ = hub_pipeline(state)

    _ = await pipeline.apply(
        state,
        module="qos",
        segment=GLOBAL[1],
        path="rules",
        patch=hub_in_rule(HUB_A.name),
    )

    (entry,) = backhaul_of(state.edge.qos)
    assert str(entry.logical_id) == SERVICE_A
    (qos,) = [m for m in state.plan().modules if m.name == "QOS"]
    assert qos.refs is not None
    assert qos.refs[BACKHAUL_REF][0]["enterpriseObjectId"] == SERVICE_A_ID


async def test_a_hub_and_its_rule_in_one_save_is_refused():
    state = await a_profile()
    pipeline, _ = hub_pipeline(state)
    _ = await pipeline.apply(
        state,
        module="device_settings",
        segment=GLOBAL[1],
        patch=add_hub_b_to_the_lists(),
    )
    before = state.profile.qos.model_dump()

    with pytest.raises(HubServiceNotFoundError) as error:
        _ = await pipeline.apply(
            state,
            module="qos",
            segment=GLOBAL[1],
            path="rules",
            patch=hub_in_rule(HUB_B.name),
        )

    assert (error.value.hub, error.value.segment) == (HUB_B.name, GLOBAL[1])
    assert "save the hub first" in str(error.value)
    assert '"vpnHubs": {"+": {"_op": "add", "name": "Hub-B"}}' in str(error.value)
    assert backhaul_of(state.profile.qos) == []
    assert state.profile.qos.model_dump() == before
    (segment,) = state.profile.device_settings.segments
    assert [h.name for h in segment.vpn.hub_edges.vpn_hubs] == [
        HUB_A.name,
        HUB_B.name,
    ]


async def test_an_edge_rule_on_a_hub_the_profile_has_no_service_for_is_refused():
    state = await an_edge(backhaul=(HUB_A, HUB_B), hubs=(HUB_A, HUB_B))
    pipeline, _ = hub_pipeline(state)

    with pytest.raises(HubServiceNotFoundError) as error:
        _ = await pipeline.apply(
            state,
            module="qos",
            segment=GLOBAL[1],
            path="rules",
            patch=hub_in_rule(HUB_B.name),
        )

    assert error.value.hub == HUB_B.name
    assert backhaul_of(state.edge.qos) == []


async def test_an_edge_rule_on_a_new_qos_module_inserts_its_backhaul_row():
    state = await an_edge()
    edge = state.edge_config
    assert edge is not None
    modules = edge.modules
    assert isinstance(modules, list)
    modules[:] = [m for m in modules if m.name != "QOS"]
    state.take_snapshot()
    pipeline, _ = hub_pipeline(state)
    rule = backhaul_rule("r2")
    rule["action"]["edge2EdgeRouteAction"]["routeCfg"]["backhaulEdges"] = [
        {"name": HUB_A.name}
    ]

    _ = await pipeline.apply(
        state,
        module="qos",
        segment=GLOBAL[1],
        path="rules",
        patch={"+": add(rule)},
    )

    (qos,) = [m for m in state.plan().modules if m.name == "QOS"]
    assert qos.operation == "insert"
    assert qos.refs is not None
    (row,) = qos.refs[BACKHAUL_REF]
    assert (row["enterpriseObjectId"], row["segmentObjectId"]) == (
        SERVICE_A_ID,
        GLOBAL[3],
    )
    assert "moduleId" not in row


async def test_an_entry_built_by_assignment_without_its_service_is_refused_at_plan():
    state = await a_profile()
    (segment,) = state.profile.qos.segments
    route = segment.rules[0].action.edge_to_edge_route_action.route_cfg
    route.backhaul_edges = [
        BackhaulEdge(
            edge_logical_id=uuid.UUID(HUB_A.logical_id),
            edge_name=HUB_A.name,
            type_="edgeHub",
        )
    ]

    with pytest.raises(HubServiceNotFoundError) as error:
        _ = state.plan()

    assert error.value.hub == HUB_A.name
    assert "backhaul_edges" in error.value.path


async def test_a_segment_added_with_a_rule_on_a_hub_it_has_no_service_for_is_refused():
    state = await a_profile()
    pipeline, _ = hub_pipeline(state)
    rule = backhaul_rule("r2")
    rule["action"]["edge2EdgeRouteAction"]["routeCfg"]["backhaulEdges"] = [
        {"name": HUB_A.name}
    ]
    guest = {"segment": {"name": GUEST[1]}, "rules": [rule]}
    before = state.profile.qos.model_dump()

    with pytest.raises(HubServiceNotFoundError) as error:
        _ = await pipeline.apply(
            state, module="qos", patch={"segments": {"+": add(guest)}}
        )

    assert (error.value.hub, error.value.segment) == (HUB_A.name, GUEST[1])
    assert state.profile.qos.model_dump() == before


async def test_an_entry_that_lacked_its_service_as_read_does_not_stop_another_edit():
    unserved = {
        key: value
        for key, value in backhaul_entry(HUB_B, "unused").items()
        if key != "logicalId"
    }
    state = await hub_profile_state(
        profile_ds(),
        [qos_entry(GLOBAL, rules=[backhaul_rule("r1"), backhaul_rule("r2", unserved)])],
        [firewall_entry(GLOBAL)],
        ds_refs=profile_refs((HUB_A, SERVICE_A_ID, SERVICE_A)),
    )
    pipeline, _ = hub_pipeline(state)

    _ = await pipeline.apply(
        state,
        module="qos",
        segment=GLOBAL[1],
        path="rules",
        patch=hub_in_rule(HUB_A.name),
    )

    (entry,) = backhaul_of(state.profile.qos)
    assert str(entry.logical_id) == SERVICE_A
