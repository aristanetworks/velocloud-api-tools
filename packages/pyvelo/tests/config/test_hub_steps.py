"""A hub added to or removed from a segment's hub lists plans its role steps, in the UI's order.

``derive_steps`` compares the device settings module as read with the module
as edited; ``state.plan()`` does it for a profile and carries the steps on
``ConfigPlan.steps``. A removal needs no hub resolver: the hub is named by the
list entry that goes. The steps are a function of the module as read and as
edited, so a plan made twice has the same steps.
"""

import copy
import uuid
from typing import Any, Literal

import pytest
from hub_helpers import (
    CLUSTER,
    HUB_A,
    HUB_B,
    Hub,
    ds_with_vpn,
    hub_entry,
    hub_row,
)
from hub_state import (  # pyright: ignore[reportImplicitRelativeImport]
    hub_edge_state,
    hub_pipeline,
    hub_profile_state,
    profile_hub_rows,
)
from profile_state import profile_state  # pyright: ignore[reportImplicitRelativeImport]
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.models.configuration.device_settings.segment import VpnHub
from pyvelo.config import HubStep
from pyvelo.config.hub_steps import (
    HubRef,
    derive_steps,
    role_disagreements,
)
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.state import ConfigState
from pyvelo.core import ConfigContext, delete
from pyvelo.core.identity import HubIdentity, ResolverNotLoadedError
from refs_helpers import CONFIG, segment_row
from segment_helpers import GLOBAL, GUEST, Segment, ds_entry, firewall_entry, qos_entry

DS = "deviceSettings"
SERVICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
type Role = Literal["hub", "bridge", "backhaul"]
ROLES: tuple[Role, ...] = ("hub", "bridge", "backhaul")
LIST_KEYS = {
    "hub": ("edgeToEdgeHub", "vpnHubs"),
    "bridge": ("edgeToEdgeDetail", "vpnHubs"),
    "backhaul": ("backHaulEdges",),
}


def refs(*rows: dict[str, Any]) -> dict[str, Any]:
    """The refs of a profile with the Global Segment and Guest, and the hub rows *rows*."""
    return profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        *rows,
    )


def service_row(
    hub: Hub, n: int, *roles: Role, segment: Segment = GLOBAL
) -> dict[str, Any]:
    return hub_row(
        hub,
        service_id=600 + n,
        service_logical_id=SERVICE,
        segment=segment,
        roles=roles,
    )


async def profile_of(
    ds: list[dict[str, Any]], *rows: dict[str, Any], **knobs: Any
) -> ConfigState:
    """A profile whose three modules hold the segments of *ds* (Global and Guest), with the hub rows *rows*."""
    segments = [e["segment"]["segmentId"] for e in ds]
    qos = [qos_entry(s) for s in (GLOBAL, GUEST) if s[0] in segments]
    firewall = [firewall_entry(s) for s in (GLOBAL, GUEST) if s[0] in segments]
    return await hub_profile_state(ds, qos, firewall, ds_refs=refs(*rows), **knobs)


def vpn_patch(role: Role, change: dict[str, Any]) -> dict[str, Any]:
    """A patch of a vpn hub list: *change* is the list's patch."""
    patch: Any = change
    for key in reversed(LIST_KEYS[role]):
        patch = {key: patch}
    return {"vpn": patch}


async def edit(
    state: ConfigState,
    role: Role,
    change: dict[str, Any],
    segment: str = GLOBAL[1],
    *,
    hub_list: bool | None = None,
    vpn: bool | None = None,
) -> None:
    """Patch the list of *role*, and the flags a hub list needs: *hub_list* is ``edgeToEdgeHub.enabled`` and *vpn* ``vpn.enabled``."""
    patch = vpn_patch(role, change)
    if hub_list is not None:
        patch["vpn"].setdefault("edgeToEdgeHub", {})["enabled"] = hub_list
    if vpn is not None:
        patch["vpn"]["enabled"] = vpn
    patch = {"segments": {segment: patch}}
    _ = await ConfigPipeline(client=None).apply(state, module=DS, patch=patch)


def add_hub(name: str) -> dict[str, Any]:
    return {"+": {"_op": "add", "name": name}}


def remove_hub(name: str) -> dict[str, Any]:
    return {name: delete()}


def ref_of(hub: Hub, *, as_added: bool) -> HubRef:
    """The ``HubRef`` of *hub*: with its IDs when the step adds it, without them for a removal."""
    ids = (hub.id, None) if hub.kind == "edge" else (None, hub.id)
    return HubRef(
        kind=hub.kind,
        logical_id=uuid.UUID(hub.logical_id),
        name=hub.name,
        edge_id=ids[0] if as_added else None,
        cluster_id=ids[1] if as_added else None,
    )


def step(
    action: Literal["enable", "disable"],
    role: Role,
    hub: Hub,
    segment: Segment = GLOBAL,
) -> HubStep:
    return HubStep(
        action=action,
        role=role,
        hub=ref_of(hub, as_added=action == "enable"),
        segment=segment[1],
        segment_id=segment[0],
        segment_object_id=segment[3],
    )


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("hub", [HUB_A, CLUSTER], ids=lambda h: h.kind)
async def test_a_hub_added_to_a_list_plans_an_enable(role: Role, hub: Hub):
    # A bridge or backhaul hub is a hub of the segment already.
    ds = ds_entry(GLOBAL) if role == "hub" else ds_with_vpn(GLOBAL, hubs=(hub,))
    rows = () if role == "hub" else (service_row(hub, 1, "hub"),)
    state = await profile_of([ds, ds_entry(GUEST)], *rows)
    await edit(state, role, add_hub(hub.name), hub_list=True, vpn=True)

    assert state.plan().steps == (step("enable", role, hub),)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("hub", [HUB_A, CLUSTER], ids=lambda h: h.kind)
async def test_a_hub_removed_from_a_list_plans_a_disable_without_ids(
    role: Role, hub: Hub
):
    # In the other two lists too, so that it is only the one list that changes.
    ds = ds_with_vpn(GLOBAL, hubs=(hub,), bridges=(hub,), backhaul=(hub,))
    state = await profile_of(
        [ds, ds_entry(GUEST)], service_row(hub, 1, "hub", "bridge", "backhaul")
    )
    if role == "hub":
        # A hub that leaves the hub list leaves the other two.
        await edit(state, "bridge", remove_hub(hub.name))
        await edit(state, "backhaul", remove_hub(hub.name))
    await edit(
        state, role, remove_hub(hub.name), hub_list=False if role == "hub" else None
    )

    (planned,) = state.plan().steps
    assert planned == step("disable", role, hub)
    assert (planned.hub.edge_id, planned.hub.cluster_id) == (None, None)
    assert planned.gives is None


async def test_an_enable_gives_a_service_and_a_disable_gives_nothing():
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    await edit(state, "hub", add_hub(HUB_A.name), hub_list=True, vpn=True)

    (planned,) = state.plan().steps
    assert planned.gives == "service"


async def test_a_step_renders_as_a_line():
    assert (
        step("enable", "backhaul", HUB_A).render()
        == "enable backhaul for Hub-A on segment Global Segment"
    )
    assert (
        step("disable", "bridge", CLUSTER, GUEST).render()
        == "disable bridge for cluster Cluster-1 on segment Guest"
    )


async def test_the_ui_order():
    ds = [
        ds_with_vpn(
            GLOBAL,
            hubs=(HUB_B, CLUSTER),
            bridges=(CLUSTER,),
            backhaul=(HUB_B, CLUSTER),
        ),
        ds_entry(GUEST),
    ]
    state = await profile_of(
        ds,
        service_row(HUB_B, 1, "hub", "backhaul"),
        service_row(CLUSTER, 2, "hub", "bridge", "backhaul"),
    )
    await edit(state, "backhaul", remove_hub(HUB_B.name))
    for role in ROLES:
        await edit(state, role, remove_hub(CLUSTER.name))
        await edit(state, role, add_hub(HUB_A.name))

    assert [s.render() for s in state.plan().steps] == [
        "disable backhaul for Hub-B on segment Global Segment",
        "disable hub for cluster Cluster-1 on segment Global Segment",
        "enable hub for Hub-A on segment Global Segment",
        "enable backhaul for Hub-A on segment Global Segment",
        "enable bridge for Hub-A on segment Global Segment",
    ]


async def test_a_hub_removed_from_the_hub_list_gets_no_other_disable():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,), bridges=(HUB_A,), backhaul=(HUB_A,))
    state = await profile_of(
        [ds, ds_entry(GUEST)], service_row(HUB_A, 1, "hub", "bridge", "backhaul")
    )
    for role in ROLES:
        await edit(state, role, remove_hub(HUB_A.name), hub_list=False)

    assert state.plan().steps == (step("disable", "hub", HUB_A),)


async def test_two_segments_follow_the_segments_order():
    # Guest is first in the module, and is edited last.
    state = await profile_of([ds_entry(GUEST), ds_entry(GLOBAL)])
    for segment in (GLOBAL, GUEST):
        await edit(
            state, "hub", add_hub(HUB_A.name), segment[1], hub_list=True, vpn=True
        )

    assert state.plan().steps == (
        step("enable", "hub", HUB_A, GUEST),
        step("enable", "hub", HUB_A, GLOBAL),
    )


async def test_hubs_follow_the_order_of_their_list():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A, HUB_B))
    state = await profile_of(
        [ds, ds_entry(GUEST)],
        service_row(HUB_A, 1, "hub"),
        service_row(HUB_B, 2, "hub"),
    )
    await edit(state, "backhaul", add_hub(HUB_B.name))
    await edit(state, "backhaul", add_hub(HUB_A.name))

    assert [s.hub.name for s in state.plan().steps] == ["Hub-B", "Hub-A"]


async def test_a_repeated_plan_derives_the_same_steps():
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    await edit(state, "hub", add_hub(HUB_A.name), hub_list=True, vpn=True)

    first = state.plan().steps
    assert first and state.plan().steps == first


async def test_a_plan_with_no_list_change_has_no_steps():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,))
    state = await profile_of([ds, ds_entry(GUEST)], service_row(HUB_A, 1, "hub"))
    state.profile.device_settings.segments[0].vpn.conditional_backhaul = True

    assert state.plan().steps == ()


async def test_an_edge_target_has_no_steps():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,))
    state = await hub_edge_state(
        [ds_entry(GLOBAL)],
        [qos_entry(GLOBAL)],
        profile_ds=[ds, ds_entry(GUEST)],
        profile_qos=[qos_entry(GLOBAL), qos_entry(GUEST)],
        profile_refs=refs(service_row(HUB_A, 1, "hub")),
    )
    state.edge.device_settings.lan.networks.clear()

    plan = state.plan()

    assert plan.steps == ()
    assert plan.check_warnings == ()


async def test_removals_need_no_hub_resolver():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,))
    # profile_state registers no HubIdentity resolver at all.
    state = await profile_state(
        [ds, ds_entry(GUEST)],
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=refs(service_row(HUB_A, 1, "hub", "backhaul")),
    )
    await edit(state, "backhaul", remove_hub(HUB_A.name))
    await edit(state, "hub", remove_hub(HUB_A.name), hub_list=False)

    assert state.plan().steps == (step("disable", "hub", HUB_A),)


async def test_an_added_hub_needs_the_resolver():
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    await edit(state, "hub", add_hub(HUB_A.name), hub_list=True, vpn=True)
    state.resolvers.register(state.resolvers.get_resolver(HubResolver).unloaded())

    with pytest.raises(ResolverNotLoadedError) as error:
        _ = state.plan()
    assert error.value.identity_type is HubIdentity

    pipeline, _ = hub_pipeline(state)
    result = await pipeline.save(state, dry_run=True)
    assert result.plan is not None
    assert result.plan.steps == (step("enable", "hub", HUB_A),)


async def test_render_shows_the_steps_first():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_B,))
    state = await profile_of([ds, ds_entry(GUEST)], service_row(HUB_B, 1, "hub"))
    await edit(state, "hub", remove_hub(HUB_B.name))
    await edit(state, "hub", add_hub(HUB_A.name))

    lines = state.plan().render().splitlines()

    assert lines[1:4] == [
        "steps:",
        "  disable hub for Hub-B on segment Global Segment",
        "  enable hub for Hub-A on segment Global Segment",
    ]
    assert lines[4].startswith(f"{DS}: update")


async def test_a_plan_without_steps_renders_no_steps_block():
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    state.profile.device_settings.segments[0].vpn.enabled = True

    assert "steps:" not in state.plan().render()


async def test_a_segment_with_no_row_takes_its_object_from_the_segment_resolver():
    ds = [ds_entry(GLOBAL), ds_with_vpn(GUEST, hubs=(HUB_A,))]
    state = await hub_profile_state(
        ds,
        [qos_entry(GLOBAL), qos_entry(GUEST)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST)],
        ds_refs=profile_hub_rows(segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2])),
    )
    await edit(state, "backhaul", add_hub(HUB_A.name), GUEST[1])

    assert state.plan().steps == (step("enable", "backhaul", HUB_A, GUEST),)


def settings(*entries: dict[str, Any]) -> DeviceSettingsData:
    return DeviceSettingsData.model_validate(
        {"lan": {"networks": [], "interfaces": []}, "segments": list(entries)},
        context={"config_level": ConfigContext.PROFILE},
    )


async def a_loaded_resolver() -> HubResolver:
    state = await profile_of([ds_entry(GLOBAL)])
    return state.resolvers.get_resolver(HubResolver)


async def test_derive_steps_names_a_removal_by_its_entry_alone():
    as_read = settings(ds_with_vpn(GLOBAL, hubs=(HUB_A,)))
    now = copy.deepcopy(as_read)
    now.segments[0].vpn.hub_edges.vpn_hubs.clear()

    steps = derive_steps(
        as_read, now, segment_object_ids={GLOBAL[0]: GLOBAL[3]}, hubs=None
    )

    assert steps == (step("disable", "hub", HUB_A),)


async def test_derive_steps_needs_the_resolver_for_an_added_hub():
    as_read = settings(ds_with_vpn(GLOBAL))
    now = copy.deepcopy(as_read)
    now.segments[0].vpn.hub_edges.vpn_hubs.append(
        VpnHub.model_validate(hub_entry(HUB_A))
    )
    object_ids = {GLOBAL[0]: GLOBAL[3]}

    with pytest.raises(ResolverNotLoadedError):
        _ = derive_steps(as_read, now, segment_object_ids=object_ids, hubs=None)
    unloaded = (await a_loaded_resolver()).unloaded()
    with pytest.raises(ResolverNotLoadedError):
        _ = derive_steps(as_read, now, segment_object_ids=object_ids, hubs=unloaded)
    steps = derive_steps(
        as_read,
        now,
        segment_object_ids=object_ids,
        hubs=await a_loaded_resolver(),
    )
    assert steps == (step("enable", "hub", HUB_A),)


async def test_derive_steps_reordering_is_no_step():
    as_read = settings(ds_with_vpn(GLOBAL, hubs=(HUB_A, HUB_B)))
    now = copy.deepcopy(as_read)
    now.segments[0].vpn.hub_edges.vpn_hubs.reverse()

    assert (
        derive_steps(as_read, now, segment_object_ids={GLOBAL[0]: GLOBAL[3]}, hubs=None)
        == ()
    )


# --- roles that disagree with the lists ----------------------------------------


def disagreements(state: ConfigState):
    return [w for w in state.plan().check_warnings if w.code == "hub_roles_disagree"]


async def test_roles_that_disagree_with_the_lists_warn():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,))
    state = await profile_of(
        [ds, ds_entry(GUEST)],
        service_row(HUB_A, 1, "hub"),  # no backhaul role
    )

    (warning,) = disagreements(state)

    assert HUB_A.name in warning.message
    assert "backhaul" in warning.message
    assert warning.path == 'deviceSettings: segments["Global Segment"].vpn[Hub-A]'
    assert warning in state.plan().warnings
    assert f"warning: {warning.message}" in state.plan().render()


async def test_roles_that_agree_with_the_lists_do_not_warn():
    ds = ds_with_vpn(
        GLOBAL, hubs=(HUB_A, CLUSTER), bridges=(CLUSTER,), backhaul=(HUB_A,)
    )
    state = await profile_of(
        [ds, ds_entry(GUEST)],
        service_row(HUB_A, 1, "hub", "backhaul"),
        service_row(CLUSTER, 2, "hub", "bridge"),
    )

    assert disagreements(state) == []


async def test_a_clusters_roles_are_read_from_its_data_for_the_segment():
    ds = ds_with_vpn(GLOBAL, hubs=(CLUSTER,), bridges=(CLUSTER,))
    state = await profile_of(
        [ds, ds_entry(GUEST)],
        service_row(CLUSTER, 2, "hub"),  # no bridge role
    )

    (warning,) = disagreements(state)

    assert CLUSTER.name in warning.message and "bridge" in warning.message


async def test_role_disagreements_reads_a_clusters_data_by_configuration():
    ds = ds_with_vpn(GLOBAL, hubs=(CLUSTER,), bridges=(CLUSTER,))
    row = service_row(CLUSTER, 2, "hub", "bridge")
    by_segment = row["data"][str(CONFIG)]  # the cluster's own data: by configuration
    all_refs = {"deviceSettings:vpn:edgeHubCluster": row}

    assert role_disagreements(settings(ds), all_refs, CONFIG) == ()

    by_segment[str(GLOBAL[0])]["roles"]["edgeToEdgeBridge"] = False
    (warning,) = role_disagreements(settings(ds), all_refs, CONFIG)

    assert warning.code == "hub_roles_disagree" and "bridge" in warning.message
    # Another profile's roles are not this profile's.
    assert role_disagreements(settings(ds), all_refs, CONFIG + 1) == ()


async def test_a_hub_with_no_row_is_not_a_role_disagreement():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,))
    state = await profile_of([ds, ds_entry(GUEST)])

    assert disagreements(state) == []
