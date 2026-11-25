"""A hub that edge QoS rules use: removing it from the Branch-to-Hub list warns and names the edges, or refuses.

A role call that disables an Edge hub deletes its service, and with it the
backhaul refs of the rules of the edges that used the hub (those rules keep the
hub in their data). The save finds the edges from the services read
(``edgeQOSUsage``, asked for only when a hub leaves a Branch-to-Hub list):
``on_hub_in_use="warn"`` says so in ``SaveResult.warnings`` and goes on,
``"refuse"`` raises ``HubListError`` before any step.
"""

import uuid
from typing import Any, Literal

import pytest
from hub_helpers import HUB_A, HUB_B, Hub, ds_with_vpn, hub_row
from hub_state import (
    hub_pipeline,
    hub_profile_state,
    profile_hub_rows,
)
from pyvelo.client.exceptions import HubListError
from pyvelo.client.schemas.enterprise import EdgeHubService
from pyvelo.config.hub_calls import HubServices, hubs_in_use
from pyvelo.config.hub_steps import HubRef, HubStep
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.state import ConfigState
from pyvelo.config.validation_warning import ValidationWarning
from pyvelo.core import delete
from pyvelo.testing import ModuleStore, make_module_json
from refs_helpers import CONFIG, DS_ID, segment_row
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry

DS = "deviceSettings"
SERVICES = "enterprise/getEnterpriseServices"
UPDATE = "configuration/updateConfigurationModule"
BACKHAUL_REF = "deviceSettings:backHaulEdge"
EDGE_CONFIG = 50
EDGE_QOS_ID = 52
BRANCH_1 = (34, "Branch-1")
"""The Edge ``NOT_A_HUB`` of the store: the one that uses a hub in these tests."""
BRANCH_2 = (70, "Branch-2")
type Role = Literal["hub", "bridge", "backhaul"]


def service_id(hub: Hub) -> int:
    return 600 + hub.id


def service_row(hub: Hub) -> dict[str, Any]:
    """The row of *hub*'s Edge service for the Global Segment of the profile, with all three roles."""
    return hub_row(
        hub,
        service_id=service_id(hub),
        service_logical_id=str(uuid.UUID(int=service_id(hub))),
        segment=GLOBAL,
        roles=("hub", "bridge", "backhaul"),
    )


async def profile_with(*hubs: Hub) -> ConfigState:
    """A profile whose Global Segment has *hubs* in all three lists, each with its service."""
    ds = ds_with_vpn(GLOBAL, hubs=hubs, bridges=hubs, backhaul=hubs)
    refs = profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        *(service_row(hub) for hub in hubs),
    )
    return await hub_profile_state(
        [ds, ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
    )


async def remove(state: ConfigState, *hubs: Hub, only_backhaul: bool = False) -> None:
    """Take *hubs* out of the three lists (out of the backhaul list only, with *only_backhaul*), as the UI's removal does."""
    gone = {hub.name: delete() for hub in hubs}
    vpn: dict[str, Any] = {"backHaulEdges": gone}
    if not only_backhaul:
        vpn["edgeToEdgeDetail"] = {"vpnHubs": gone}
        vpn["edgeToEdgeHub"] = {"vpnHubs": gone}
    _ = await ConfigPipeline(client=None).apply(
        state, module=DS, patch={"segments": {GLOBAL[1]: {"vpn": vpn}}}
    )


def used_by_edge(
    store: ModuleStore,
    edge: tuple[int, str],
    *hubs: Hub,
    configuration_id: int = EDGE_CONFIG,
) -> None:
    """Give Edge *edge* a configuration whose QoS module has a ``backHaulEdge`` row for the service of each of *hubs*."""
    edge_id, name = edge
    if edge_id not in store.edges:
        store.add_edge(edge_id, logical_id=str(uuid.UUID(int=edge_id)), name=name)
    rows = [
        {
            "id": 9000 + service_id(hub) + configuration_id,
            "enterpriseObjectId": service_id(hub),
            "configurationId": configuration_id,
            "moduleId": EDGE_QOS_ID + configuration_id,
            "segmentObjectId": GLOBAL[3],
            "ref": BACKHAUL_REF,
        }
        for hub in hubs
    ]
    store.add_configuration(
        {
            "id": configuration_id,
            "name": f"Edge Specific Profile of {name}",
            "modules": [
                make_module_json(
                    "QOS",
                    {"segments": []},
                    module_id=EDGE_QOS_ID + configuration_id,
                    configuration_id=configuration_id,
                    refs={BACKHAUL_REF: rows},
                )
            ],
        }
    )
    store.add_stack(configuration_id, CONFIG, edge_id=edge_id)


def role_calls(store: ModuleStore) -> list[str]:
    return [
        method
        for method, _ in store.requests
        if method.startswith(("edge/", "enterprise/")) and method != SERVICES
    ]


def writes(store: ModuleStore) -> list[str]:
    return [method for method, _ in store.requests if method == UPDATE]


def edge_hub_services(store: ModuleStore) -> list[str]:
    answered: Any = store.handle(SERVICES, {"type": "edgeHub"})
    return [service["name"] for service in answered]


def in_use_warnings(warnings: list[ValidationWarning]) -> list[ValidationWarning]:
    return [w for w in warnings if w.code == "hub_in_use"]


async def used_hub_save() -> tuple[ConfigPipeline, ModuleStore, ConfigState]:
    """A profile that removes Hub-A, which Branch-1's QoS rules use, from its lists: the pipeline, the store and the state."""
    state = await profile_with(HUB_A, HUB_B)
    await remove(state, HUB_A)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    used_by_edge(store, BRANCH_1, HUB_A)
    return pipeline, store, state


async def test_removing_a_hub_that_an_edge_uses_warns_and_names_the_edge_and_goes_on() -> (
    None
):
    pipeline, store, state = await used_hub_save()

    result = await pipeline.save(state)

    (warning,) = in_use_warnings(result.warnings)
    assert warning.path == 'deviceSettings: segments["Global Segment"].vpn'
    assert "Hub-A" in warning.message
    assert "Branch-1" in warning.message
    assert "Hub-B" not in warning.message
    assert "deletes" in warning.message
    assert "service" in warning.message
    assert [(o.step.action, o.step.role, o.status) for o in result.steps] == [
        ("disable", "hub", "sent")
    ]
    assert edge_hub_services(store) == ["Hub-B"]
    assert len(writes(store)) == 1


async def test_the_advice_to_keep_the_hub_names_the_lists_the_patch_restores() -> None:
    pipeline, _, state = await used_hub_save()

    result = await pipeline.save(state)

    (warning,) = in_use_warnings(result.warnings)
    # The patch restores the Branch-to-Hub list only, and the text says so.
    assert "put it back in the Branch-to-Hub list" in warning.message
    assert '"edgeToEdgeHub": {"vpnHubs": {"+": {"_op": "add", "name": "Hub-A"}}}' in (
        warning.message
    )
    assert "`vpn.edgeToEdgeDetail.vpnHubs`" in warning.message
    assert "`vpn.backHaulEdges`" in warning.message


async def test_with_refuse_a_hub_an_edge_uses_raises_before_any_step() -> None:
    pipeline, store, state = await used_hub_save()

    with pytest.raises(HubListError) as error:
        _ = await pipeline.save(state, on_hub_in_use="refuse")

    assert error.value.edges == (BRANCH_1,)
    assert error.value.hubs == ("Hub-A",)
    assert error.value.segment == "Global Segment"
    message = str(error.value)
    assert "Branch-1" in message
    assert 'on_hub_in_use="warn"' in message
    assert role_calls(store) == []
    assert writes(store) == []
    assert edge_hub_services(store) == ["Hub-A", "Hub-B"]
    # Nothing was lost: the same save with the default goes on.
    result = await pipeline.save(state)
    assert [o.status for o in result.steps] == ["sent"]


async def test_a_hub_no_edge_uses_gives_no_warning_and_is_not_refused() -> None:
    state = await profile_with(HUB_A, HUB_B)
    await remove(state, HUB_B)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    used_by_edge(store, BRANCH_1, HUB_A)

    result = await pipeline.save(state, on_hub_in_use="refuse")

    assert in_use_warnings(result.warnings) == []
    assert edge_hub_services(store) == ["Hub-A"]


async def test_each_hub_in_use_has_its_own_warning_with_its_own_edges() -> None:
    state = await profile_with(HUB_A, HUB_B)
    await remove(state, HUB_A, HUB_B)
    _ = await ConfigPipeline(client=None).apply(
        state,
        module=DS,
        patch={"segments": {GLOBAL[1]: {"vpn": {"edgeToEdgeHub": {"enabled": False}}}}},
    )
    pipeline, store = hub_pipeline(state, vpn_check=True)
    used_by_edge(store, BRANCH_1, HUB_A, HUB_B)
    used_by_edge(store, BRANCH_2, HUB_B, configuration_id=60)

    result = await pipeline.save(state)

    first, second = in_use_warnings(result.warnings)
    assert "Hub-A" in first.message
    assert "Branch-1" in first.message
    assert "Branch-2" not in first.message
    assert "Hub-B" in second.message
    assert "Branch-1" in second.message
    assert "Branch-2" in second.message


async def test_with_refuse_every_edge_of_every_hub_in_use_is_in_the_error() -> None:
    state = await profile_with(HUB_A, HUB_B)
    await remove(state, HUB_A, HUB_B)
    _ = await ConfigPipeline(client=None).apply(
        state,
        module=DS,
        patch={"segments": {GLOBAL[1]: {"vpn": {"edgeToEdgeHub": {"enabled": False}}}}},
    )
    pipeline, store = hub_pipeline(state, vpn_check=True)
    used_by_edge(store, BRANCH_1, HUB_A, HUB_B)
    used_by_edge(store, BRANCH_2, HUB_B, configuration_id=60)

    with pytest.raises(HubListError) as error:
        _ = await pipeline.save(state, on_hub_in_use="refuse")

    assert error.value.hubs == ("Hub-A", "Hub-B")
    assert error.value.edges == (BRANCH_1, BRANCH_2)
    assert role_calls(store) == []


async def test_a_dry_run_warns_the_same_way_and_sends_nothing() -> None:
    pipeline, store, state = await used_hub_save()

    result = await pipeline.save(state, dry_run=True)

    (warning,) = in_use_warnings(result.warnings)
    assert "Branch-1" in warning.message
    assert [o.status for o in result.steps] == ["would_send"]
    assert role_calls(store) == []
    assert writes(store) == []


async def test_a_dry_run_with_refuse_raises_the_same_error() -> None:
    pipeline, store, state = await used_hub_save()

    with pytest.raises(HubListError) as error:
        _ = await pipeline.save(state, dry_run=True, on_hub_in_use="refuse")

    assert error.value.edges == (BRANCH_1,)
    assert role_calls(store) == []
    assert writes(store) == []


async def test_a_hub_that_only_leaves_the_backhaul_list_is_not_in_use_in_that_sense() -> (
    None
):
    state = await profile_with(HUB_A, HUB_B)
    await remove(state, HUB_A, only_backhaul=True)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    used_by_edge(store, BRANCH_1, HUB_A)

    result = await pipeline.save(state, on_hub_in_use="refuse")

    assert in_use_warnings(result.warnings) == []
    assert [(o.step.role, o.status) for o in result.steps] == [("backhaul", "sent")]
    assert edge_hub_services(store) == ["Hub-A", "Hub-B"]


@pytest.mark.parametrize("leaves_the_hub_list", [True, False])
async def test_the_services_read_asks_for_edge_qos_usage_only_when_a_hub_leaves_the_hub_list(
    *,
    leaves_the_hub_list: bool,
) -> None:
    state = await profile_with(HUB_A, HUB_B)
    await remove(state, HUB_A, only_backhaul=not leaves_the_hub_list)
    pipeline, store = hub_pipeline(state, vpn_check=True)

    _ = await pipeline.save(state)

    reads = [p for m, p in store.requests if m == SERVICES and p["type"] == "edgeHub"]
    expected = ["configuration", *(["edgeQOSUsage"] * leaves_the_hub_list)]
    assert reads[0]["with"] == expected
    assert all("edgeQOSUsage" not in r["with"] for r in reads[1:])


# -- hubs_in_use ---------------------------------------------------------------

GLOBAL_NAME = GLOBAL[1]


def removal(hub: Hub, *, role: Role = "hub", action: str = "disable") -> HubStep:
    return HubStep(
        action,  # pyright: ignore[reportArgumentType]
        role,
        HubRef("edge", uuid.UUID(hub.logical_id), hub.name, None, None),
        GLOBAL_NAME,
        GLOBAL[0],
        GLOBAL[3],
    )


def usage(edge_id: int, name: str, ref: object = BACKHAUL_REF) -> dict[str, Any]:
    return {"edgeId": edge_id, "name": name, "ref": ref, "segmentObjectId": GLOBAL[3]}


def service_with(
    hub: Hub, *usages: dict[str, Any], configuration_id: int = CONFIG
) -> EdgeHubService:
    return EdgeHubService.model_validate(
        {
            "id": service_id(hub),
            "logicalId": str(uuid.UUID(int=service_id(hub))),
            "name": hub.name,
            "type": "edgeHub",
            "created": "2024-01-01T00:00:00.000Z",
            "edgeId": hub.id,
            "data": {
                "logicalId": hub.logical_id,
                "roles": {
                    "edgeHub": True,
                    "edgeToEdgeBridge": False,
                    "backHaulEdge": False,
                },
            },
            "configuration": [
                {
                    "id": configuration_id,
                    "moduleId": DS_ID,
                    "segmentObjectId": GLOBAL[3],
                }
            ],
            "edgeQOSUsage": list(usages),
        }
    )


def in_use(
    steps: list[HubStep], *services: EdgeHubService
) -> dict[HubStep, tuple[tuple[int, str], ...]]:
    return hubs_in_use(
        steps,
        HubServices(edge_hubs=services),
        configuration_id=CONFIG,
        module_id=DS_ID,
    )


def test_hubs_in_use_gives_the_edges_whose_rows_are_backhaul_refs_once_each() -> None:
    step = removal(HUB_A)
    service = service_with(
        HUB_A, usage(34, "Branch-1"), usage(70, "Branch-2"), usage(34, "Branch-1")
    )

    assert in_use([step], service) == {step: ((34, "Branch-1"), (70, "Branch-2"))}


def test_hubs_in_use_counts_only_rows_whose_ref_is_the_backhaul_ref_name() -> None:
    step = removal(HUB_A)
    service = service_with(
        HUB_A,
        usage(34, "Branch-1", ref="deviceSettings:vpn:edgeHub"),
        usage(35, "Branch-3", ref={"name": BACKHAUL_REF}),
        usage(36, "Branch-4", ref=None),
        usage(70, "Branch-2"),
    )

    assert in_use([step], service) == {step: ((70, "Branch-2"),)}


def test_hubs_in_use_leaves_out_a_hub_no_row_uses_and_a_service_that_was_not_asked_for() -> (
    None
):
    used, unused = removal(HUB_A), removal(HUB_B)
    services = (
        service_with(HUB_A, usage(34, "Branch-1")),
        service_with(HUB_B),
    )

    assert in_use([used, unused], *services) == {used: ((34, "Branch-1"),)}
    asked_not = EdgeHubService.model_validate(
        service_with(HUB_A).model_dump(by_alias=True, exclude={"edge_qos_usage"})
    )
    assert in_use([used], asked_not) == {}


def test_hubs_in_use_looks_only_at_the_disable_hub_steps_of_edge_hubs() -> None:
    service = service_with(HUB_A, usage(34, "Branch-1"))
    others = [
        removal(HUB_A, role="backhaul"),
        removal(HUB_A, role="bridge"),
        removal(HUB_A, action="enable"),
        HubStep(
            "disable",
            "hub",
            HubRef("cluster", uuid.UUID(HUB_A.logical_id), "Cluster-1", None, None),
            GLOBAL_NAME,
            GLOBAL[0],
            GLOBAL[3],
        ),
    ]

    assert in_use(others, service) == {}


def test_hubs_in_use_takes_the_service_of_the_profile_and_segment_only() -> None:
    step = removal(HUB_A)
    elsewhere = service_with(HUB_A, usage(34, "Branch-1"), configuration_id=CONFIG + 1)

    assert in_use([step], elsewhere) == {}


async def test_an_on_hub_in_use_that_is_neither_warn_nor_refuse_is_a_value_error() -> (
    None
):
    pipeline, store, state = await used_hub_save()

    with pytest.raises(ValueError, match=r"'warn'.*'refuse'"):
        _ = await pipeline.save(state, on_hub_in_use="refues")  # pyright: ignore[reportArgumentType]

    assert role_calls(store) == []
    assert writes(store) == []
