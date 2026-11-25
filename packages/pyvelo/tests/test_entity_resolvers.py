"""Tests for the entity resolvers.

ProfileResolver, ApplicationResolver, AddressGroupResolver, ServiceGroupResolver,
LicenseResolver.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from types import SimpleNamespace
from typing import cast, final
from unittest.mock import AsyncMock

import pytest
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.configuration import GetRoutableApplications
from pyvelo.client.schemas.enterprise import GetObjectGroups
from pyvelo.client.schemas.license import GetEnterpriseEdgeLicenses
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.config.resolver import BaseResolver, ResolverContext, ResolverRegistry
from pyvelo.config.resolvers.address_group import AddressGroupResolver
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.config.resolvers.license import LicenseResolver
from pyvelo.config.resolvers.link import LinkResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.config.resolvers.service_group import ServiceGroupResolver
from pyvelo.core.identity import (
    AddressGroupId,
    AddressGroupIdentity,
    AddressGroupLogicalId,
    ApplicationId,
    ApplicationIdentity,
    EdgeId,
    EdgeLogicalId,
    HubLogicalId,
    IdentityKey,
    LicenseId,
    LicenseIdentity,
    LicenseName,
    LicenseSku,
    LinkId,
    LinkIdentity,
    LinkInternalId,
    LinkLogicalId,
    NetworkServiceLogicalId,
    NetworkServiceObjectId,
    ProfileId,
    ProfileIdentity,
    ResolutionError,
    SegmentId,
    SegmentLogicalId,
    SegmentObjectId,
    ServiceGroupId,
    ServiceGroupIdentity,
    ServiceGroupLogicalId,
)
from pyvelo.testing import FakeEdge, FakeSegment
from pyvelo.testing.fixtures import make_hub_resolver, make_network_service_resolver

AG_UUID1 = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
AG_UUID2 = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SG_UUID1 = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


# ---------------------------------------------------------------------------
# Fake API response objects
# ---------------------------------------------------------------------------


@final
class FakeProfile:
    def __init__(self, id_: int, name: str) -> None:
        self.id_: int = id_
        self.name: str = name


@final
class FakeApp:
    def __init__(self, id_: int, name: str, display_name: str | None = None) -> None:
        self.id_: int = id_
        self.name: str = name
        self.display_name: str | None = display_name


@final
class FakeAppResult:
    def __init__(self, applications: list[FakeApp]) -> None:
        self.applications: list[FakeApp] = applications


@final
class FakeAllAppsResult:
    def __init__(self, applications: list[FakeApp]) -> None:
        self.all: FakeAppResult = FakeAppResult(applications)


@final
class FakeObjectGroup:
    def __init__(self, id_: int, name: str, logical_id: uuid.UUID) -> None:
        self.id_: int = id_
        self.name: str = name
        self.logical_id: uuid.UUID = logical_id


@final
class FakeLicense:
    def __init__(self, id_: int, license_id: int, name: str, sku: str) -> None:
        self.id_: int = id_
        self.license_id: int = license_id
        self.name: str = name
        self.sku: str = sku


@final
class FakeResolverClient:
    def __init__(self) -> None:
        self.profiles: list[FakeProfile] = []
        self.applications: FakeAllAppsResult = FakeAllAppsResult([])
        self.object_groups: list[FakeObjectGroup] = []
        self.licenses: list[FakeLicense] = []
        self.profile_requests: list[GetEnterpriseConfigurations] = []
        self.application_requests: list[GetRoutableApplications] = []
        self.object_group_requests: list[GetObjectGroups] = []
        self.license_requests: list[GetEnterpriseEdgeLicenses] = []

    @property
    def protocol(self) -> VcoClientProtocol:
        return cast("VcoClientProtocol", cast("object", self))

    async def get_enterprise_configurations(
        self, request: GetEnterpriseConfigurations
    ) -> Sequence[FakeProfile]:
        self.profile_requests.append(request)
        return self.profiles

    async def get_applications(
        self, request: GetRoutableApplications
    ) -> FakeAllAppsResult:
        self.application_requests.append(request)
        return self.applications

    async def get_object_groups(
        self, request: GetObjectGroups
    ) -> Sequence[FakeObjectGroup]:
        self.object_group_requests.append(request)
        return self.object_groups

    async def get_enterprise_edge_licenses(
        self, request: GetEnterpriseEdgeLicenses
    ) -> Sequence[FakeLicense]:
        self.license_requests.append(request)
        return self.licenses


# ---------------------------------------------------------------------------
# ProfileResolver
# ---------------------------------------------------------------------------


class TestProfileResolver:
    @pytest.fixture
    def mock_client(self) -> FakeResolverClient:
        client = FakeResolverClient()
        client.profiles = [
            FakeProfile(id_=10, name="Default Profile"),
            FakeProfile(id_=20, name="Branch Profile"),
        ]
        return client

    @pytest.fixture
    async def resolver(self, mock_client: FakeResolverClient) -> ProfileResolver:
        r = ProfileResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=1))
        return r

    async def test_resolve(self, resolver: ProfileResolver) -> None:
        identity = await resolver.resolve("Default Profile")
        assert identity.name == "Default Profile"
        assert identity.id == 10

    async def test_resolve_second(self, resolver: ProfileResolver) -> None:
        identity = await resolver.resolve("Branch Profile")
        assert identity.name == "Branch Profile"
        assert identity.id == 20

    async def test_resolve_unknown(self, resolver: ProfileResolver) -> None:
        with pytest.raises(KeyError, match="Unknown profile"):
            _ = await resolver.resolve("nonexistent")

    async def test_lookup_key_by_id(self, resolver: ProfileResolver) -> None:
        assert resolver.lookup_key(ProfileId(10)).name == "Default Profile"

    def test_identity_type(self, mock_client: FakeResolverClient) -> None:
        assert ProfileResolver(mock_client.protocol).identity_type is ProfileIdentity

    async def test_load_passes_enterprise_id(
        self, mock_client: FakeResolverClient
    ) -> None:
        r = ProfileResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=42))
        request = mock_client.profile_requests[0]
        assert request.enterprise_id == 42


# ---------------------------------------------------------------------------
# ApplicationResolver
# ---------------------------------------------------------------------------


class TestApplicationResolver:
    @pytest.fixture
    def mock_client(self) -> FakeResolverClient:
        client = FakeResolverClient()
        client.applications = FakeAllAppsResult(
            applications=[
                FakeApp(id_=100, name="office365", display_name="Office 365"),
                FakeApp(id_=200, name="zoom", display_name="Zoom"),
                FakeApp(id_=300, name="ssh"),  # no display_name
            ]
        )
        return client

    @pytest.fixture
    async def resolver(self, mock_client: FakeResolverClient) -> ApplicationResolver:
        r = ApplicationResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=1))
        return r

    async def test_resolve_by_display_name(self, resolver: ApplicationResolver) -> None:
        identity = await resolver.resolve("Office 365")
        assert identity.id == 100
        assert identity.name == "Office 365"

    async def test_resolve_fallback_to_name(
        self, resolver: ApplicationResolver
    ) -> None:
        identity = await resolver.resolve("ssh")
        assert identity.id == 300

    async def test_resolve_unknown(self, resolver: ApplicationResolver) -> None:
        with pytest.raises(KeyError, match="Unknown application"):
            _ = await resolver.resolve("nonexistent")

    async def test_lookup_key_by_id(self, resolver: ApplicationResolver) -> None:
        assert resolver.lookup_key(ApplicationId(200)).name == "Zoom"

    def test_identity_type(self, mock_client: FakeResolverClient) -> None:
        assert (
            ApplicationResolver(mock_client.protocol).identity_type
            is ApplicationIdentity
        )


# ---------------------------------------------------------------------------
# AddressGroupResolver
# ---------------------------------------------------------------------------


class TestAddressGroupResolver:
    @pytest.fixture
    def mock_client(self) -> FakeResolverClient:
        client = FakeResolverClient()
        client.object_groups = [
            FakeObjectGroup(id_=50, name="RFC1918", logical_id=AG_UUID1),
            FakeObjectGroup(id_=51, name="Branch Subnets", logical_id=AG_UUID2),
        ]
        return client

    @pytest.fixture
    async def resolver(self, mock_client: FakeResolverClient) -> AddressGroupResolver:
        r = AddressGroupResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=1))
        return r

    async def test_resolve(self, resolver: AddressGroupResolver) -> None:
        identity = await resolver.resolve("RFC1918")
        assert identity.name == "RFC1918"
        assert identity.id == 50
        assert identity.logical_id == AG_UUID1

    async def test_resolve_unknown(self, resolver: AddressGroupResolver) -> None:
        with pytest.raises(KeyError, match="Unknown address group"):
            _ = await resolver.resolve("nonexistent")

    async def test_lookup_key_by_id(self, resolver: AddressGroupResolver) -> None:
        assert resolver.lookup_key(AddressGroupId(51)).name == "Branch Subnets"

    async def test_lookup_key_by_logical_id(
        self, resolver: AddressGroupResolver
    ) -> None:
        assert resolver.lookup_key(AddressGroupLogicalId(AG_UUID1)).name == "RFC1918"

    def test_identity_type(self, mock_client: FakeResolverClient) -> None:
        assert (
            AddressGroupResolver(mock_client.protocol).identity_type
            is AddressGroupIdentity
        )

    async def test_load_uses_address_group_type(
        self, mock_client: FakeResolverClient
    ) -> None:
        r = AddressGroupResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=1))
        request = mock_client.object_group_requests[0]
        assert request.type_ == "address_group"


# ---------------------------------------------------------------------------
# ServiceGroupResolver
# ---------------------------------------------------------------------------


class TestServiceGroupResolver:
    @pytest.fixture
    def mock_client(self) -> FakeResolverClient:
        client = FakeResolverClient()
        client.object_groups = [
            FakeObjectGroup(id_=60, name="Web Ports", logical_id=SG_UUID1),
        ]
        return client

    @pytest.fixture
    async def resolver(self, mock_client: FakeResolverClient) -> ServiceGroupResolver:
        r = ServiceGroupResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=1))
        return r

    async def test_resolve(self, resolver: ServiceGroupResolver) -> None:
        identity = await resolver.resolve("Web Ports")
        assert identity.name == "Web Ports"
        assert identity.id == 60
        assert identity.logical_id == SG_UUID1

    async def test_resolve_unknown(self, resolver: ServiceGroupResolver) -> None:
        with pytest.raises(KeyError, match="Unknown service group"):
            _ = await resolver.resolve("nonexistent")

    async def test_lookup_key_by_id(self, resolver: ServiceGroupResolver) -> None:
        assert resolver.lookup_key(ServiceGroupId(60)).name == "Web Ports"

    def test_identity_type(self, mock_client: FakeResolverClient) -> None:
        assert (
            ServiceGroupResolver(mock_client.protocol).identity_type
            is ServiceGroupIdentity
        )

    async def test_load_uses_port_group_type(
        self, mock_client: FakeResolverClient
    ) -> None:
        r = ServiceGroupResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=1))
        request = mock_client.object_group_requests[0]
        assert request.type_ == "port_group"


# ---------------------------------------------------------------------------
# LicenseResolver
# ---------------------------------------------------------------------------


class TestLicenseResolver:
    @pytest.fixture
    def mock_client(self) -> FakeResolverClient:
        client = FakeResolverClient()
        client.licenses = [
            FakeLicense(100, 200, "Enterprise 1G", "ENT-BWR-1G"),
            FakeLicense(101, 201, "Premium 2G", "PRM-BWR-2G"),
        ]
        return client

    @pytest.fixture
    async def resolver(self, mock_client: FakeResolverClient) -> LicenseResolver:
        r = LicenseResolver(mock_client.protocol)
        await r.load(ResolverContext(enterprise_id=1))
        return r

    async def test_resolve_by_name(self, resolver: LicenseResolver) -> None:
        identity = await resolver.resolve("Enterprise 1G")
        assert identity.name == "Enterprise 1G"
        assert identity.id == 100
        assert identity.license_id == 200
        assert identity.sku == "ENT-BWR-1G"

    async def test_resolve_unknown(self, resolver: LicenseResolver) -> None:
        with pytest.raises(KeyError, match="Unknown license"):
            _ = await resolver.resolve("nonexistent")

    async def test_resolve_key_name(self, resolver: LicenseResolver) -> None:
        identity = await resolver.resolve_key(LicenseName("Premium 2G"))
        assert identity.id == 101

    async def test_resolve_key_sku(self, resolver: LicenseResolver) -> None:
        identity = await resolver.resolve_key(LicenseSku("ENT-BWR-1G"))
        assert identity.name == "Enterprise 1G"

    async def test_resolve_key_id(self, resolver: LicenseResolver) -> None:
        identity = await resolver.resolve_key(LicenseId(101))
        assert identity.name == "Premium 2G"

    async def test_lookup_key_by_id(self, resolver: LicenseResolver) -> None:
        assert resolver.lookup_key(LicenseId(100)).name == "Enterprise 1G"

    async def test_lookup_key_by_sku(self, resolver: LicenseResolver) -> None:
        assert resolver.lookup_key(LicenseSku("PRM-BWR-2G")).name == "Premium 2G"

    async def test_lookup_by_license_id(self, resolver: LicenseResolver) -> None:
        registry = ResolverRegistry()
        registry.register(resolver)
        found = registry.lookup_by(LicenseIdentity, lambda i: i.license_id, 200)
        assert found.name == "Enterprise 1G"

    def test_identity_type(self, mock_client: FakeResolverClient) -> None:
        assert LicenseResolver(mock_client.protocol).identity_type is LicenseIdentity


# ---------------------------------------------------------------------------
# An ID key the resolver does not hold
# ---------------------------------------------------------------------------

ABSENT_UUID = uuid.UUID("99999999-9999-4999-8999-999999999999")


def _fake_client(**answers: object) -> VcoClientProtocol:
    client = AsyncMock()
    for method, answer in answers.items():
        getattr(client, method).return_value = answer
    return cast("VcoClientProtocol", client)


def _erased[T](resolver: BaseResolver[T]) -> BaseResolver[object]:
    return cast("BaseResolver[object]", cast("object", resolver))


async def _loaded[T](resolver: BaseResolver[T]) -> BaseResolver[object]:
    await resolver.load(ResolverContext(enterprise_id=1, edge_id=7))
    return _erased(resolver)


async def _segments() -> BaseResolver[object]:
    return await _loaded(
        SegmentResolver(
            _fake_client(
                get_enterprise_network_segments=[
                    FakeSegment(
                        id_=100,
                        name="Global Segment",
                        logical_id=uuid.UUID(int=1),
                        segment_id=0,
                    )
                ]
            )
        )
    )


async def _edges() -> BaseResolver[object]:
    edge = FakeEdge("Branch", 1, uuid.UUID(int=1))
    return await _loaded(EdgeResolver(_fake_client(get_enterprise_edges=[edge])))


async def _profiles() -> BaseResolver[object]:
    return await _loaded(
        ProfileResolver(
            _fake_client(
                get_enterprise_configurations=[FakeProfile(10, "Default Profile")]
            )
        )
    )


async def _applications() -> BaseResolver[object]:
    apps = FakeAllAppsResult([FakeApp(200, "zoom", "Zoom")])
    return await _loaded(ApplicationResolver(_fake_client(get_applications=apps)))


async def _address_groups() -> BaseResolver[object]:
    group = FakeObjectGroup(50, "RFC1918", AG_UUID1)
    return await _loaded(AddressGroupResolver(_fake_client(get_object_groups=[group])))


async def _service_groups() -> BaseResolver[object]:
    group = FakeObjectGroup(60, "Web Ports", SG_UUID1)
    return await _loaded(ServiceGroupResolver(_fake_client(get_object_groups=[group])))


async def _licenses() -> BaseResolver[object]:
    lic = FakeLicense(100, 200, "Enterprise 1G", "ENT-BWR-1G")
    return await _loaded(
        LicenseResolver(_fake_client(get_enterprise_edge_licenses=[lic]))
    )


async def _links() -> BaseResolver[object]:
    link = SimpleNamespace(
        display_name="GE1",
        name="GE1",
        id_=5,
        logical_id="link-1",
        internal_id=uuid.UUID(int=5),
    )
    edge = SimpleNamespace(recent_links=[link])
    return await _loaded(LinkResolver(_fake_client(get_edge=edge)))


async def _network_services() -> BaseResolver[object]:
    return _erased(await make_network_service_resolver([]))


async def _hubs() -> BaseResolver[object]:
    return _erased(await make_hub_resolver([]))


_UNKNOWN_ID_CASES: list[
    tuple[Callable[[], Awaitable[BaseResolver[object]]], IdentityKey[object], str]
] = [
    (_segments, SegmentId(99), "No segment has segment_id 99."),
    (_segments, SegmentObjectId(99), "No segment has object_id 99."),
    (
        _segments,
        SegmentLogicalId(ABSENT_UUID),
        f"No segment has logical_id {ABSENT_UUID}.",
    ),
    (_edges, EdgeId(99), "No edge has id 99."),
    (_edges, EdgeLogicalId(ABSENT_UUID), f"No edge has logical_id {ABSENT_UUID}."),
    (_profiles, ProfileId(99), "No profile has id 99."),
    (_applications, ApplicationId(99), "No application has id 99."),
    (_address_groups, AddressGroupId(99), "No address group has id 99."),
    (
        _address_groups,
        AddressGroupLogicalId(ABSENT_UUID),
        f"No address group has logical_id {ABSENT_UUID}.",
    ),
    (_service_groups, ServiceGroupId(99), "No service group has id 99."),
    (
        _service_groups,
        ServiceGroupLogicalId(ABSENT_UUID),
        f"No service group has logical_id {ABSENT_UUID}.",
    ),
    (_licenses, LicenseId(99), "No license has id 99."),
    (_licenses, LicenseSku("NO-SKU"), "No license has sku NO-SKU."),
    (_links, LinkId(99), "No link has id 99."),
    (_links, LinkLogicalId("no-such-link"), "No link has logical_id no-such-link."),
    (
        _links,
        LinkInternalId(ABSENT_UUID),
        f"No link has internal_id {ABSENT_UUID}.",
    ),
    (
        _network_services,
        NetworkServiceLogicalId(ABSENT_UUID),
        f"No network service has logical_id {ABSENT_UUID}.",
    ),
    (
        _network_services,
        NetworkServiceObjectId(99),
        "No network service has object_id 99.",
    ),
    (
        _hubs,
        HubLogicalId(ABSENT_UUID),
        f"No Edge or hub cluster of the enterprise has the logical ID {ABSENT_UUID}",
    ),
]


@pytest.mark.parametrize(
    ("build", "key", "message"),
    _UNKNOWN_ID_CASES,
    ids=[f"{type(key).__name__}" for _, key, _ in _UNKNOWN_ID_CASES],
)
async def test_an_unknown_id_key_raises_resolution_error(
    build: Callable[[], Awaitable[BaseResolver[object]]],
    key: IdentityKey[object],
    message: str,
) -> None:
    resolver = await build()
    with pytest.raises(ResolutionError) as caught:
        _ = resolver.lookup_key(key)
    assert str(caught.value) == message
    assert isinstance(caught.value, KeyError)


_GE1 = LinkIdentity(
    name="GE1", id=5, logical_id="link-1", internal_id=uuid.UUID(int=5), edge_id=7
)
_WEB_PORTS = ServiceGroupIdentity(name="Web Ports", id=60, logical_id=SG_UUID1)

_KNOWN_ID_CASES: list[
    tuple[Callable[[], Awaitable[BaseResolver[object]]], IdentityKey[object], object]
] = [
    (_links, LinkId(5), _GE1),
    (_links, LinkLogicalId("link-1"), _GE1),
    (_links, LinkInternalId(uuid.UUID(int=5)), _GE1),
    (_service_groups, ServiceGroupLogicalId(SG_UUID1), _WEB_PORTS),
]


@pytest.mark.parametrize(
    ("build", "key", "expected"),
    _KNOWN_ID_CASES,
    ids=[f"{type(key).__name__}" for _, key, _ in _KNOWN_ID_CASES],
)
async def test_a_known_id_key_finds_the_identity_registered_under_it(
    build: Callable[[], Awaitable[BaseResolver[object]]],
    key: IdentityKey[object],
    expected: object,
) -> None:
    resolver = await build()
    assert resolver.lookup_key(key) == expected
