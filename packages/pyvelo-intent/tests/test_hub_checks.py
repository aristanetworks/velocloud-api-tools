"""A hub list edit that breaks the UI's rules is refused at ``plan()``, before any call; one broken as read is a warning.

``check_hub_lists`` runs in ``state.plan()`` for a profile, so a ``save``
refuses with no request sent: no version read, no services read.
"""

from typing import Any

import pytest
from hub_helpers import (
    CLUSTER,
    HUB_A,
    HUB_B,
    MEMBER,
    NOT_A_HUB,
    Hub,
    ds_with_vpn,
    hub_entry,
    hub_row,
)
from hub_state import (  # pyright: ignore[reportImplicitRelativeImport]
    ALL_HUBS,
    hub_pipeline,
    hub_profile_state,
    profile_hub_rows,
)
from profile_state import profile_state  # pyright: ignore[reportImplicitRelativeImport]
from pyvelo.client.exceptions import HubListError
from pyvelo.client.models.configuration.device_settings.segment import VpnHub
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.core import delete
from pyvelo.core.identity import ResolverNotLoadedError
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from refs_helpers import segment_row
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry

DS = "deviceSettings"
SERVICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
HUB_LIST = "vpn.edgeToEdgeHub.vpnHubs"
BRIDGE_LIST = "vpn.edgeToEdgeDetail.vpnHubs"
BACKHAUL_LIST = "vpn.backHaulEdges"


def service_row(hub: Hub, n: int) -> dict[str, Any]:
    return hub_row(
        hub,
        service_id=600 + n,
        service_logical_id=SERVICE,
        segment=GLOBAL,
        roles=("hub", "bridge", "backhaul"),
    )


async def profile_of(
    ds: dict[str, Any], *hubs: Hub, loaded: tuple[Hub, ...] | None = None
) -> ConfigState:
    """A profile whose Global Segment is *ds* (and Guest the default), with a service row for each of *hubs*.

    The hub resolver holds *loaded* (every hub unless given).
    """
    refs = profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        *(service_row(h, n) for n, h in enumerate(hubs, 1)),
    )
    return await hub_profile_state(
        [ds, ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs,
        hubs=ALL_HUBS if loaded is None else loaded,
    )


async def edit(state: ConfigState, vpn: dict[str, Any], segment: str = GLOBAL[1]):
    patch = {"segments": {segment: {"vpn": vpn}}}
    _ = await ConfigPipeline(client=None).apply(state, module=DS, patch=patch)


def hub_list_patch(change: dict[str, Any]) -> dict[str, Any]:
    return {"edgeToEdgeHub": change}


def add(name: str) -> dict[str, Any]:
    return {"+": {"_op": "add", "name": name}}


def remove(*names: str) -> dict[str, Any]:
    return {name: delete() for name in names}


async def refused(state: ConfigState) -> HubListError:
    """The ``HubListError`` that ``plan()`` raises, after a ``save`` that raises it and sends no request at all."""
    with pytest.raises(HubListError) as planned:
        _ = state.plan()
    pipeline, store = hub_pipeline(state)
    with pytest.raises(HubListError):
        _ = await pipeline.save(state)
    assert store.requests == []
    return planned.value


def assert_names(error: HubListError, *texts: str, lists: tuple[str, ...] = ()):
    assert error.segment == GLOBAL[1]
    assert GLOBAL[1] in str(error)
    for text in texts:
        assert text in str(error)
    assert set(lists) <= set(error.lists)


def as_read_warnings(state: ConfigState):
    return [w for w in state.plan().check_warnings if w.code == "hub_list_as_read"]


# --- rule 1: the Branch-to-Hub list and its flag -----------------------------------


async def test_a_hub_listed_with_the_hub_list_off_raises():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await edit(state, {"edgeToEdgeHub": {"vpnHubs": add(HUB_A.name)}})

    error = await refused(state)

    assert_names(error, "Hub-A", "edgeToEdgeHub.enabled", lists=(HUB_LIST,))
    assert error.hubs == ("Hub-A",)


async def test_the_hub_list_on_with_no_hub_raises():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await edit(state, {"edgeToEdgeHub": {"enabled": True}})

    error = await refused(state)

    assert_names(error, "edgeToEdgeHub.enabled", "empty", lists=(HUB_LIST,))


async def test_turning_the_hub_list_off_while_it_lists_hubs_says_to_remove_them_first():
    state = await profile_of(ds_with_vpn(GLOBAL, hubs=(HUB_A,)), HUB_A)
    await edit(state, {"edgeToEdgeHub": {"enabled": False}})

    error = await refused(state)

    assert_names(error, "Remove the hubs first", "Hub-A", lists=(HUB_LIST,))
    assert '"_op": "delete"' in str(error)


async def test_the_hub_list_emptied_while_it_is_on_raises():
    state = await profile_of(ds_with_vpn(GLOBAL, hubs=(HUB_A,)), HUB_A)
    await edit(state, {"edgeToEdgeHub": {"vpnHubs": remove(HUB_A.name)}})

    assert_names(await refused(state), "empty", lists=(HUB_LIST,))


# --- rule 2: no logical ID twice -------------------------------------------------


@pytest.mark.parametrize(
    ("list_path", "ds"),
    [
        (HUB_LIST, ds_with_vpn(GLOBAL, hubs=(HUB_A,))),
        (
            BRIDGE_LIST,
            ds_with_vpn(GLOBAL, hubs=(HUB_A,), bridges=(HUB_A,)),
        ),
        (
            BACKHAUL_LIST,
            ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,)),
        ),
    ],
)
async def test_a_hub_listed_twice_raises(list_path: str, ds: dict[str, Any]):
    state = await profile_of(ds, HUB_A)
    vpn = state.profile.device_settings.segments[0].vpn
    holder = {
        HUB_LIST: vpn.hub_edges.vpn_hubs,
        BRIDGE_LIST: vpn.edge_to_edge_detail.vpn_hubs,
        BACKHAUL_LIST: vpn.backhaul_edges,
    }[list_path]
    holder.append(VpnHub.model_validate(hub_entry(HUB_A)))

    error = await refused(state)

    assert_names(error, "Hub-A", "twice", lists=(list_path,))


# --- rule 3: Edge to Edge through hubs needs a bridge hub ------------------------


async def test_edge_to_edge_through_hubs_with_no_bridge_hub_raises():
    state = await profile_of(ds_with_vpn(GLOBAL, hubs=(HUB_A,)), HUB_A)
    await edit(
        state, {"edgeToEdge": True, "edgeToEdgeDetail": {"useCloudGateway": False}}
    )

    assert_names(await refused(state), "useCloudGateway", lists=(BRIDGE_LIST,))


async def test_edge_to_edge_through_the_cloud_gateway_needs_no_bridge_hub():
    state = await profile_of(ds_with_vpn(GLOBAL, hubs=(HUB_A,)), HUB_A)
    await edit(state, {"edgeToEdge": True})

    assert state.plan().steps == ()


# --- rule 4: a backhaul or bridge hub is a hub of the segment --------------------


@pytest.mark.parametrize(
    ("path", "list_path"),
    [
        (("edgeToEdgeDetail", "vpnHubs"), BRIDGE_LIST),
        (("backHaulEdges",), BACKHAUL_LIST),
    ],
    ids=["bridge", "backhaul"],
)
async def test_a_hub_that_is_not_in_the_hub_list_cannot_be_added_to_another(
    path: tuple[str, ...], list_path: str
):
    state = await profile_of(ds_with_vpn(GLOBAL, hubs=(HUB_A,)), HUB_A)
    change: Any = add(HUB_B.name)
    for key in reversed(path):
        change = {key: change}
    await edit(state, change)

    error = await refused(state)

    assert_names(error, "Hub-B", HUB_LIST, lists=(HUB_LIST, list_path))
    assert error.hubs == ("Hub-B",)


async def test_a_hub_left_in_backhaul_after_leaving_the_hub_list_raises():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A, HUB_B), backhaul=(HUB_A,))
    state = await profile_of(ds, HUB_A, HUB_B)
    await edit(state, {"edgeToEdgeHub": {"vpnHubs": remove(HUB_A.name)}})

    error = await refused(state)

    assert_names(error, "Hub-A", "backhaul", lists=(HUB_LIST, BACKHAUL_LIST))


async def test_a_hub_removed_from_every_list_is_allowed():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A, HUB_B), backhaul=(HUB_A,))
    state = await profile_of(ds, HUB_A, HUB_B)
    await edit(
        state,
        {
            "edgeToEdgeHub": {"vpnHubs": remove(HUB_A.name)},
            "backHaulEdges": remove(HUB_A.name),
        },
    )

    assert [s.render() for s in state.plan().steps] == [
        "disable hub for Hub-A on segment Global Segment"
    ]


# --- rule 5: a segment with hubs has its VPN on ----------------------------------


async def test_a_hub_listed_with_the_vpn_off_raises():
    state = await profile_of(ds_with_vpn(GLOBAL, enabled=False))
    await edit(state, {"edgeToEdgeHub": {"enabled": True, "vpnHubs": add(HUB_A.name)}})

    assert_names(await refused(state), "vpn.enabled", lists=(HUB_LIST,))


async def test_turning_the_vpn_off_while_it_lists_hubs_says_to_remove_them_first():
    state = await profile_of(ds_with_vpn(GLOBAL, hubs=(HUB_A,)), HUB_A)
    await edit(state, {"enabled": False})

    error = await refused(state)

    assert_names(error, "Remove the hubs first", "Hub-A", lists=(HUB_LIST,))


# --- rule 6: an added hub is a hub of the enterprise ----------------------------


async def with_entry_added(
    state: ConfigState, entry: dict[str, Any], *, to_list: str = "hub"
) -> None:
    """Give the Global Segment the list *entry* as it is (a patch names a hub, so it could not give a wrong one), and turn the list on."""
    vpn = state.profile.device_settings.segments[0].vpn
    vpn.enabled = True
    vpn.hub_edges.enabled = True
    hub = VpnHub.model_validate(entry)
    vpn.hub_edges.vpn_hubs.append(hub)
    if to_list == "backhaul":
        vpn.backhaul_edges.append(hub)


async def test_an_added_cluster_member_raises():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await with_entry_added(state, hub_entry(MEMBER))

    error = await refused(state)

    assert_names(error, "Member-1", "member of a hub cluster", lists=(HUB_LIST,))


async def test_an_added_edge_that_is_no_hub_of_the_enterprise_raises():
    state = await profile_of(ds_with_vpn(GLOBAL), loaded=(HUB_A, HUB_B, CLUSTER))
    await with_entry_added(state, hub_entry(NOT_A_HUB))

    error = await refused(state)

    assert_names(error, "Branch-1", "no Edge or hub cluster", lists=(HUB_LIST,))


async def test_an_added_edge_listed_as_a_cluster_raises():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await with_entry_added(state, hub_entry(HUB_A) | {"type": "edgeHubCluster"})

    error = await refused(state)

    assert_names(error, "Hub-A", "edgeHubCluster", "an Edge", lists=(HUB_LIST,))
    assert error.hubs == ("Hub-A",)


async def test_an_added_cluster_listed_as_an_edge_raises():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await with_entry_added(state, hub_entry(CLUSTER) | {"type": "edgeHub"})

    error = await refused(state)

    assert_names(error, "Cluster-1", "edgeHub", "a hub cluster", lists=(HUB_LIST,))


async def test_an_added_hub_with_another_hubs_name_raises():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await with_entry_added(state, hub_entry(HUB_A) | {"name": HUB_B.name})

    error = await refused(state)

    assert_names(error, "Hub-B", "Hub-A", lists=(HUB_LIST,))


async def test_an_added_hubs_name_is_compared_without_regard_to_case():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await with_entry_added(state, hub_entry(HUB_A) | {"name": HUB_A.name.upper()})

    assert [s.render() for s in state.plan().steps] == [
        "enable hub for Hub-A on segment Global Segment"
    ]


async def test_an_added_hub_of_the_enterprise_passes():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await edit(
        state,
        {"enabled": True, "edgeToEdgeHub": {"enabled": True, "vpnHubs": add("Hub-A")}},
    )

    assert state.plan().check_warnings == ()


# --- a rule broken as read is a warning --------------------------------------------


def broken_as_read() -> dict[str, Any]:
    """A segment whose backhaul list holds Hub-A, which its hub list (Hub-B) does not."""
    return ds_with_vpn(GLOBAL, hubs=(HUB_B,), backhaul=(HUB_A,))


async def test_a_rule_already_broken_as_read_warns():
    state = await profile_of(broken_as_read(), HUB_A, HUB_B)
    await edit(state, {"conditionalBackhaul": True})

    (warning,) = as_read_warnings(state)

    assert warning.code == "hub_list_as_read"
    assert "Hub-A" in warning.message and GLOBAL[1] in warning.message
    assert warning in state.plan().warnings
    assert state.plan().steps == ()


async def test_a_rule_broken_as_read_with_no_edit_warns_nowhere():
    state = await profile_of(broken_as_read(), HUB_A, HUB_B)

    assert as_read_warnings(state) == []


async def test_another_hub_broken_the_same_way_is_an_error():
    state = await profile_of(broken_as_read(), HUB_A, HUB_B)
    await edit(state, {"backHaulEdges": add(CLUSTER.name)})

    error = await refused(state)

    assert_names(error, "Cluster-1", lists=(HUB_LIST, BACKHAUL_LIST))
    assert error.hubs == ("Cluster-1",)
    assert "Hub-A" not in str(error)  # as read, so only a warning


@pytest.mark.parametrize(
    ("ds", "text"),
    [
        (
            ds_with_vpn(GLOBAL, hubs=(HUB_A,), hub_list_enabled=False),
            "edgeToEdgeHub.enabled",
        ),
        (ds_with_vpn(GLOBAL, enabled=False, hubs=(HUB_A,)), "vpn.enabled"),
    ],
    ids=["hub-list-off", "vpn-off"],
)
async def test_a_segment_with_its_hub_list_or_vpn_off_as_read_warns_for_an_unrelated_edit(
    ds: dict[str, Any], text: str
):
    state = await profile_of(ds, HUB_A)
    await edit(state, {"conditionalBackhaul": True})

    (warning,) = as_read_warnings(state)

    assert text in warning.message and "Hub-A" in warning.message


@pytest.mark.parametrize(
    ("ds", "text"),
    [
        (ds_with_vpn(GLOBAL, hubs=(HUB_A,), hub_list_enabled=False), "edgeToEdgeHub"),
        (ds_with_vpn(GLOBAL, enabled=False, hubs=(HUB_A,)), "vpn.enabled"),
    ],
    ids=["hub-list-off", "vpn-off"],
)
async def test_adding_a_hub_to_a_segment_already_off_as_read_raises(
    ds: dict[str, Any], text: str
):
    state = await profile_of(ds, HUB_A, HUB_B)
    await edit(state, {"edgeToEdgeHub": {"vpnHubs": add(HUB_B.name)}})

    error = await refused(state)

    assert_names(error, text, "Hub-B", lists=(HUB_LIST,))


async def test_adding_a_listed_hub_to_another_list_of_a_segment_with_the_vpn_off_raises():
    # Hub-A is in the hub list as read, with vpn.enabled off: the backhaul
    # enable for it would be planned on a segment whose VPN is off.
    ds = ds_with_vpn(GLOBAL, enabled=False, hubs=(HUB_A,))
    state = await profile_of(ds, HUB_A)
    await edit(state, {"backHaulEdges": add(HUB_A.name)})

    error = await refused(state)

    assert_names(error, "vpn.enabled", "Hub-A", lists=(BACKHAUL_LIST,))


@pytest.mark.parametrize(
    ("path", "list_path"),
    [
        (("edgeToEdgeDetail", "vpnHubs"), BRIDGE_LIST),
        (("backHaulEdges",), BACKHAUL_LIST),
    ],
    ids=["bridge", "backhaul"],
)
async def test_adding_a_listed_hub_to_another_list_of_a_segment_with_the_hub_list_off_raises(
    path: tuple[str, ...], list_path: str
):
    # Enabling a bridge or backhaul sets the hub role too, on a segment whose
    # Branch-to-Hub list is off.
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,), hub_list_enabled=False)
    state = await profile_of(ds, HUB_A)
    change: Any = add(HUB_A.name)
    for key in reversed(path):
        change = {key: change}
    await edit(state, change)

    error = await refused(state)

    assert_names(error, "edgeToEdgeHub.enabled", "Hub-A", lists=(HUB_LIST,))


@pytest.mark.parametrize(
    "ds",
    [
        ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,), hub_list_enabled=False),
        ds_with_vpn(GLOBAL, enabled=False, hubs=(HUB_A,), backhaul=(HUB_A,)),
    ],
    ids=["hub-list-off", "vpn-off"],
)
async def test_removing_a_hub_from_one_list_of_a_segment_already_off_still_warns(
    ds: dict[str, Any],
):
    state = await profile_of(ds, HUB_A)
    await edit(state, {"backHaulEdges": remove(HUB_A.name)})

    (warning,) = as_read_warnings(state)

    assert "Hub-A" in warning.message
    assert [s.render() for s in state.plan().steps] == [
        "disable backhaul for Hub-A on segment Global Segment"
    ]


async def test_segments_with_no_vpn_change_are_not_checked():
    state = await profile_of(broken_as_read(), HUB_A, HUB_B)
    patch = {"segments": {GLOBAL[1]: {"ospf": {"enabled": True}}}}
    _ = await ConfigPipeline(client=None).apply(state, module=DS, patch=patch)
    assert state.plan().modules  # the edit is a change of the module

    assert as_read_warnings(state) == []


# --- the checks need the hub resolver only for an added hub --------------------------


async def test_a_plan_with_no_hub_change_needs_no_hub_resolver():
    # profile_state registers no HubIdentity resolver at all.
    state = await profile_state(
        [ds_with_vpn(GLOBAL, hubs=(HUB_A,)), ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=profile_hub_rows(
            segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]), service_row(HUB_A, 1)
        ),
    )
    await edit(state, {"conditionalBackhaul": True})

    plan = state.plan()

    assert plan.steps == ()
    assert [w for w in plan.check_warnings if w.code == "hub_list_as_read"] == []


async def test_an_added_hub_needs_a_loaded_hub_resolver_to_be_checked():
    state = await profile_of(ds_with_vpn(GLOBAL))
    await edit(
        state,
        {"enabled": True, "edgeToEdgeHub": {"enabled": True, "vpnHubs": add("Hub-A")}},
    )
    resolver = state.resolvers.get_resolver(HubResolver)
    state.resolvers.register(resolver.unloaded())

    with pytest.raises(ResolverNotLoadedError):
        _ = state.plan()
