"""A hub named in a patch is looked up in the enterprise's Edges and clusters, loaded once on first use."""

from typing import Any

import hub_helpers
from hub_helpers import CLUSTER, HUB_A, HUB_B, MEMBER, NOT_A_HUB, Hub
from profile_state import (  # pyright: ignore[reportImplicitRelativeImport]
    module_store_pipeline,
    profile_state,
)
from pyvelo.config import HubIdentity as ExportedHubIdentity
from pyvelo.config.pipeline import ConfigPipeline
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.config.state import ConfigState
from pyvelo.core.identity import HubIdentity
from pyvelo.testing import ModuleStore
from segment_helpers import GLOBAL, firewall_entry, qos_entry

GET_EDGES = "enterprise/getEnterpriseEdgeList"
GET_SERVICES = "enterprise/getEnterpriseServices"
HUB_READS = [GET_EDGES, GET_SERVICES, GET_SERVICES]


def add_hub(name: str) -> dict[str, Any]:
    return {
        "segments": {
            GLOBAL[1]: {
                "vpn": {
                    "edgeToEdgeHub": {
                        "vpnHubs": {"+": {"_op": "add", "name": name}},
                    }
                }
            }
        }
    }


def add_hub_by_id(logical_id: str) -> dict[str, Any]:
    return {
        "segments": {
            GLOBAL[1]: {
                "vpn": {
                    "edgeToEdgeHub": {
                        "vpnHubs": {"+": {"_op": "add", "logicalId": logical_id}},
                    }
                }
            }
        }
    }


def hubs_of(state: ConfigState) -> list[tuple[str, str, str]]:
    (segment,) = state.profile.device_settings.segments
    return [
        (h.name, h.type_, str(h.logical_id))
        for h in segment.vpn.hub_edges.vpn_hubs  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
    ]


def hold(store: ModuleStore, *hubs: Hub) -> None:
    for hub in hubs:
        if hub.kind == "cluster":
            store.add_cluster(hub.id, logical_id=hub.logical_id, name=hub.name)
        else:
            store.add_edge(
                hub.id,
                logical_id=hub.logical_id,
                name=hub.name,
                cluster=CLUSTER.id if hub == MEMBER else None,
            )


async def hub_pipeline(
    *hubs: Hub,
) -> tuple[ConfigPipeline, ModuleStore, ConfigState]:
    state = await profile_state(
        [hub_helpers.ds_with_vpn(GLOBAL)], [qos_entry(GLOBAL)], [firewall_entry(GLOBAL)]
    )
    pipeline, store = module_store_pipeline(state)
    hold(store, *hubs)
    await pipeline.load_resolvers(state)
    return pipeline, store, state


def test_hub_identity_is_exported_by_the_package():
    assert ExportedHubIdentity is HubIdentity


async def test_a_state_gets_an_unloaded_hub_resolver_and_a_load_reads_no_hubs():
    _, store, state = await hub_pipeline(HUB_A)

    resolver = state.resolvers.get_resolver(HubResolver)

    assert not resolver.loaded
    assert not {GET_EDGES} & {m for m, _ in store.requests}
    assert GET_SERVICES not in [m for m, _ in store.requests]


async def test_apply_of_a_hub_by_name_loads_the_resolver_once():
    pipeline, store, state = await hub_pipeline(HUB_A, HUB_B, CLUSTER, MEMBER)
    store.requests.clear()

    _ = await pipeline.apply(state, module="device_settings", patch=add_hub("Hub-A"))

    assert [m for m, _ in store.requests] == HUB_READS
    assert state.resolvers.get_resolver(HubResolver).loaded
    assert hubs_of(state) == [("Hub-A", "edgeHub", HUB_A.logical_id)]

    store.requests.clear()
    _ = await pipeline.apply(
        state, module="device_settings", patch=add_hub("Cluster-1")
    )

    assert store.requests == []
    assert hubs_of(state) == [
        ("Hub-A", "edgeHub", HUB_A.logical_id),
        ("Cluster-1", "edgeHubCluster", CLUSTER.logical_id),
    ]


async def test_apply_of_a_hub_by_logical_id_loads_the_resolver_and_takes_the_name():
    pipeline, store, state = await hub_pipeline(HUB_A, HUB_B, CLUSTER, MEMBER)
    store.requests.clear()

    _ = await pipeline.apply(
        state, module="device_settings", patch=add_hub_by_id(HUB_B.logical_id)
    )

    assert [m for m, _ in store.requests] == HUB_READS
    assert hubs_of(state) == [("Hub-B", "edgeHub", HUB_B.logical_id)]


async def test_load_resolvers_loads_the_hub_resolver_when_asked_to():
    pipeline, store, state = await hub_pipeline(HUB_A, NOT_A_HUB)
    store.requests.clear()

    await pipeline.load_resolvers(state, HubIdentity)

    assert [m for m, _ in store.requests] == HUB_READS
    resolver = state.resolvers.get_resolver(HubResolver)
    assert resolver.loaded
    assert sorted(i.name for i in resolver.identities()) == ["Branch-1", "Hub-A"]


async def test_a_state_with_no_hub_resolver_is_given_one_to_load():
    pipeline, _, state = await hub_pipeline(HUB_A)
    state.resolvers = ResolverRegistry()
    assert state.resolvers.resolver_for(HubIdentity) is None

    await pipeline.load_resolvers(state, HubIdentity)

    assert state.resolvers.get_resolver(HubResolver).loaded
