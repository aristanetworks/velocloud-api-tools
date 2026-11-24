"""A save that adds or removes hubs sends the role steps first, then writes the hub rows they made.

The save of a profile whose edit changes a segment's hub lists reads the
module's version, reads the hub services, sends each step not already in place
in the UI's order, reads the services again and then writes the device
settings with the rows of the hubs added to (or removed from) the Branch-to-Hub
list. The store (``ModuleStore`` with ``vpn_check``) refuses a device settings
write whose hub list and hub rows disagree, as the VCO does.
"""

import asyncio
import copy
import uuid
from typing import Any, Literal, cast

import httpx
import pytest
from hub_helpers import (
    CLUSTER,
    HUB_A,
    HUB_B,
    Hub,
    ds_with_vpn,
    hub_row,
)
from hub_state import (  # pyright: ignore[reportImplicitRelativeImport]
    hub_pipeline,
    hub_profile_state,
    profile_hub_rows,
)
from pyvelo.client.exceptions import (
    ConfigConflictError,
    HubListError,
    RefsDroppedError,
    VcoError,
)
from pyvelo.client.hub_services import BACKHAUL_REF, CLUSTER_HUB_REF, EDGE_HUB_REF
from pyvelo.client.models.configuration.device_settings.root import DeviceSettingsData
from pyvelo.client.schemas.enterprise import EdgeHubClusterService, EdgeHubService
from pyvelo.core import ConfigContext, delete
from pyvelo.core.identity import HubServiceNotFoundError
from pyvelo.testing import ModuleStore
from pyvelo_intent import (
    HubStep,
    SaveStepError,
    StepOutcome,
    save_progress,
)
from pyvelo_intent.config.hub_calls import (
    HubServices,
    hub_rows,
    in_place,
    read_services,
    send_step,
)
from pyvelo_intent.config.hub_steps import HubRef
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from refs_helpers import CONFIG, DS_ID, QOS_ID, segment_row
from segment_helpers import GLOBAL, GUEST, ds_entry, firewall_entry, qos_entry
from test_backhaul_refs_state import (  # pyright: ignore[reportImplicitRelativeImport]
    SERVICE_A,
    SERVICE_A_ID,
    a_profile,
    use_hub,
)

DS = "deviceSettings"
SERVICES = "enterprise/getEnterpriseServices"
VERSION = "configuration/getConfigurationModules"
CONFIGURATION = "configuration/getConfiguration"
UPDATE = "configuration/updateConfigurationModule"
type Role = Literal["hub", "bridge", "backhaul"]
ROLES: tuple[Role, ...] = ("hub", "bridge", "backhaul")
LIST_KEYS = {
    "hub": ("edgeToEdgeHub", "vpnHubs"),
    "bridge": ("edgeToEdgeDetail", "vpnHubs"),
    "backhaul": ("backHaulEdges",),
}
SUFFIXES: dict[Role, str] = {
    "hub": "EdgeHub",
    "bridge": "EdgeToEdgeBridge",
    "backhaul": "BackHaul",
}
ROLE_FLAGS: dict[Role, str] = {
    "hub": "edgeHub",
    "bridge": "edgeToEdgeBridge",
    "backhaul": "backHaulEdge",
}
HUBS = (HUB_A, CLUSTER)


def call_name(action: str, role: Role, hub: Hub) -> str:
    """The wire method of the role call for *action*, *role* and the kind of *hub*."""
    scope = "edge" if hub.kind == "edge" else "enterprise"
    who = "Edge" if hub.kind == "edge" else "Cluster"
    return f"{scope}/{action}{who}For{SUFFIXES[role]}"


def refs(*rows: dict[str, Any]) -> dict[str, Any]:
    """The refs of a profile with the Global Segment and Guest, and the hub rows *rows*."""
    return profile_hub_rows(
        segment_row(GLOBAL[0], GLOBAL[3], GLOBAL[2]),
        segment_row(GUEST[0], GUEST[3], GUEST[2]),
        *rows,
    )


def service_row(hub: Hub, *roles: Role, segment=GLOBAL) -> dict[str, Any]:
    """The row of *hub*'s service for *segment*: the Edge's own service, the cluster itself."""
    service_id = hub.id if hub.kind == "cluster" else 600 + hub.id
    return hub_row(
        hub,
        service_id=service_id,
        service_logical_id=str(uuid.UUID(int=service_id)),
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


async def edit(
    state: ConfigState,
    role: Role,
    change: dict[str, Any],
    *,
    hub_list: bool | None = None,
    vpn: bool | None = None,
) -> None:
    """Patch the list of *role* of the Global Segment; *hub_list* is ``edgeToEdgeHub.enabled`` and *vpn* ``vpn.enabled``."""
    patch: Any = change
    for key in reversed(LIST_KEYS[role]):
        patch = {key: patch}
    patch = {"vpn": patch}
    if hub_list is not None:
        patch["vpn"].setdefault("edgeToEdgeHub", {})["enabled"] = hub_list
    if vpn is not None:
        patch["vpn"]["enabled"] = vpn
    patch = {"segments": {GLOBAL[1]: patch}}
    _ = await ConfigPipeline(client=None).apply(state, module=DS, patch=patch)


def add_hub(name: str) -> dict[str, Any]:
    return {"+": {"_op": "add", "name": name}}


def remove_hub(name: str) -> dict[str, Any]:
    return {name: delete()}


def methods(store: ModuleStore) -> list[str]:
    return [method for method, _ in store.requests]


def calls(store: ModuleStore) -> list[tuple[str, dict[str, Any]]]:
    """The role calls the store got, with their parameters."""
    return [
        (method, params)
        for method, params in store.requests
        if method.startswith(("edge/", "enterprise/")) and method != SERVICES
    ]


def role_call_params(hub: Hub) -> dict[str, Any]:
    return {
        "id": hub.id,
        "configurationId": CONFIG,
        "moduleId": DS_ID,
        "segmentObjectId": GLOBAL[3],
    }


def sent_refs(store: ModuleStore) -> dict[str, Any]:
    """The refs of the last update the store got."""
    updates = [p for m, p in store.requests if m == UPDATE]
    return cast(dict[str, Any], updates[-1]["_update"]["refs"])


def stored_rows(store: ModuleStore, name: str) -> list[dict[str, Any]]:
    held = store.rows[DS_ID].get("refs", {}).get(name)
    return held if isinstance(held, list) else [] if held is None else [held]


def step_of(
    action: Literal["enable", "disable"], role: Role, hub: Hub, *, added: bool
) -> HubStep:
    ids = (hub.id, None) if hub.kind == "edge" else (None, hub.id)
    return HubStep(
        action,
        role,
        HubRef(
            hub.kind,
            uuid.UUID(hub.logical_id),
            hub.name,
            ids[0] if added else None,
            ids[1] if added else None,
        ),
        GLOBAL[1],
        GLOBAL[0],
        GLOBAL[3],
    )


async def two_lists_state() -> ConfigState:
    """A profile with no hub, and an edit that adds Hub-A to the hub list and the backhaul list."""
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    await edit(state, "hub", add_hub(HUB_A.name), hub_list=True, vpn=True)
    await edit(state, "backhaul", add_hub(HUB_A.name))
    return state


# -- the order of a save -------------------------------------------------------


async def test_a_hub_added_by_name_to_two_lists_is_enabled_and_carried():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)

    result = await pipeline.save(state)

    assert methods(store) == [
        VERSION,
        SERVICES,
        SERVICES,
        "edge/enableEdgeForEdgeHub",
        "edge/enableEdgeForBackHaul",
        SERVICES,
        SERVICES,
        UPDATE,
    ]
    services = [p for m, p in store.requests if m == SERVICES]
    assert [(p["type"], p.get("with")) for p in services] == [
        ("edgeHub", ["configuration"]),
        ("edgeHubCluster", None),
        ("edgeHub", ["configuration"]),
        ("edgeHubCluster", None),
    ]
    (service,) = services_of(store, "edgeHub")
    (row,) = stored_rows(store, EDGE_HUB_REF)
    assert row["enterpriseObjectId"] == service["id"]
    assert row["segmentObjectId"] == GLOBAL[3]
    assert [r["enterpriseObjectId"] for r in sent_refs(store)[EDGE_HUB_REF]] == [
        service["id"]
    ]
    assert service["data"]["roles"] == {
        "edgeHub": True,
        "edgeToEdgeBridge": False,
        "backHaulEdge": True,
    }
    assert result.plan is not None
    assert result.steps == tuple(StepOutcome(s, "sent") for s in result.plan.steps)
    assert [s.render() for s in result.plan.steps] == [
        "enable hub for Hub-A on segment Global Segment",
        "enable backhaul for Hub-A on segment Global Segment",
    ]
    assert [w.name for w in result.written] == [DS]
    again = state.plan()
    assert (again.steps, again.changed) == ((), ())


async def test_the_plan_the_save_shows_lists_the_rows_it_carries():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    result = await pipeline.save(state)
    (write,) = result.written
    (change,) = [c for c in write.plan.ref_changes if c.name == EDGE_HUB_REF]
    assert (change.added, change.segment) == (True, GLOBAL[1])
    assert f"+ refs {EDGE_HUB_REF} (Global Segment): " in write.plan.render()
    assert store.requests[-1][0] == UPDATE


async def test_the_role_calls_name_the_enterprise_when_the_state_knows_it():
    state = await two_lists_state()
    state.resolver_context.enterprise_id = 7
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.enterprise_id = 7

    _ = await pipeline.save(state)

    assert all(p.get("enterpriseId") == 7 for m, p in store.requests if m != UPDATE)


# -- one call per list and kind ------------------------------------------------


async def state_before(role: Role, hub: Hub, action: str) -> ConfigState:
    """A profile in which *hub* is where an edit of the list of *role* with *action* needs it, and the edit."""
    name = hub.name
    if action == "enable":
        if role == "hub":
            state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
            await edit(state, role, add_hub(name), hub_list=True, vpn=True)
        else:
            ds = ds_with_vpn(GLOBAL, hubs=(hub,))
            state = await profile_of(
                [ds, ds_entry(GUEST)], service_row(hub, "hub", segment=GLOBAL)
            )
            await edit(state, role, add_hub(name))
        return state
    ds = ds_with_vpn(GLOBAL, hubs=(hub,), bridges=(hub,), backhaul=(hub,))
    state = await profile_of(
        [ds, ds_entry(GUEST)], service_row(hub, *ROLES, segment=GLOBAL)
    )
    if role == "hub":
        # A hub that leaves the hub list leaves the other two.
        await edit(state, "bridge", remove_hub(name))
        await edit(state, "backhaul", remove_hub(name))
        await edit(state, role, remove_hub(name), hub_list=False)
    else:
        await edit(state, role, remove_hub(name))
    return state


@pytest.mark.parametrize("hub", HUBS, ids=lambda h: h.kind)
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("action", ["enable", "disable"])
async def test_each_list_and_kind_sends_its_call_with_the_ids_of_the_step(
    action: Literal["enable", "disable"], role: Role, hub: Hub
):
    state = await state_before(role, hub, action)
    pipeline, store = hub_pipeline(state, vpn_check=True)
    rows_as_read = {
        name: copy.deepcopy(stored_rows(store, name))
        for name in (EDGE_HUB_REF, CLUSTER_HUB_REF)
    }

    result = await pipeline.save(state)

    assert [(m, {k: p[k] for k in role_call_params(hub)}) for m, p in calls(store)] == [
        (call_name(action, role, hub), role_call_params(hub))
    ]
    (outcome,) = result.steps
    assert outcome.status == "sent"
    assert (outcome.step.action, outcome.step.role) == (action, role)
    ref = EDGE_HUB_REF if hub.kind == "edge" else CLUSTER_HUB_REF
    other = CLUSTER_HUB_REF if hub.kind == "edge" else EDGE_HUB_REF
    if role == "hub" and action == "disable":
        # The row of the hub that left the list is not sent, and not stored.
        assert sent_refs(store).get(ref) in (None, [])
        assert stored_rows(store, ref) == []
    elif action == "disable":
        # The hub stays in the hub list: its row is sent as read.
        assert sent_refs(store)[ref] == rows_as_read[ref][0]
        (stays,) = stored_rows(store, ref)
        assert stays["enterpriseObjectId"] == rows_as_read[ref][0]["enterpriseObjectId"]
    if action == "enable":
        (added,) = stored_rows(store, ref)
        assert (added["segmentObjectId"], added["configurationId"]) == (
            GLOBAL[3],
            CONFIG,
        )
        if hub.kind == "cluster":
            assert added["enterpriseObjectId"] == hub.id
    assert stored_rows(store, other) == []
    again = state.plan()
    assert (again.steps, again.changed) == ((), ())


async def test_a_removed_edge_hubs_service_is_deleted_by_the_call_and_its_row_left_out():
    state = await state_before("hub", HUB_A, "disable")
    pipeline, store = hub_pipeline(state, vpn_check=True)
    assert services_of(store, "edgeHub")

    _ = await pipeline.save(state)

    assert services_of(store, "edgeHub") == []
    assert EDGE_HUB_REF not in sent_refs(store)


def services_of(store: ModuleStore, kind: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], store.handle(SERVICES, {"type": kind}))


async def test_the_rows_of_hubs_that_stay_are_sent_as_read():
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A, CLUSTER))
    state = await profile_of(
        [ds, ds_entry(GUEST)],
        service_row(HUB_A, "hub", segment=GLOBAL),
        service_row(CLUSTER, "hub", segment=GLOBAL),
    )
    await edit(state, "hub", add_hub(HUB_B.name))
    pipeline, store = hub_pipeline(state, vpn_check=True)
    as_read = {
        name: copy.deepcopy(stored_rows(store, name))
        for name in (EDGE_HUB_REF, CLUSTER_HUB_REF)
    }

    _ = await pipeline.save(state)

    sent = sent_refs(store)
    assert sent[CLUSTER_HUB_REF] == as_read[CLUSTER_HUB_REF][0]
    assert sent[EDGE_HUB_REF][0] == as_read[EDGE_HUB_REF][0]
    assert len(sent[EDGE_HUB_REF]) == 2


# -- a second save -------------------------------------------------------------


async def test_a_repeated_save_skips_the_steps_already_in_place():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.fail_calls.add("edge/enableEdgeForBackHaul")

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    first = failed.value
    assert first.step is not None and first.step.role == "backhaul"
    assert [s.role for s in first.steps_done] == ["hub"]
    assert UPDATE not in methods(store)
    store.requests.clear()

    result = await pipeline.save(state)

    assert [m for m, _ in calls(store)] == ["edge/enableEdgeForBackHaul"]
    assert [(o.step.role, o.status) for o in result.steps] == [
        ("hub", "in_place"),
        ("backhaul", "sent"),
    ]
    assert methods(store)[-1] == UPDATE
    (hub_service,) = services_of(store, "edgeHub")
    assert len(stored_rows(store, EDGE_HUB_REF)) == 1
    assert (
        stored_rows(store, EDGE_HUB_REF)[0]["enterpriseObjectId"] == hub_service["id"]
    )
    assert state.plan().steps == ()


async def test_a_dry_run_sends_no_step_and_says_which_are_in_place():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.fail_calls.add("edge/enableEdgeForBackHaul")
    with pytest.raises(SaveStepError):
        _ = await pipeline.save(state)
    store.requests.clear()

    result = await pipeline.save(state, dry_run=True)

    assert methods(store) == [SERVICES, SERVICES]
    assert [(o.step.role, o.status) for o in result.steps] == [
        ("hub", "in_place"),
        ("backhaul", "would_send"),
    ]
    assert result.written == ()
    assert result.dry_run


async def test_a_dry_run_before_anything_is_sent_would_send_every_step():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)

    result = await pipeline.save(state, dry_run=True)

    assert methods(store) == [SERVICES, SERVICES]
    assert [o.status for o in result.steps] == ["would_send", "would_send"]
    assert (await pipeline.save(state, dry_run=True)).steps == result.steps


async def test_a_disable_answered_null_counts_as_done(
    monkeypatch: pytest.MonkeyPatch,
):
    ds = ds_with_vpn(GLOBAL, hubs=(HUB_A,), backhaul=(HUB_A,))
    state = await profile_of(
        [ds, ds_entry(GUEST)], service_row(HUB_A, "hub", "backhaul")
    )
    await edit(state, "backhaul", remove_hub(HUB_A.name))
    pipeline, store = hub_pipeline(state, vpn_check=True)
    original = pipeline.client.disable_edge_backhaul
    (service,) = services_of(store, "edgeHub")

    async def deleted_meanwhile(request: Any) -> Any:
        _ = store.objects.pop(service["id"])
        return await original(request)

    monkeypatch.setattr(pipeline.client, "disable_edge_backhaul", deleted_meanwhile)

    # The row stays in the module: the service is gone, which the write does not check.
    result = await pipeline.save(state)

    assert [o.status for o in result.steps] == ["sent"]
    assert "edge/disableEdgeForBackHaul" in methods(store)


async def test_a_cluster_disable_with_no_role_data_is_in_place_and_not_sent():
    ds = ds_with_vpn(GLOBAL, hubs=(CLUSTER,))
    row = service_row(CLUSTER, "hub")
    row["data"] = {}
    state = await profile_of([ds, ds_entry(GUEST)], row)
    await edit(state, "hub", remove_hub(CLUSTER.name), hub_list=False)
    pipeline, store = hub_pipeline(state, vpn_check=True)

    result = await pipeline.save(state)

    assert calls(store) == []
    assert [o.status for o in result.steps] == ["in_place"]
    assert stored_rows(store, CLUSTER_HUB_REF) == []


async def test_an_unpredicted_cluster_failure_is_a_save_step_error():
    state = await state_before("hub", CLUSTER, "disable")
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.fail_calls.add("enterprise/disableClusterForEdgeHub")

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    error = failed.value
    assert error.step is not None and error.step.hub.name == CLUSTER.name
    assert (error.module_name, error.steps_done, error.written) == (None, (), ())
    assert isinstance(error.__cause__, VcoError)
    assert "simulated failure" in str(error.__cause__)
    progress = save_progress(error)
    assert progress is not None
    assert (progress.completed, progress.failed) == ((), None)
    assert progress.pending == (DS,)
    message = str(error)
    assert "disable hub for cluster Cluster-1 on segment Global Segment" in message
    assert "Nothing is undone" in message and "Save again" in message
    assert UPDATE not in methods(store)
    # The state keeps the edits: a second save sends it.
    result = await pipeline.save(state)
    assert [o.status for o in result.steps] == ["sent"]


# -- a failure after a step ---------------------------------------------------


async def test_a_module_write_after_a_step_fails_as_a_save_step_error():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.fail_writes.add(1)

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    error = failed.value
    assert error.module_name == DS and error.step is None
    assert [s.role for s in error.steps_done] == ["hub", "backhaul"]
    assert error.steps_in_place == () and error.written == ()
    assert isinstance(error.__cause__, VcoError)
    progress = save_progress(error)
    assert progress is not None
    assert (progress.completed, progress.failed) == ((), DS)
    assert progress.pending == ()
    assert "the write of the module deviceSettings" in str(error)
    assert state.plan().changed  # the state keeps the edits

    store.requests.clear()
    result = await pipeline.save(state)

    assert calls(store) == []
    assert [o.status for o in result.steps] == ["in_place", "in_place"]
    assert methods(store).count(UPDATE) == 1
    assert state.plan().changed == ()


async def test_a_conflict_of_the_module_write_after_a_step_is_the_cause():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.conditional_check = True
    original = pipeline.client.get_enterprise_services
    reads = 0

    def elsewhere(row: dict[str, Any]) -> None:
        row["data"]["future"] = 2

    async def other_writer_after_the_steps(request: Any) -> Any:
        nonlocal reads
        reads += 1
        if reads == 3:  # the first read after the steps
            store.other_writer(DS_ID, elsewhere)
        return await original(request)

    pipeline.client.get_enterprise_services = other_writer_after_the_steps  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    error = failed.value
    assert isinstance(error.__cause__, ConfigConflictError)
    assert error.module_name == DS
    assert [s.role for s in error.steps_done] == ["hub", "backhaul"]
    assert error.__cause__.lost_edits  # the list edits the other writer's version lost
    assert methods(store).count(VERSION) == 1  # the write did not read it again


async def test_a_module_write_that_fails_with_no_step_sent_propagates_unchanged():
    # Hub-A's service has the hub role already, so the one step is in place.
    ds = ds_with_vpn(GLOBAL, enabled=True, hub_list_enabled=False)
    state = await profile_of(
        [ds, ds_entry(GUEST)], service_row(HUB_A, "hub", segment=GLOBAL)
    )
    await edit(state, "hub", add_hub(HUB_A.name), hub_list=True)
    pipeline, store = hub_pipeline(state)
    store.fail_writes.add(1)

    with pytest.raises(VcoError) as failed:
        _ = await pipeline.save(state)

    assert not isinstance(failed.value, SaveStepError)
    assert calls(store) == []


async def test_a_stale_version_stops_before_the_first_step():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)

    def elsewhere(row: dict[str, Any]) -> None:
        row["data"]["future"] = 2

    store.other_writer(DS_ID, elsewhere)

    with pytest.raises(ConfigConflictError) as failed:
        _ = await pipeline.save(state)

    assert (failed.value.stated, failed.value.sent) == (False, False)
    assert methods(store) == [VERSION, CONFIGURATION]
    assert services_of(store, "edgeHub") == []


async def test_a_failure_of_the_services_read_before_any_step_propagates_unchanged():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.fail_calls.add(SERVICES)

    with pytest.raises(VcoError) as failed:
        _ = await pipeline.save(state)

    assert not isinstance(failed.value, SaveStepError)
    assert calls(store) == []
    assert save_progress(failed.value) is None


async def test_a_failure_of_the_second_services_read_is_a_save_step_error():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    original = pipeline.client.get_enterprise_services
    reads = 0

    async def second_read_fails(request: Any) -> Any:
        nonlocal reads
        reads += 1
        if reads == 3:
            raise VcoError("the VCO is gone")
        return await original(request)

    pipeline.client.get_enterprise_services = second_read_fails  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    assert failed.value.step is None and failed.value.module_name is None
    assert [s.role for s in failed.value.steps_done] == ["hub", "backhaul"]
    assert "after its steps" in str(failed.value)
    assert UPDATE not in methods(store)


async def test_a_cancellation_of_a_step_propagates_unchanged():
    state = await two_lists_state()
    pipeline, _ = hub_pipeline(state, vpn_check=True)
    cancelled = asyncio.CancelledError()

    async def cancelled_call(request: Any) -> Any:
        raise cancelled

    pipeline.client.enable_edge_hub = cancelled_call  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(asyncio.CancelledError) as raised:
        _ = await pipeline.save(state)

    assert raised.value is cancelled
    progress = save_progress(cancelled)
    assert progress is not None and progress.pending == (DS,)


def test_a_keyboard_interrupt_of_a_step_propagates_unchanged():
    interrupt = KeyboardInterrupt()

    async def scenario() -> None:
        state = await two_lists_state()
        pipeline, _ = hub_pipeline(state, vpn_check=True)

        async def interrupted_call(request: Any) -> Any:
            raise interrupt

        pipeline.client.enable_edge_hub = interrupted_call  # pyright: ignore[reportAttributeAccessIssue]
        _ = await pipeline.save(state)

    with pytest.raises(KeyboardInterrupt) as raised:
        asyncio.run(scenario())

    assert raised.value is interrupt


# -- a write whose answer was lost ----------------------------------------------


async def test_a_module_the_version_read_finds_stored_is_not_written_again():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.lose_answers.add(1)

    with pytest.raises(SaveStepError) as lost:
        _ = await pipeline.save(state)
    assert isinstance(lost.value.__cause__, httpx.ReadTimeout)
    stored_version = store.rows[DS_ID]["version"]
    store.requests.clear()

    result = await pipeline.save(state)

    assert UPDATE not in methods(store)
    assert methods(store)[:2] == [VERSION, CONFIGURATION]
    assert [w.name for w in result.written] == [DS]
    assert result.written[0].module.version == stored_version
    assert [o.status for o in result.steps] == ["in_place", "in_place"]
    assert state.plan().changed == ()


async def test_a_stored_write_the_version_read_finds_counts_as_done_before_the_steps():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.lose_answers.add(1)
    with pytest.raises(SaveStepError):
        _ = await pipeline.save(state)
    original = pipeline.client.get_enterprise_services
    reads = 0

    async def third_read_fails(request: Any) -> Any:
        nonlocal reads
        reads += 1
        if reads == 3:  # the first read after the steps
            raise VcoError("the VCO is gone")
        return await original(request)

    pipeline.client.get_enterprise_services = third_read_fails  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(VcoError) as failed:
        _ = await pipeline.save(state)

    # Every step was in place, so none was sent: the error is the VCO's own.
    assert not isinstance(failed.value, SaveStepError)
    progress = save_progress(failed.value)
    assert progress is not None
    assert [w.name for w in progress.completed] == [DS]
    assert progress.pending == ()
    # The state already holds the module as stored, and plans nothing.
    assert state.module(DS).version == store.rows[DS_ID]["version"]
    assert state.plan().changed == ()


async def test_a_stored_write_found_before_a_failed_first_services_read_is_in_the_progress():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)
    store.lose_answers.add(1)
    with pytest.raises(SaveStepError):
        _ = await pipeline.save(state)
    store.fail_calls.add(SERVICES)

    with pytest.raises(VcoError) as failed:
        _ = await pipeline.save(state)

    assert not isinstance(failed.value, SaveStepError)
    progress = save_progress(failed.value)
    assert progress is not None
    assert [w.name for w in progress.completed] == [DS]
    assert state.plan().changed == ()


async def test_a_hub_row_the_vco_leaves_out_of_the_stored_module_is_refused():
    state = await two_lists_state()
    pipeline, store = hub_pipeline(state, vpn_check=True)

    def drop_the_hub_row(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        new["refs"].pop(EDGE_HUB_REF)

    store.on_store.append(drop_the_hub_row)

    with pytest.raises(SaveStepError) as failed:
        _ = await pipeline.save(state)

    error = failed.value
    assert isinstance(error.__cause__, RefsDroppedError)
    assert error.module_name == DS
    assert [s.role for s in error.steps_done] == ["hub", "backhaul"]
    assert EDGE_HUB_REF in str(error.__cause__)
    assert [
        str(c.path) for c in error.__cause__.lost_edits if EDGE_HUB_REF in str(c.path)
    ]


# -- the rows ------------------------------------------------------------------


async def test_a_row_the_refs_as_read_already_hold_is_not_added_twice():
    # Hub-A's service has the hub role and a row, but the list does not hold it.
    ds = ds_with_vpn(GLOBAL, enabled=True, hub_list_enabled=False)
    state = await profile_of(
        [ds, ds_entry(GUEST)], service_row(HUB_A, "hub", segment=GLOBAL)
    )
    await edit(state, "hub", add_hub(HUB_A.name), hub_list=True)
    pipeline, store = hub_pipeline(state)
    rows_as_read = copy.deepcopy(stored_rows(store, EDGE_HUB_REF))

    result = await pipeline.save(state)

    assert [o.status for o in result.steps] == ["in_place"]
    assert sent_refs(store)[EDGE_HUB_REF] == rows_as_read[0]
    assert len(stored_rows(store, EDGE_HUB_REF)) == 1


async def test_a_plan_without_steps_saves_as_before():
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    state.profile.device_settings.segments[0].vpn.conditional_backhaul = True
    pipeline, store = hub_pipeline(state, vpn_check=True)

    result = await pipeline.save(state)

    assert SERVICES not in methods(store)
    assert methods(store).count(VERSION) == 1
    assert result.steps == ()
    assert [w.name for w in result.written] == [DS]


def rows_of_ref(held: Any) -> list[dict[str, Any]]:
    """The rows of one ref name in a module's refs, which hold one row bare and several in a list."""
    return held if isinstance(held, list) else [held]


async def test_a_rebased_qos_write_keeps_its_backhaul_row():
    state = await a_profile()
    state.profile.device_settings.segments[0].vpn.conditional_backhaul = True
    use_hub(state.profile.qos, HUB_A, SERVICE_A)
    pipeline, store = hub_pipeline(state)
    done: list[bool] = []

    def another_writer(new: dict[str, Any], old: dict[str, Any] | None) -> None:
        """After the device settings write, another writer changes a key of QoS nobody here edits."""
        del old
        if new["name"] != DS or done:
            return
        done.append(True)

        def rate_enabled(row: dict[str, Any]) -> None:
            row["data"]["serviceRateLimit"] = {
                "enabled": True,
                "inputType": "percent",
                "value": 1,
            }

        store.other_writer(QOS_ID, rate_enabled)

    store.on_store.append(another_writer)

    result = await pipeline.save(state)

    assert [w.name for w in result.written] == [DS, "QOS"]
    (qos,) = [w for w in result.written if w.name == "QOS"]
    assert qos.server_changes
    assert all(c.merged for c in qos.server_changes)
    # The rebased write sent the row once, with the data the other writer left.
    held = store.rows[QOS_ID]
    assert held["data"]["serviceRateLimit"]["enabled"] is True
    (row,) = rows_of_ref(held["refs"][BACKHAUL_REF])
    assert row["enterpriseObjectId"] == SERVICE_A_ID
    assert row["segmentObjectId"] == GLOBAL[3]
    (update,) = [p for m, p in store.requests if m == UPDATE and p["id"] == QOS_ID]
    sent = rows_of_ref(update["_update"]["refs"][BACKHAUL_REF])
    assert [r["enterpriseObjectId"] for r in sent] == [SERVICE_A_ID]
    assert state.plan().changed == ()


# -- the pieces ---------------------------------------------------------------


def ds_data_with(*hubs: Hub) -> DeviceSettingsData:
    """The device settings as edited, whose Global Segment lists *hubs* in its hub list."""
    return DeviceSettingsData.model_validate(
        {
            "lan": {"networks": [], "interfaces": []},
            "segments": [ds_with_vpn(GLOBAL, hubs=hubs)],
        },
        context={"config_level": ConfigContext.PROFILE},
    )


def edge_service(
    service_id: int, hub: Hub, *, roles: tuple[bool, bool, bool] = (True, False, False)
) -> EdgeHubService:
    return EdgeHubService.model_validate(
        {
            "id": service_id,
            "logicalId": str(uuid.UUID(int=service_id)),
            "name": hub.name,
            "type": "edgeHub",
            "created": "2024-01-01T00:00:00.000Z",
            "edgeId": hub.id,
            "data": {
                "logicalId": hub.logical_id,
                "roles": dict(
                    zip(("edgeHub", "edgeToEdgeBridge", "backHaulEdge"), roles)
                ),
            },
            "configuration": [
                {"id": CONFIG, "moduleId": DS_ID, "segmentObjectId": GLOBAL[3]}
            ],
        }
    )


def test_two_services_for_one_hub_and_segment_are_a_hub_list_error():
    services = HubServices(
        edge_hubs=(edge_service(601, HUB_A), edge_service(602, HUB_A))
    )
    with pytest.raises(HubListError) as error:
        _ = services.edge_service(uuid.UUID(HUB_A.logical_id), CONFIG, DS_ID, GLOBAL[3])
    assert "601, 602" in str(error.value)
    assert (
        services.edge_service(uuid.UUID(HUB_A.logical_id), CONFIG, DS_ID, 999) is None
    )
    assert (
        services.edge_service(uuid.UUID(HUB_B.logical_id), CONFIG, DS_ID, GLOBAL[3])
        is None
    )


def test_a_service_with_no_configuration_row_for_the_module_is_not_the_hubs():
    services = HubServices(edge_hubs=(edge_service(601, HUB_A),))
    assert (
        services.edge_service(uuid.UUID(HUB_A.logical_id), CONFIG, DS_ID + 1, GLOBAL[3])
        is None
    )
    assert (
        services.edge_service(uuid.UUID(HUB_A.logical_id), CONFIG + 1, DS_ID, GLOBAL[3])
        is None
    )


def cluster_service(data: dict[str, Any]) -> EdgeHubClusterService:
    return EdgeHubClusterService.model_validate(
        {
            "id": CLUSTER.id,
            "logicalId": CLUSTER.logical_id,
            "name": CLUSTER.name,
            "type": "edgeHubCluster",
            "created": "2024-01-01T00:00:00.000Z",
            "data": data,
        }
    )


def test_cluster_roles_are_those_of_the_profile_and_segment():
    roles = {"edgeHub": True, "edgeToEdgeBridge": False, "backHaulEdge": True}
    cluster = cluster_service(
        {str(CONFIG): {str(GLOBAL[0]): {"roles": roles}}, "autoRebalance": True}
    )
    held = HubServices.cluster_roles(cluster, CONFIG, GLOBAL[0])
    assert held is not None
    assert (held.edge_hub, held.edge_to_edge_bridge, held.back_haul_edge) == (
        True,
        False,
        True,
    )
    assert HubServices.cluster_roles(cluster, CONFIG, GUEST[0]) is None
    assert HubServices.cluster_roles(cluster, CONFIG + 1, GLOBAL[0]) is None
    assert HubServices.cluster_roles(cluster_service({}), CONFIG, GLOBAL[0]) is None


def test_a_hub_found_by_its_logical_id():
    services = HubServices(clusters=(cluster_service({}),))
    assert services.cluster(uuid.UUID(CLUSTER.logical_id)) is not None
    assert services.cluster(uuid.UUID(HUB_A.logical_id)) is None


@pytest.mark.parametrize("qos_usage", [False, True])
async def test_the_services_read_asks_for_the_qos_usage_only_when_told_to(
    qos_usage: bool,
):
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    pipeline, store = hub_pipeline(state)

    services = await read_services(pipeline.client, 7, qos_usage=qos_usage)

    reads = [p for m, p in store.requests if m == SERVICES]
    assert [p["type"] for p in reads] == ["edgeHub", "edgeHubCluster"]
    assert reads[0]["with"] == ["configuration", *(["edgeQOSUsage"] * qos_usage)]
    assert "with" not in reads[1]
    assert all(p["enterpriseId"] == 7 for p in reads)
    assert (services.edge_hubs, [c.name for c in services.clusters]) == (
        (),
        [CLUSTER.name],
    )


def test_an_enable_of_a_hub_whose_role_is_set_is_in_place():
    services = HubServices(
        edge_hubs=(edge_service(601, HUB_A, roles=(True, False, True)),)
    )
    for role, expected in (("hub", True), ("bridge", False), ("backhaul", True)):
        step = step_of("enable", role, HUB_A, added=True)  # pyright: ignore[reportArgumentType]
        assert (
            in_place(step, services, configuration_id=CONFIG, module_id=DS_ID)
            is expected
        )


def test_an_edge_disable_is_in_place_only_with_no_service():
    services = HubServices(
        edge_hubs=(edge_service(601, HUB_A, roles=(True, False, False)),)
    )
    own = step_of("disable", "backhaul", HUB_A, added=False)
    other = step_of("disable", "backhaul", HUB_B, added=False)
    assert not in_place(own, services, configuration_id=CONFIG, module_id=DS_ID)
    assert in_place(other, services, configuration_id=CONFIG, module_id=DS_ID)


def test_a_cluster_disable_is_in_place_when_its_role_is_off_or_it_has_no_data():
    roles = {"edgeHub": True, "edgeToEdgeBridge": False, "backHaulEdge": True}
    with_roles = HubServices(
        clusters=(cluster_service({str(CONFIG): {str(GLOBAL[0]): {"roles": roles}}}),)
    )
    without = HubServices(clusters=(cluster_service({}),))
    for role, on in (("hub", True), ("bridge", False), ("backhaul", True)):
        step = step_of("disable", role, CLUSTER, added=False)  # pyright: ignore[reportArgumentType]
        assert (
            in_place(step, with_roles, configuration_id=CONFIG, module_id=DS_ID)
            is not on
        )
        assert in_place(step, without, configuration_id=CONFIG, module_id=DS_ID)
    nothing = HubServices()
    assert in_place(
        step_of("disable", "hub", CLUSTER, added=False),
        nothing,
        configuration_id=CONFIG,
        module_id=DS_ID,
    )


async def test_a_step_with_no_ids_to_call_with_is_refused():
    state = await profile_of([ds_entry(GLOBAL), ds_entry(GUEST)])
    pipeline, store = hub_pipeline(state)
    with pytest.raises(ValueError, match="no Edge ID to call the VCO with"):
        await send_step(
            pipeline.client,
            step_of("enable", "hub", HUB_A, added=False),
            HubServices(),
            configuration_id=CONFIG,
            module_id=DS_ID,
            enterprise_id=None,
        )
    with pytest.raises(HubServiceNotFoundError):
        await send_step(
            pipeline.client,
            step_of("disable", "hub", HUB_A, added=False),
            HubServices(),
            configuration_id=CONFIG,
            module_id=DS_ID,
            enterprise_id=None,
        )
    assert calls(store) == []


def test_a_hub_enabled_with_no_service_afterwards_is_refused():
    with pytest.raises(HubServiceNotFoundError):
        _ = hub_rows(
            {},
            [step_of("enable", "hub", HUB_A, added=True)],
            HubServices(),
            configuration_id=CONFIG,
            module_id=DS_ID,
            now=ds_data_with(HUB_A),
        )


def test_the_as_read_rows_of_hubs_that_left_the_hub_list_are_the_rows_removed():
    as_read = refs(service_row(HUB_A, "hub"), service_row(CLUSTER, "hub"))
    steps = [
        step_of("disable", "hub", HUB_A, added=False),
        step_of("disable", "hub", CLUSTER, added=False),
    ]

    added, removed = hub_rows(
        as_read,
        steps,
        HubServices(),
        configuration_id=CONFIG,
        module_id=DS_ID,
        now=ds_data_with(),
    )

    assert added == []
    assert removed == [as_read[EDGE_HUB_REF], as_read[CLUSTER_HUB_REF]]


def test_the_row_of_a_hub_that_stays_in_the_hub_list_is_never_removed():
    as_read = refs(service_row(HUB_A, "hub"))

    _, removed = hub_rows(
        as_read,
        [step_of("disable", "hub", HUB_A, added=False)],
        HubServices(),
        configuration_id=CONFIG,
        module_id=DS_ID,
        now=ds_data_with(HUB_A),
    )

    assert removed == []


def test_a_bridge_or_backhaul_step_makes_no_row():
    as_read = refs(service_row(HUB_A, "hub", "backhaul"))
    steps = [
        step_of("disable", "backhaul", HUB_A, added=False),
        step_of("enable", "bridge", HUB_A, added=True),
    ]

    assert hub_rows(
        as_read,
        steps,
        HubServices(edge_hubs=(edge_service(601, HUB_A),)),
        configuration_id=CONFIG,
        module_id=DS_ID,
        now=ds_data_with(HUB_A),
    ) == ([], [])


def test_the_row_of_an_added_hub_is_the_five_fields_of_its_service():
    added, removed = hub_rows(
        refs(),
        [
            step_of("enable", "hub", HUB_A, added=True),
            step_of("enable", "hub", CLUSTER, added=True),
        ],
        HubServices(
            edge_hubs=(edge_service(601, HUB_A),), clusters=(cluster_service({}),)
        ),
        configuration_id=CONFIG,
        module_id=DS_ID,
        now=ds_data_with(HUB_A, CLUSTER),
    )

    assert removed == []
    assert added == [
        {
            "configurationId": CONFIG,
            "moduleId": DS_ID,
            "enterpriseObjectId": 601,
            "ref": EDGE_HUB_REF,
            "segmentObjectId": GLOBAL[3],
        },
        {
            "configurationId": CONFIG,
            "moduleId": DS_ID,
            "enterpriseObjectId": CLUSTER.id,
            "ref": CLUSTER_HUB_REF,
            "segmentObjectId": GLOBAL[3],
        },
    ]
