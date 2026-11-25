"""A hub named once, or given by its logical ID once, fills its entry.

That is its list entry or rule entry.

``merge_patch`` runs here over a registry that holds a loaded ``HubResolver``.
"""

import uuid
from typing import Any

import pytest
from hub_helpers import CLUSTER, HUB_A, HUB_B, MEMBER, NOT_A_HUB, Hub
from pydantic import MISSING
from pyvelo.client.models.configuration.device_settings.segment import (
    EdgeToEdgeDetail,
    EdgeToEdgeHub,
)
from pyvelo.client.models.configuration.qos.rule import BackhaulEdge, RouteConfig
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import PatchError, add, delete
from pyvelo.core.identity import HubIdentity, ResolutionError, ResolverNotLoadedError
from pyvelo.testing.fixtures import make_hub_resolver


def identity(hub: Hub) -> HubIdentity:
    logical_id = uuid.UUID(hub.logical_id)
    if hub.kind == "cluster":
        return HubIdentity(
            hub.name, "cluster", "edgeHubCluster", logical_id, None, None, hub.id,
            logical_id,
        )  # fmt: skip
    return HubIdentity(
        hub.name, "edge", "edgeHub", logical_id, hub.id, logical_id, None, None
    )


async def hub_registry() -> ResolverRegistry:
    resolver = await make_hub_resolver(
        [identity(h) for h in (HUB_A, HUB_B, CLUSTER, NOT_A_HUB)],
        members=[(uuid.UUID(MEMBER.logical_id), MEMBER.name)],
    )
    registry = ResolverRegistry()
    registry.register(resolver)
    return registry


def hub_list(model: EdgeToEdgeHub) -> list[dict[str, Any]]:
    return [
        {"name": h.name, "type": h.type_, "logicalId": str(h.logical_id)}
        for h in model.vpn_hubs
    ]


def entry(hub: Hub) -> dict[str, Any]:
    return {"name": hub.name, "type": hub.type_, "logicalId": hub.logical_id}


async def test_an_edge_named_once_fills_its_type_and_logical_id() -> None:
    model = EdgeToEdgeHub()

    _ = model.merge_patch(
        {"vpnHubs": {"+": add({"name": "Hub-A"})}}, resolvers=await hub_registry()
    )

    assert hub_list(model) == [entry(HUB_A)]


async def test_a_cluster_named_once_fills_its_type_and_logical_id() -> None:
    model = EdgeToEdgeHub()

    _ = model.merge_patch(
        {"vpnHubs": {"+": add({"name": "Cluster-1"})}}, resolvers=await hub_registry()
    )

    assert hub_list(model) == [entry(CLUSTER)]
    assert model.vpn_hubs[0].type_ == "edgeHubCluster"


async def test_an_edge_given_by_logical_id_takes_its_name() -> None:
    model = EdgeToEdgeHub()

    _ = model.merge_patch(
        {"vpnHubs": {"+": add({"logicalId": HUB_A.logical_id})}},
        resolvers=await hub_registry(),
    )

    assert hub_list(model) == [entry(HUB_A)]


async def test_a_cluster_given_by_logical_id_takes_its_name_and_type() -> None:
    model = EdgeToEdgeHub()

    _ = model.merge_patch(
        {"vpnHubs": {"+": add({"logicalId": CLUSTER.logical_id})}},
        resolvers=await hub_registry(),
    )

    assert hub_list(model) == [entry(CLUSTER)]


async def test_an_unknown_logical_id_raises_resolution_error() -> None:
    model = EdgeToEdgeHub()
    unknown = "99999999-9999-4999-8999-999999999999"

    with pytest.raises(ResolutionError, match=unknown):
        _ = model.merge_patch(
            {"vpnHubs": {"+": add({"logicalId": unknown})}},
            resolvers=await hub_registry(),
        )

    assert model.vpn_hubs == []


async def test_a_cluster_members_logical_id_is_refused() -> None:
    model = EdgeToEdgeHub()

    with pytest.raises(ResolutionError, match=MEMBER.logical_id):
        _ = model.merge_patch(
            {"vpnHubs": {"+": add({"logicalId": MEMBER.logical_id})}},
            resolvers=await hub_registry(),
        )


async def test_a_body_giving_the_type_keeps_it() -> None:
    model = EdgeToEdgeHub()

    _ = model.merge_patch(
        {"vpnHubs": {"+": add({"name": "Hub-A", "type": "edgeHub"})}},
        resolvers=await hub_registry(),
    )

    assert hub_list(model) == [entry(HUB_A)]


async def test_a_body_giving_the_cluster_type_keeps_it() -> None:
    model = EdgeToEdgeHub()

    _ = model.merge_patch(
        {
            "vpnHubs": {
                "+": add(
                    {
                        "name": "Cluster-1",
                        "type": "edgeHubCluster",
                        "logicalId": CLUSTER.logical_id,
                    }
                )
            }
        },
        resolvers=await hub_registry(),
    )

    assert hub_list(model) == [entry(CLUSTER)]


async def test_a_shared_name_given_with_its_logical_id_adds_that_hub() -> None:
    resolver = await make_hub_resolver(
        [
            identity(HUB_A),
            identity(Hub("cluster", 51, CLUSTER.logical_id, "Hub-A")),
        ]
    )
    registry = ResolverRegistry()
    registry.register(resolver)
    model = EdgeToEdgeHub()

    with pytest.raises(ResolutionError, match="Several hubs are named"):
        _ = model.merge_patch(
            {"vpnHubs": {"+": add({"name": "Hub-A"})}}, resolvers=registry
        )
    _ = model.merge_patch(
        {"vpnHubs": {"+": add({"logicalId": CLUSTER.logical_id})}}, resolvers=registry
    )

    assert hub_list(model) == [
        {"name": "Hub-A", "type": "edgeHubCluster", "logicalId": CLUSTER.logical_id}
    ]


async def test_a_selector_by_label_deletes_the_entry() -> None:
    model = EdgeToEdgeHub.model_validate(
        {"vpnHubs": [entry(HUB_A), entry(HUB_B)], "enabled": True}
    )

    _ = model.merge_patch(
        {"vpnHubs": {"Hub-A": delete()}}, resolvers=await hub_registry()
    )

    assert hub_list(model) == [entry(HUB_B)]


async def test_a_hub_already_in_the_list_is_refused_under_its_name_and_its_id() -> None:
    model = EdgeToEdgeHub.model_validate({"vpnHubs": [entry(HUB_A)]})
    registry = await hub_registry()

    with pytest.raises(PatchError, match="already in the list"):
        _ = model.merge_patch(
            {"vpnHubs": {"+": add({"name": "Hub-A"})}}, resolvers=registry
        )
    with pytest.raises(PatchError, match="already in the list"):
        _ = model.merge_patch(
            {"vpnHubs": {"+": add({"logicalId": HUB_A.logical_id})}},
            resolvers=registry,
        )

    assert hub_list(model) == [entry(HUB_A)]


async def test_a_hub_is_added_to_the_bridge_list_the_same_way() -> None:
    model = EdgeToEdgeDetail()

    _ = model.merge_patch(
        {"vpnHubs": {"+": add({"name": "Hub-B"}), "+2": add({"name": "Cluster-1"})}},
        resolvers=await hub_registry(),
    )

    assert [(h.name, h.type_) for h in model.vpn_hubs] == [
        ("Hub-B", "edgeHub"),
        ("Cluster-1", "edgeHubCluster"),
    ]


async def test_an_unknown_name_raises_resolution_error() -> None:
    model = EdgeToEdgeHub()

    with pytest.raises(ResolutionError, match="Hub-Z"):
        _ = model.merge_patch(
            {"vpnHubs": {"+": add({"name": "Hub-Z"})}}, resolvers=await hub_registry()
        )


async def test_a_name_with_a_resolver_not_loaded_asks_for_the_load() -> None:
    resolver = (await make_hub_resolver([identity(HUB_A)])).unloaded()
    registry = ResolverRegistry()
    registry.register(resolver)

    with pytest.raises(ResolverNotLoadedError):
        _ = EdgeToEdgeHub().merge_patch(
            {"vpnHubs": {"+": add({"name": "Hub-A"})}}, resolvers=registry
        )
    # The type is a value of its field, not a name to look up.
    model = EdgeToEdgeHub()
    _ = model.merge_patch(
        {
            "vpnHubs": {
                "+": add(
                    {
                        "name": "Hub-A",
                        "type": "edgeHub",
                        "logicalId": HUB_A.logical_id,
                    }
                )
            }
        },
        resolvers=registry,
    )
    assert hub_list(model) == [entry(HUB_A)]


# -- QoS rule entries --------------------------------------------------------


def backhaul_edges(route: RouteConfig) -> list[BackhaulEdge]:
    edges = route.backhaul_edges
    return [] if edges is MISSING else edges


def backhaul(route: RouteConfig) -> list[dict[str, Any]]:
    return [
        {
            "type": e.type_,
            "edgeLogicalId": str(e.edge_logical_id),
            "edgeId": e.edge_id,
            "name": e.edge_name,
            "logicalId": e.logical_id,
        }
        for e in backhaul_edges(route)
    ]


async def test_a_backhaul_hub_named_once_fills_its_edge_ids_and_type() -> None:
    route = RouteConfig()

    _ = route.merge_patch(
        {"backhaulEdges": {"+": add({"name": "Hub-A"})}}, resolvers=await hub_registry()
    )

    assert backhaul(route) == [
        {
            "type": "edgeHub",
            "edgeLogicalId": HUB_A.logical_id,
            "edgeId": HUB_A.id,
            "name": "Hub-A",
            "logicalId": MISSING,
        }
    ]


async def test_a_backhaul_cluster_named_once_fills_a_null_edge_id() -> None:
    route = RouteConfig()

    _ = route.merge_patch(
        {"backhaulEdges": {"+": add({"name": "Cluster-1"})}},
        resolvers=await hub_registry(),
    )

    assert backhaul(route) == [
        {
            "type": "edgeHubCluster",
            "edgeLogicalId": CLUSTER.logical_id,
            "edgeId": None,
            "name": "Cluster-1",
            "logicalId": MISSING,
        }
    ]


async def test_a_backhaul_hub_given_by_logical_id_takes_its_name_and_ids() -> None:
    route = RouteConfig()

    _ = route.merge_patch(
        {"backhaulEdges": {"+": add({"edgeLogicalId": HUB_B.logical_id})}},
        resolvers=await hub_registry(),
    )

    assert backhaul(route) == [
        {
            "type": "edgeHub",
            "edgeLogicalId": HUB_B.logical_id,
            "edgeId": HUB_B.id,
            "name": "Hub-B",
            "logicalId": MISSING,
        }
    ]


async def test_a_backhaul_body_giving_the_type_keeps_it() -> None:
    route = RouteConfig()

    _ = route.merge_patch(
        {"backhaulEdges": {"+": add({"name": "Hub-A", "type": "edgeHub"})}},
        resolvers=await hub_registry(),
    )

    assert [e.type_ for e in backhaul_edges(route)] == ["edgeHub"]


async def test_an_unknown_backhaul_name_raises_resolution_error() -> None:
    with pytest.raises(ResolutionError, match="Hub-Z"):
        _ = RouteConfig().merge_patch(
            {"backhaulEdges": {"+": add({"name": "Hub-Z"})}},
            resolvers=await hub_registry(),
        )
