"""The network-service resolver.

Services of an enterprise by name, logical ID and object ID.

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
) -> None:
    registry = await loaded_registry(mock_vco_client, store_with_services())
    expected = NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns")

    assert registry.lookup(NetworkServiceIdentity, "dns-a") == expected
    assert registry.lookup(NetworkServiceName("dns-a")) == expected
    assert registry.lookup(NetworkServiceObjectId(701)) == expected
    assert registry.lookup(NetworkServiceLogicalId(uuid.UUID(DNS_A))) == expected
    found = registry.lookup_by(
        NetworkServiceIdentity, lambda s: s.logical_id, uuid.UUID(DNS_A)
    )
    assert found.object_id == 701
    assert registry.lookup(NetworkServiceObjectId(702)).name == "radius-a"
    assert registry.lookup(NetworkServiceLogicalId(uuid.UUID(CSS_A))).name == "zs-a"
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert sorted(i.name for i in resolver.identities()) == [
        "dns-a",
        "radius-a",
        "zs-a",
    ]
    assert (
        registry.lookup_by(NetworkServiceIdentity, lambda s: s.object_id, 703).name
        == "zs-a"
    )


async def test_a_service_of_a_type_the_models_do_not_know_still_loads(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = store_with_services()
    store.add_object(704, logical_id=TACACS, name="tacacs-a", type_="tacacs")
    registry = await loaded_registry(mock_vco_client, store)

    found = registry.lookup(NetworkServiceIdentity, "tacacs-a")

    assert found == NetworkServiceIdentity("tacacs-a", uuid.UUID(TACACS), 704, "tacacs")
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert "tacacs-a" in [i.name for i in resolver.identities()]


async def test_before_its_load_the_resolver_says_it_is_not_loaded(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    client = await mock_vco_client(store_with_services())
    resolver = NetworkServiceResolver(client)
    registry = ResolverRegistry()
    registry.register(resolver)

    with pytest.raises(ResolverNotLoadedError) as caught:
        _ = registry.lookup(NetworkServiceIdentity, "dns-a")
    assert caught.value.identity_type is NetworkServiceIdentity
    assert "load_resolvers(state, NetworkServiceIdentity)" in str(caught.value)
    assert not resolver.loaded
    with pytest.raises(ResolverNotLoadedError):
        _ = registry.lookup(NetworkServiceName("dns-a"))
    with pytest.raises(ResolverNotLoadedError):
        _ = resolver.lookup_key(NetworkServiceObjectId(701))
    with pytest.raises(ResolverNotLoadedError):
        _ = registry.lookup_by(NetworkServiceIdentity, lambda s: s.object_id, 701)

    await resolver.load(ResolverContext(enterprise_id=1))
    assert resolver.loaded
    assert registry.lookup(NetworkServiceIdentity, "dns-a").object_id == 701


def test_the_not_loaded_message_names_the_type_and_the_label() -> None:
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


def test_lookup_by_without_a_registered_resolver_is_not_loaded() -> None:
    registry = ResolverRegistry()

    with pytest.raises(ResolverNotLoadedError) as caught:
        _ = registry.lookup_by(NetworkServiceIdentity, lambda s: s.object_id, 701)
    assert caught.value.identity_type is NetworkServiceIdentity


async def test_lookup_by_of_an_unknown_value_is_a_resolution_error(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry = await loaded_registry(mock_vco_client, store_with_services())

    with pytest.raises(ResolutionError) as caught:
        _ = registry.lookup_by(NetworkServiceIdentity, lambda s: s.object_id, 999)
    assert not isinstance(caught.value, ResolverNotLoadedError)
    assert caught.value.name == "999"
    assert caught.value.identity_type is NetworkServiceIdentity
    assert caught.value.candidates == []


async def test_a_service_unknown_by_name_is_a_resolution_error(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry = await loaded_registry(mock_vco_client, store_with_services())

    with pytest.raises(ResolutionError) as caught:
        _ = registry.lookup(NetworkServiceIdentity, "dns-b")
    assert "dns-a" in str(caught.value)
    with pytest.raises(ResolutionError):
        _ = registry.lookup(NetworkServiceObjectId(999))
    with pytest.raises(ResolutionError):
        _ = registry.lookup(NetworkServiceLogicalId(uuid.UUID(int=1)))


async def test_a_name_matches_without_regard_to_case(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry = await loaded_registry(mock_vco_client, store_with_services())

    assert registry.lookup(NetworkServiceIdentity, "DNS-A").object_id == 701


async def test_a_shared_name_asks_for_the_logical_id(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = store_with_services()
    store.add_object(711, logical_id=DUP_DNS, name="dup", type_="dns")
    store.add_object(712, logical_id=DUP_AUTH, name="dup", type_="authentication")
    registry = await loaded_registry(mock_vco_client, store)

    with pytest.raises(ResolutionError) as caught:
        _ = registry.lookup(NetworkServiceIdentity, "dup")
    message = str(caught.value)
    assert f"a service of type 'dns' ({DUP_DNS})" in message
    assert f"a service of type 'authentication' ({DUP_AUTH})" in message
    assert "a authentication" not in message
    assert "logical ID" in message
    assert DUP_DNS in message
    assert DUP_AUTH in message

    by_logical = registry.lookup(NetworkServiceLogicalId(uuid.UUID(DUP_AUTH)))
    assert by_logical.object_id == 712
    assert registry.lookup(NetworkServiceObjectId(711)).type_ == "dns"
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert sorted(i.object_id for i in resolver.identities()) == [
        701,
        702,
        703,
        711,
        712,
    ]
    with pytest.raises(ResolutionError, match="Several network services are named"):
        _ = registry.find(NetworkServiceIdentity, "dup")
    assert registry.find(NetworkServiceLogicalId(uuid.UUID(int=987654321))) is None


async def test_an_unloaded_copy_must_be_loaded_again(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry = await loaded_registry(mock_vco_client, store_with_services())
    resolver = registry.get_resolver(NetworkServiceResolver)

    twin = resolver.unloaded()

    assert not twin.loaded
    assert list(twin.identities()) == []
    with pytest.raises(ResolverNotLoadedError):
        _ = await twin.resolve("dns-a")
    assert resolver.loaded
    assert registry.lookup(NetworkServiceIdentity, "dns-a").object_id == 701
    await twin.load(ResolverContext(enterprise_id=1))
    assert (await twin.resolve("dns-a")).object_id == 701
    copied = registry.unloaded()
    with pytest.raises(ResolverNotLoadedError):
        _ = copied.lookup(NetworkServiceIdentity, "dns-a")


async def test_clearing_the_registry_context_unloads_the_resolver(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    registry = await loaded_registry(mock_vco_client, store_with_services())

    with (
        registry.context(ResolverContext(enterprise_id=2)),
        pytest.raises(ResolverNotLoadedError),
    ):
        _ = registry.lookup(NetworkServiceIdentity, "dns-a")
    resolver = registry.get_resolver(NetworkServiceResolver)
    assert not resolver.loaded
    assert list(resolver.identities()) == []


async def test_a_reload_replaces_what_was_loaded(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    store = store_with_services()
    registry = await loaded_registry(mock_vco_client, store)
    del store.objects[701]

    await registry.get_resolver(NetworkServiceResolver).load(
        ResolverContext(enterprise_id=1)
    )

    with pytest.raises(ResolutionError):
        _ = registry.lookup(NetworkServiceIdentity, "dns-a")
    assert registry.lookup(NetworkServiceIdentity, "zs-a").object_id == 703


async def test_a_resolver_that_needs_no_load_answers_lookup_by_as_loaded() -> None:
    resolver = await make_segment_resolver(
        [{"name": "Global Segment", "segment_id": 0}]
    )
    registry = ResolverRegistry()
    registry.register(resolver)

    assert not SegmentResolver.requires_load
    assert registry.lookup_by(SegmentIdentity, lambda s: s.segment_id, 0).name == (
        "Global Segment"
    )


async def test_the_fixture_resolver_is_loaded_with_the_services_given() -> None:
    services = [
        NetworkServiceIdentity("dns-a", uuid.UUID(DNS_A), 701, "dns"),
        NetworkServiceIdentity("tacacs-a", uuid.UUID(TACACS), 704, "tacacs"),
    ]
    resolver = await make_network_service_resolver(services)
    registry = ResolverRegistry()
    registry.register(resolver)

    assert resolver.loaded
    assert sorted(resolver.identities(), key=lambda i: i.object_id) == services
    assert registry.lookup(NetworkServiceObjectId(704)).type_ == "tacacs"
