"""The hub resolver: the Edges and the hub clusters of an enterprise by name and logical ID.

Each test that loads the resolver serves ``enterprise/getEnterpriseEdgeList``
and ``enterprise/getEnterpriseServices`` from a ``ModuleStore`` through a
``VcoClient``, so the rows are parsed as the real client parses them.
"""

import uuid

import hub_helpers
import pytest
from hub_helpers import CLUSTER, HUB_A, HUB_B, MEMBER, NOT_A_HUB, Hub
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.hub import HubResolver
from pyvelo.core.identity import (
    HubIdentity,
    HubLogicalId,
    HubName,
    ResolutionError,
    ResolverNotLoadedError,
)
from pyvelo.testing import ModuleStore
from pyvelo.testing.fixtures import make_hub_resolver

from ._type_support import MockVcoClientFactory

GET_EDGES = "enterprise/getEnterpriseEdgeList"
GET_SERVICES = "enterprise/getEnterpriseServices"
SHARED = uuid.UUID("f7f7f7f7-f7f7-4f7f-8f7f-f7f7f7f7f7f7")


def store_with_hubs() -> ModuleStore:
    return hub_helpers.hub_store(HUB_A, HUB_B, CLUSTER, MEMBER, NOT_A_HUB)


def edge_identity(hub: Hub) -> HubIdentity:
    logical_id = uuid.UUID(hub.logical_id)
    return HubIdentity(
        hub.name, "edge", "edgeHub", logical_id, hub.id, logical_id, None, None
    )


def cluster_identity(hub: Hub) -> HubIdentity:
    logical_id = uuid.UUID(hub.logical_id)
    return HubIdentity(
        hub.name,
        "cluster",
        "edgeHubCluster",
        logical_id,
        None,
        None,
        hub.id,
        logical_id,
    )


async def loaded(
    mock_vco_client: MockVcoClientFactory, store: ModuleStore
) -> tuple[ResolverRegistry, HubResolver]:
    client = await mock_vco_client(store)
    resolver = HubResolver(client)
    registry = ResolverRegistry()
    registry.register(resolver)
    await resolver.load(ResolverContext(enterprise_id=1))
    return registry, resolver


async def test_a_load_makes_three_requests(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = store_with_hubs()

    _ = await loaded(mock_vco_client, store)

    assert [method for method, _ in store.requests] == [
        GET_EDGES,
        GET_SERVICES,
        GET_SERVICES,
    ]
    assert [p.get("type") for m, p in store.requests if m == GET_SERVICES] == [
        "edgeHubCluster",
        "edgeHubClusterMember",
    ]
    assert {p["enterpriseId"] for _, p in store.requests} == {1}
    assert store.requests[0][1]["limit"] == 200


async def test_an_edge_that_is_no_member_is_an_edge_identity(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry, _ = await loaded(mock_vco_client, store_with_hubs())

    found = registry.lookup(HubIdentity, "Hub-A")

    assert found == edge_identity(HUB_A)
    assert found.logical_id == found.edge_logical_id == uuid.UUID(HUB_A.logical_id)
    assert (found.edge_id, found.type_) == (HUB_A.id, "edgeHub")
    # Any Edge can be a hub, so the Edge of no hub list is one too.
    assert registry.lookup(HubIdentity, NOT_A_HUB.name) == edge_identity(NOT_A_HUB)


async def test_a_cluster_is_a_cluster_identity(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry, _ = await loaded(mock_vco_client, store_with_hubs())

    found = registry.lookup(HubIdentity, "Cluster-1")

    assert found == cluster_identity(CLUSTER)
    assert found.logical_id == found.cluster_logical_id == uuid.UUID(CLUSTER.logical_id)
    assert (found.edge_id, found.edge_logical_id) == (None, None)
    assert (found.cluster_id, found.type_) == (CLUSTER.id, "edgeHubCluster")


async def test_lookups_by_name_logical_id_and_edge_id(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry, _ = await loaded(mock_vco_client, store_with_hubs())
    expected = edge_identity(HUB_B)

    assert registry.lookup(HubName("Hub-B")) == expected
    assert registry.lookup(HubName("hub-b")) == expected
    assert registry.lookup(HubLogicalId(uuid.UUID(HUB_B.logical_id))) == expected
    assert registry.lookup_by(HubIdentity, lambda h: h.edge_id, HUB_B.id) == expected
    assert registry.lookup_by(
        HubIdentity, lambda h: h.cluster_id, CLUSTER.id
    ) == cluster_identity(CLUSTER)
    assert (
        registry.lookup(HubLogicalId(uuid.UUID(CLUSTER.logical_id))).name == "Cluster-1"
    )
    assert (
        registry.lookup_by(HubIdentity, lambda h: h.logical_id, expected.logical_id)
        == expected
    )
    assert sorted(i.name for i in registry.get_resolver(HubResolver).identities()) == [
        "Branch-1",
        "Cluster-1",
        "Hub-A",
        "Hub-B",
    ]


async def test_a_cluster_member_is_no_identity(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry, resolver = await loaded(mock_vco_client, store_with_hubs())
    member = uuid.UUID(MEMBER.logical_id)

    assert resolver.is_cluster_member(member)
    assert resolver.member_name(member) == "Member-1"
    assert not resolver.is_cluster_member(uuid.UUID(HUB_A.logical_id))
    assert resolver.member_name(uuid.UUID(HUB_A.logical_id)) is None
    assert MEMBER.name not in [i.name for i in resolver.identities()]
    with pytest.raises(ResolutionError):
        _ = registry.lookup_by(HubIdentity, lambda h: h.logical_id, member)
    with pytest.raises(ResolutionError, match="member of a hub cluster"):
        _ = registry.lookup(HubIdentity, "Member-1")
    with pytest.raises(ResolutionError, match="member of a hub cluster"):
        _ = registry.lookup(HubLogicalId(member))


async def test_an_unknown_name_lists_the_close_ones(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry, _ = await loaded(mock_vco_client, store_with_hubs())

    with pytest.raises(ResolutionError, match="Hub-A") as caught:
        _ = registry.lookup(HubIdentity, "Hub-C")
    assert "Unknown hub: 'Hub-C'" in str(caught.value)
    with pytest.raises(ResolutionError, match="No Edge or hub cluster"):
        _ = registry.lookup(HubLogicalId(SHARED))


async def test_a_shared_hub_name_asks_for_the_logical_id(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = store_with_hubs()
    store.add_cluster(51, logical_id=str(SHARED), name="Hub-A")
    registry, _ = await loaded(mock_vco_client, store)

    with pytest.raises(ResolutionError) as caught:
        _ = registry.lookup(HubIdentity, "Hub-A")

    message = str(caught.value)
    assert HUB_A.logical_id in message
    assert str(SHARED) in message
    assert "logical ID" in message
    assert sorted(caught.value.candidates) == sorted([HUB_A.logical_id, str(SHARED)])
    by_edge = registry.lookup(HubLogicalId(uuid.UUID(HUB_A.logical_id)))
    by_cluster = registry.lookup(HubLogicalId(SHARED))
    assert (by_edge.kind, by_cluster.kind) == ("edge", "cluster")
    assert sorted(
        i.kind
        for i in registry.get_resolver(HubResolver).identities()
        if i.name == "Hub-A"
    ) == ["cluster", "edge"]


async def test_lookup_by_finds_a_hub_whose_name_another_hub_shares(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    # An Edge and a cluster both named Hub-A: the name index keeps one of them.
    store = store_with_hubs()
    store.add_cluster(51, logical_id=str(SHARED), name="Hub-A")
    registry, _ = await loaded(mock_vco_client, store)

    cluster = registry.lookup_by(HubIdentity, lambda h: h.cluster_id, 51)
    edge = registry.lookup_by(HubIdentity, lambda h: h.edge_id, HUB_A.id)

    assert (cluster.kind, edge.kind) == ("cluster", "edge")
    assert cluster.name == edge.name == "Hub-A"


async def test_before_its_load_the_resolver_says_it_is_not_loaded(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    client = await mock_vco_client(store_with_hubs())
    resolver = HubResolver(client)
    registry = ResolverRegistry()
    registry.register(resolver)

    with pytest.raises(ResolverNotLoadedError) as caught:
        _ = registry.lookup(HubIdentity, "Hub-A")
    assert caught.value.identity_type is HubIdentity
    assert "load_resolvers(state, HubIdentity)" in str(caught.value)
    with pytest.raises(ResolverNotLoadedError):
        _ = registry.lookup_by(HubIdentity, lambda h: h.edge_id, HUB_A.id)
    with pytest.raises(ResolverNotLoadedError):
        _ = resolver.is_cluster_member(uuid.UUID(MEMBER.logical_id))
    assert not resolver.loaded

    await resolver.load(ResolverContext(enterprise_id=1))
    assert registry.lookup(HubIdentity, "Hub-A").edge_id == HUB_A.id


async def test_an_unloaded_copy_holds_nothing_and_loads_on_its_own(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    _, resolver = await loaded(mock_vco_client, store_with_hubs())

    twin = resolver.unloaded()

    assert not twin.loaded
    assert twin.identities() == []
    assert resolver.loaded
    assert len(resolver.identities()) == 4
    await twin.load(ResolverContext(enterprise_id=1))
    assert len(twin.identities()) == 4
    assert twin.is_cluster_member(uuid.UUID(MEMBER.logical_id))


async def test_a_reload_replaces_what_was_loaded(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = store_with_hubs()
    _, resolver = await loaded(mock_vco_client, store)
    del store.edges[HUB_B.id]

    await resolver.load(ResolverContext(enterprise_id=1))

    assert sorted(i.name for i in resolver.identities()) == [
        "Branch-1",
        "Cluster-1",
        "Hub-A",
    ]


async def test_the_fixture_builds_a_loaded_resolver() -> None:
    resolver = await make_hub_resolver(
        [edge_identity(HUB_A), cluster_identity(CLUSTER)],
        members=[(uuid.UUID(MEMBER.logical_id), MEMBER.name)],
    )
    registry = ResolverRegistry()
    registry.register(resolver)

    assert resolver.loaded
    assert registry.lookup(HubIdentity, "Hub-A") == edge_identity(HUB_A)
    assert registry.lookup(HubIdentity, "Cluster-1") == cluster_identity(CLUSTER)
    assert resolver.is_cluster_member(uuid.UUID(MEMBER.logical_id))
    assert len(resolver.identities()) == 2
