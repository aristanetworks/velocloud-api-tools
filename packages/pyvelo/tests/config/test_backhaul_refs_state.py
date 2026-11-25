"""A state plans the row of a QoS rule's backhaul hub, on a profile and on an edge.

It plans it from the profile's service for that segment.

The profile has the Global Segment with ``HUB_A`` as its hub and backhaul hub,
whose service (object 601) is in the device settings refs. A rule is edited as
a caller edits it, through the state's data; ``state.plan()`` plans the three
modules and then checks the references.
"""

from typing import Any, cast

import pytest
from hub_helpers import (
    HUB_A,
    HUB_B,
    Hub,
    backhaul_entry,
    backhaul_rule,
    ds_with_vpn,
    hub_row,
)
from hub_state import (
    EDGE,
    EDGE_CONFIG,
    EDGE_QOS_ID,
    hub_edge_state,
    hub_pipeline,
    hub_profile_state,
    profile_hub_rows,
)
from pyvelo.client.enums.configuration import ConfigurationModuleName
from pyvelo.client.exceptions import DanglingReferenceError
from pyvelo.client.hub_services import BACKHAUL_REF
from pyvelo.client.models.configuration.qos.rule import BackhaulEdge
from pyvelo.config import HubServiceNotFoundError as ExportedError
from pyvelo.config.state import ConfigState
from pyvelo.core.identity import HubServiceNotFoundError
from refs_helpers import CONFIG, QOS_ID, SEG0, SEG0_OBJECT, segment_row
from segment_helpers import GLOBAL, ds_entry, firewall_entry, qos_entry

SERVICE_A = "a6a6a6a6-a6a6-4a6a-8a6a-a6a6a6a6a6a6"
SERVICE_B = "b6b6b6b6-b6b6-4b6b-8b6b-b6b6b6b6b6b6"
SERVICE_A_ID = 601
SERVICE_B_ID = 602
GET_SERVICES = "enterprise/getEnterpriseServices"


def profile_refs(*hubs: tuple[Hub, int, str]) -> dict[str, Any]:
    """The profile's device settings refs.

    The Global Segment's row and a hub row (hub and backhaul roles) for each of *hubs*.
    """
    return profile_hub_rows(
        segment_row(GLOBAL[0], SEG0_OBJECT, SEG0),
        *(
            hub_row(
                hub,
                service_id=service_id,
                service_logical_id=service_logical_id,
                segment=GLOBAL,
                roles=("hub", "backhaul"),
            )
            for hub, service_id, service_logical_id in hubs
        ),
    )


def profile_ds(
    *, backhaul: tuple[Hub, ...] = (HUB_A,), hubs: tuple[Hub, ...] = (HUB_A,)
) -> list[dict[str, Any]]:
    return [ds_with_vpn(GLOBAL, hubs=hubs, backhaul=backhaul)]


async def a_profile(
    *,
    rows: tuple[tuple[Hub, int, str], ...] = ((HUB_A, SERVICE_A_ID, SERVICE_A),),
    backhaul: tuple[Hub, ...] = (HUB_A,),
    hubs: tuple[Hub, ...] = (HUB_A,),
) -> ConfigState:
    """A profile with rule ``r1`` that uses no hub."""
    return await hub_profile_state(
        profile_ds(backhaul=backhaul, hubs=hubs),
        [qos_entry(GLOBAL, rules=[backhaul_rule("r1")])],
        [firewall_entry(GLOBAL)],
        ds_refs=profile_refs(*rows),
    )


async def an_edge(
    *,
    rows: tuple[tuple[Hub, int, str], ...] = ((HUB_A, SERVICE_A_ID, SERVICE_A),),
    backhaul: tuple[Hub, ...] = (HUB_A,),
    hubs: tuple[Hub, ...] = (HUB_A,),
) -> ConfigState:
    """An edge with its own QoS rule ``r1`` that uses no hub.

    Its profile is a profile of the hubs *hubs*.
    """
    return await hub_edge_state(
        [ds_entry(GLOBAL)],
        [qos_entry(GLOBAL, rules=[backhaul_rule("r1")])],
        profile_ds=profile_ds(backhaul=backhaul, hubs=hubs),
        profile_qos=[qos_entry(GLOBAL)],
        profile_refs=profile_refs(*rows),
    )


def use_hub(qos: Any, hub: Hub, service_logical_id: str) -> None:
    """Have rule ``r1`` of the Global Segment of *qos* backhaul to *hub*.

    It backhauls to the hub through its service.
    """
    (segment,) = qos.segments
    (held,) = segment.rules
    route = held.action.edge_to_edge_route_action.route_cfg
    route.backhaul_edges = [
        BackhaulEdge.model_validate(backhaul_entry(hub, service_logical_id))
    ]


def new_row(
    service_id: int, *, configuration_id: int, module_id: int
) -> dict[str, Any]:
    return {
        "configurationId": configuration_id,
        "moduleId": module_id,
        "enterpriseObjectId": service_id,
        "ref": BACKHAUL_REF,
        "segmentObjectId": GLOBAL[3],
    }


def planned_refs(state: ConfigState, name: str = "QOS") -> dict[str, Any] | None:
    (module,) = [m for m in state.plan().modules if m.name == name]
    return module.refs


def test_the_error_is_exported() -> None:
    assert ExportedError is HubServiceNotFoundError


async def test_a_profile_state_plans_the_row_of_a_rules_hub() -> None:
    state = await a_profile()
    use_hub(state.profile.qos, HUB_A, SERVICE_A)

    plan = state.plan()

    (qos,) = [m for m in plan.modules if m.name == "QOS"]
    assert qos.refs == {
        BACKHAUL_REF: [new_row(SERVICE_A_ID, configuration_id=CONFIG, module_id=QOS_ID)]
    }
    assert qos.ref_lines() == [f"  + refs {BACKHAUL_REF} (Global Segment): {SERVICE_A}"]
    assert [m.name for m in plan.changed] == ["QOS"]


async def test_an_edge_state_plans_the_row_of_a_rules_hub() -> None:
    state = await an_edge()
    use_hub(state.edge.qos, HUB_A, SERVICE_A)

    plan = state.plan()

    (qos,) = [m for m in plan.modules if m.name == "QOS"]
    assert qos.refs == {
        BACKHAUL_REF: [
            new_row(SERVICE_A_ID, configuration_id=EDGE_CONFIG, module_id=EDGE_QOS_ID)
        ]
    }
    assert [m.name for m in plan.changed] == ["QOS"]


async def test_a_rule_on_a_hub_with_no_service_raises_service_not_found() -> None:
    state = await a_profile(backhaul=(HUB_A, HUB_B), hubs=(HUB_A, HUB_B))
    use_hub(state.profile.qos, HUB_B, SERVICE_B)

    with pytest.raises(HubServiceNotFoundError) as error:
        _ = state.plan()

    assert (error.value.hub, error.value.segment) == ("Hub-B", "Global Segment")
    assert "save the hub first and the rule after" in str(error.value)


async def test_an_edge_rule_on_a_hub_the_profile_has_no_service_for_raises() -> None:
    state = await an_edge(backhaul=(HUB_A, HUB_B), hubs=(HUB_A, HUB_B))
    use_hub(state.edge.qos, HUB_B, SERVICE_B)

    with pytest.raises(HubServiceNotFoundError) as error:
        _ = state.plan()

    assert error.value.hub == "Hub-B"


async def test_a_rule_on_a_hub_with_a_service_but_not_listed_for_backhaul_dangles() -> (
    None
):
    state = await a_profile(
        rows=((HUB_A, SERVICE_A_ID, SERVICE_A), (HUB_B, SERVICE_B_ID, SERVICE_B)),
        backhaul=(HUB_A,),
        hubs=(HUB_A, HUB_B),
    )
    use_hub(state.profile.qos, HUB_B, SERVICE_B)

    with pytest.raises(DanglingReferenceError) as error:
        _ = state.plan()

    (reference,) = error.value.references
    assert (reference.module, reference.set_name) == ("QOS", "backhaul_hub")


async def test_the_save_of_a_profile_rule_writes_the_row_the_store_keeps() -> None:
    state = await a_profile()
    pipeline, store = hub_pipeline(state)
    use_hub(state.profile.qos, HUB_A, SERVICE_A)

    result = await pipeline.save(state)

    assert result.modules_updated == [ConfigurationModuleName.QOS]
    stored = cast("dict[str, Any]", store.rows[QOS_ID]["refs"][BACKHAUL_REF])
    assert (stored["enterpriseObjectId"], stored["logicalId"]) == (
        SERVICE_A_ID,
        SERVICE_A,
    )
    assert stored["segmentObjectId"] == GLOBAL[3]


async def test_the_save_of_an_edge_rule_writes_the_row_and_the_service_knows_its_edge() -> (  # noqa: E501 (a test's name)
    None
):
    state = await an_edge()
    pipeline, store = hub_pipeline(state)
    use_hub(state.edge.qos, HUB_A, SERVICE_A)

    _ = await pipeline.save(state)

    stored = cast("dict[str, Any]", store.rows[EDGE_QOS_ID]["refs"][BACKHAUL_REF])
    assert (stored["enterpriseObjectId"], stored["configurationId"]) == (
        SERVICE_A_ID,
        EDGE_CONFIG,
    )
    answered = cast(
        "list[dict[str, Any]]",
        store.handle(
            GET_SERVICES,
            {
                "enterpriseId": 7,
                "type": "edgeHub",
                "with": ["configuration", "edgeQOSUsage"],
            },
        ),
    )
    (service,) = [s for s in answered if s["id"] == SERVICE_A_ID]
    assert [(u["edgeId"], u["profileId"]) for u in service["edgeQOSUsage"]] == [
        (EDGE.id, EDGE_CONFIG)
    ]
