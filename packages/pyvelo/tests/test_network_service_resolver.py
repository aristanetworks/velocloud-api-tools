"""The network-service resolver: services of an enterprise by name, logical ID and object ID.

Each test that loads the resolver serves ``enterprise/getEnterpriseServices``
from a ``ModuleStore`` through a ``VcoClient``, so the rows are parsed as the
real client parses them.
"""

import uuid

import pytest
from pyvelo.config.resolver import ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.network_service import NetworkServiceResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.core.identity import (
    NetworkServiceIdentity,
    NetworkServiceLogicalId,
    NetworkServiceName,
    NetworkServiceObjectId,
    ResolutionError,
    ResolverNotLoadedError,
    SegmentIdentity,
)
from pyvelo.testing import ModuleStore, make_segment_resolver
from pyvelo.testing.fixtures import make_network_service_resolver

from ._type_support import MockVcoClientFactory

DNS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
AUTH_A = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CSS_A = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TACACS = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
DUP_DNS = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
DUP_AUTH = "ffffffff-ffff-4fff-8fff-ffffffffffff"
SEGMENT = "99999999-9999-4999-8999-999999999999"


def store_with_services() -> ModuleStore:
    store = ModuleStore()
    store.add_object(701, logical_id=DNS_A, name="dns-a", type_="dns")
    store.add_object(702, logical_id=AUTH_A, name="radius-a", type_="authentication")
    store.add_object(703, logical_id=CSS_A, name="zs-a", type_="cloudSecurityService")
    store.add_object(
        501,
        logical_id=SEGMENT,
        name="Global Segment",
        type_="REGULAR",
        object_="NETWORK_SEGMENT",
    )
    return store


async def loaded_registry(
    mock_vco_client: MockVcoClientFactory, store: ModuleStore
) -> ResolverRegistry:
    client = await mock_vco_client(store)
    resolver = NetworkServiceResolver(client)
    registry = ResolverRegistry()
    registry.register(resolver)
    await resolver.load(ResolverContext(enterprise_id=1))
    return registry


async def test_loads_services_by_name_logical_id_and_object_id(
    mock_vco_client: MockVcoClientFactory,
):
    registry = await loaded_registry(mock_vco_client, store_with_services())
    expected = NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")

    assert registry.resolve_sync(NetworkServiceIdentity, "dns-a") == expected
    assert registry.resolve_sync(NetworkServiceName("dns-a")) == expected
    assert registry.resolve_sync(NetworkServiceObjectId(701)) == expected
    assert registry.resolve_sync(NetworkServiceLogicalId(uuid.UUID(DNS_A))) == expected
    found = registry.identity_sync(
        NetworkServiceIdentity, "logical_id", uuid.UUID(DNS_A)
    )
    assert found.object_id == 701
    assert await registry.reverse(NetworkServiceIdentity, "object_id", 702) == (
        "radius-a"
    )
    assert await registry.reverse(
        NetworkServiceIdentity, "logical_id", uuid.UUID(CSS_A)
    ) == ("zs-a")
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert sorted(i.name for i in resolver.identities()) == [
        "dns-a",
        "radius-a",
        "zs-a",
    ]
    assert registry.has_id_sync(NetworkServiceIdentity, "object_id", 703)


async def test_a_service_of_a_type_the_models_do_not_know_still_loads(
    mock_vco_client: MockVcoClientFactory,
):
    store = store_with_services()
    store.add_object(704, logical_id=TACACS, name="tacacs-a", type_="tacacs")
    registry = await loaded_registry(mock_vco_client, store)

    found = registry.resolve_sync(NetworkServiceIdentity, "tacacs-a")

    assert found == NetworkServiceIdentity("tacacs-a", uuid.UUID(TACACS), 704, "tacacs")
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert "tacacs-a" in [i.name for i in resolver.identities()]


async def test_before_its_load_the_resolver_says_it_is_not_loaded(
    mock_vco_client: MockVcoClientFactory,
):
    client = await mock_vco_client(store_with_services())
    resolver = NetworkServiceResolver(client)
    registry = ResolverRegistry()
    registry.register(resolver)

    with pytest.raises(ResolverNotLoadedError) as caught:
        _ = registry.resolve_sync(NetworkServiceIdentity, "dns-a")
    assert caught.value.identity_type is NetworkServiceIdentity
    assert "load_resolvers(state, NetworkServiceIdentity)" in str(caught.value)
    assert not resolver.loaded
    with pytest.raises(ResolverNotLoadedError):
        _ = registry.resolve_sync(NetworkServiceName("dns-a"))
    with pytest.raises(ResolverNotLoadedError):
        _ = await resolver.reverse_by("object_id", 701)
    with pytest.raises(ResolverNotLoadedError):
        _ = registry.identity_sync(NetworkServiceIdentity, "object_id", 701)

    await resolver.load(ResolverContext(enterprise_id=1))
    assert resolver.loaded
    assert registry.resolve_sync(NetworkServiceIdentity, "dns-a").object_id == 701


def test_the_not_loaded_message_names_the_type_and_the_label():
    message = str(ResolverNotLoadedError(NetworkServiceIdentity))

    assert message == (
        "The NetworkServiceIdentity lookups are not loaded, so a network service "
        "cannot be found yet. With a ConfigState, a save, a dry run and apply load "
        "them themselves; `await pipeline.load_resolvers(state, "
        "NetworkServiceIdentity)` loads them now. With plan_module or "
        "update_module, pass resolvers= a registry in which a loaded resolver for "
        "NetworkServiceIdentity is registered."
    )
    error = ResolverNotLoadedError(NetworkServiceIdentity)
    assert (error.name, error.candidates) == ("", [])
    assert isinstance(error, ResolutionError)
    assert isinstance(error, KeyError)


def test_identity_sync_without_a_registered_resolver_is_not_loaded():
    registry = ResolverRegistry()

    with pytest.raises(ResolverNotLoadedError) as caught:
        _ = registry.identity_sync(NetworkServiceIdentity, "object_id", 701)
    assert caught.value.identity_type is NetworkServiceIdentity


async def test_identity_sync_of_an_unknown_value_is_a_resolution_error(
    mock_vco_client: MockVcoClientFactory,
):
    registry = await loaded_registry(mock_vco_client, store_with_services())

    with pytest.raises(ResolutionError) as caught:
        _ = registry.identity_sync(NetworkServiceIdentity, "object_id", 999)
    assert not isinstance(caught.value, ResolverNotLoadedError)
    assert caught.value.name == "999"
    assert caught.value.identity_type is NetworkServiceIdentity
    assert caught.value.candidates == []


async def test_a_service_unknown_by_name_is_a_resolution_error(
    mock_vco_client: MockVcoClientFactory,
):
    registry = await loaded_registry(mock_vco_client, store_with_services())

    with pytest.raises(ResolutionError) as caught:
        _ = registry.resolve_sync(NetworkServiceIdentity, "dns-b")
    assert "dns-a" in str(caught.value)
    with pytest.raises(ResolutionError):
        _ = registry.resolve_sync(NetworkServiceObjectId(999))
    with pytest.raises(ResolutionError):
        _ = registry.resolve_sync(NetworkServiceLogicalId(uuid.UUID(int=1)))


async def test_a_name_matches_without_regard_to_case(
    mock_vco_client: MockVcoClientFactory,
):
    registry = await loaded_registry(mock_vco_client, store_with_services())

    assert registry.resolve_sync(NetworkServiceIdentity, "DNS-A").object_id == 701


async def test_a_shared_name_asks_for_the_logical_id(
    mock_vco_client: MockVcoClientFactory,
):
    store = store_with_services()
    store.add_object(711, logical_id=DUP_DNS, name="dup", type_="dns")
    store.add_object(712, logical_id=DUP_AUTH, name="dup", type_="authentication")
    registry = await loaded_registry(mock_vco_client, store)

    with pytest.raises(ResolutionError) as caught:
        _ = registry.resolve_sync(NetworkServiceIdentity, "dup")
    message = str(caught.value)
    assert "dns" in message
    assert "authentication" in message
    assert "logical ID" in message
    assert DUP_DNS in message
    assert DUP_AUTH in message

    by_logical = registry.resolve_sync(NetworkServiceLogicalId(uuid.UUID(DUP_AUTH)))
    assert by_logical.object_id == 712
    assert registry.resolve_sync(NetworkServiceObjectId(711)).type_ == "dns"
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert sorted(i.object_id for i in resolver.identities()) == [
        701,
        702,
        703,
        711,
        712,
    ]


async def test_an_unloaded_copy_must_be_loaded_again(
    mock_vco_client: MockVcoClientFactory,
):
    registry = await loaded_registry(mock_vco_client, store_with_services())
    resolver = registry.get_resolver(NetworkServiceResolver)

    twin = resolver.unloaded()

    assert not twin.loaded
    assert list(twin.identities()) == []
    with pytest.raises(ResolverNotLoadedError):
        _ = await twin.resolve("dns-a")
    assert resolver.loaded
    assert registry.resolve_sync(NetworkServiceIdentity, "dns-a").object_id == 701
    await twin.load(ResolverContext(enterprise_id=1))
    assert (await twin.resolve("dns-a")).object_id == 701
    copied = registry.unloaded()
    with pytest.raises(ResolverNotLoadedError):
        _ = copied.resolve_sync(NetworkServiceIdentity, "dns-a")


async def test_clearing_the_registry_context_unloads_the_resolver(
    mock_vco_client: MockVcoClientFactory,
):
    registry = await loaded_registry(mock_vco_client, store_with_services())

    with (
        registry.context(ResolverContext(enterprise_id=2)),
        pytest.raises(ResolverNotLoadedError),
    ):
        _ = registry.resolve_sync(NetworkServiceIdentity, "dns-a")
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert not resolver.loaded
    assert list(resolver.identities()) == []


async def test_a_reload_replaces_what_was_loaded(
    mock_vco_client: MockVcoClientFactory,
):
    store = store_with_services()
    registry = await loaded_registry(mock_vco_client, store)
    del store.objects[701]

    await registry.get_resolver(NetworkServiceResolver).load(
        ResolverContext(enterprise_id=1)
    )

    with pytest.raises(ResolutionError):
        _ = registry.resolve_sync(NetworkServiceIdentity, "dns-a")
    assert registry.resolve_sync(NetworkServiceIdentity, "zs-a").object_id == 703


async def test_a_resolver_that_needs_no_load_answers_identity_sync_as_loaded():
    resolver = await make_segment_resolver(
        [{"name": "Global Segment", "segment_id": 0}]
    )
    registry = ResolverRegistry()
    registry.register(resolver)

    assert not SegmentResolver.requires_load
    assert registry.identity_sync(SegmentIdentity, "segment_id", 0).name == (
        "Global Segment"
    )


async def test_the_fixture_resolver_is_loaded_with_the_services_given():
    services = [
        NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns"),
        NetworkServiceIdentity("tacacs-a", uuid.UUID(TACACS), 704, "tacacs"),
    ]
    resolver = await make_network_service_resolver(services)
    registry = ResolverRegistry()
    registry.register(resolver)

    assert resolver.loaded
    assert sorted(resolver.identities(), key=lambda i: i.object_id) == services
    assert registry.resolve_sync(NetworkServiceObjectId(704)).type_ == "tacacs"
