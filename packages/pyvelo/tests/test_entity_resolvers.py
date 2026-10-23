"""Tests for ProfileResolver, ApplicationResolver, AddressGroupResolver, ServiceGroupResolver, LicenseResolver."""

import uuid
from collections.abc import Sequence
from typing import cast, final

import pytest
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.configuration import GetRoutableApplications
from pyvelo.client.schemas.enterprise import GetObjectGroups
from pyvelo.client.schemas.license import GetEnterpriseEdgeLicenses
from pyvelo.client.schemas.profiles import GetEnterpriseConfigurations
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.address_group import AddressGroupResolver
from pyvelo.config.resolvers.application import ApplicationResolver
from pyvelo.config.resolvers.license import LicenseResolver
from pyvelo.config.resolvers.profile import ProfileResolver
from pyvelo.config.resolvers.service_group import ServiceGroupResolver
from pyvelo.core.identity import (
    AddressGroupIdentity,
    ApplicationIdentity,
    LicenseId,
    LicenseIdentity,
    LicenseName,
    LicenseSku,
    ProfileIdentity,
    ServiceGroupIdentity,
)

AG_UUID1 = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
AG_UUID2 = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SG_UUID1 = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


# ---------------------------------------------------------------------------
# Fake API response objects
# ---------------------------------------------------------------------------


@final
class FakeProfile:
    def __init__(self, id_: int, name: str):
        self.id_: int = id_
        self.name: str = name


@final
class FakeApp:
    def __init__(self, id_: int, name: str, display_name: str | None = None):
        self.id_: int = id_
        self.name: str = name
        self.display_name: str | None = display_name


@final
class FakeAppResult:
    def __init__(self, applications: list[FakeApp]):
        self.applications: list[FakeApp] = applications


@final
class FakeAllAppsResult:
    def __init__(self, applications: list[FakeApp]):
        self.all: FakeAppResult = FakeAppResult(applications)


@final
class FakeObjectGroup:
    def __init__(self, id_: int, name: str, logical_id: uuid.UUID):
        self.id_: int = id_
        self.name: str = name
        self.logical_id: uuid.UUID = logical_id


@final
class FakeLicense:
    def __init__(self, id_: int, license_id: int, name: str, sku: str):
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
        return cast(VcoClientProtocol, cast(object, self))

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

    async def test_resolve(self, resolver: ProfileResolver):
        identity = await resolver.resolve("Default Profile")
        assert identity.name == "Default Profile"
        assert identity.id == 10

    async def test_resolve_second(self, resolver: ProfileResolver):
        identity = await resolver.resolve("Branch Profile")
        assert identity.name == "Branch Profile"
        assert identity.id == 20

    async def test_resolve_unknown(self, resolver: ProfileResolver):
        with pytest.raises(KeyError, match="Unknown profile"):
            _ = await resolver.resolve("nonexistent")

    async def test_reverse_by_id(self, resolver: ProfileResolver):
        name = await resolver.reverse_by("id", 10)
        assert name == "Default Profile"

    async def test_reverse_unknown(self, resolver: ProfileResolver):
        with pytest.raises(KeyError, match="No profile"):
            _ = await resolver.reverse_by("id", 999)

    def test_identity_type(self, mock_client: FakeResolverClient):
        assert ProfileResolver(mock_client.protocol).identity_type is ProfileIdentity

    async def test_load_passes_enterprise_id(self, mock_client: FakeResolverClient):
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

    async def test_resolve_by_display_name(self, resolver: ApplicationResolver):
        identity = await resolver.resolve("Office 365")
        assert identity.id == 100
        assert identity.name == "Office 365"

    async def test_resolve_fallback_to_name(self, resolver: ApplicationResolver):
        identity = await resolver.resolve("ssh")
        assert identity.id == 300

    async def test_resolve_unknown(self, resolver: ApplicationResolver):
        with pytest.raises(KeyError, match="Unknown application"):
            _ = await resolver.resolve("nonexistent")

    async def test_reverse_by_id(self, resolver: ApplicationResolver):
        name = await resolver.reverse_by("id", 200)
        assert name == "Zoom"

    def test_identity_type(self, mock_client: FakeResolverClient):
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

    async def test_resolve(self, resolver: AddressGroupResolver):
        identity = await resolver.resolve("RFC1918")
        assert identity.name == "RFC1918"
        assert identity.id == 50
        assert identity.logical_id == AG_UUID1

    async def test_resolve_unknown(self, resolver: AddressGroupResolver):
        with pytest.raises(KeyError, match="Unknown address group"):
            _ = await resolver.resolve("nonexistent")

    async def test_reverse_by_id(self, resolver: AddressGroupResolver):
        name = await resolver.reverse_by("id", 51)
        assert name == "Branch Subnets"

    async def test_reverse_by_logical_id(self, resolver: AddressGroupResolver):
        name = await resolver.reverse_by("logical_id", AG_UUID1)
        assert name == "RFC1918"

    def test_identity_type(self, mock_client: FakeResolverClient):
        assert (
            AddressGroupResolver(mock_client.protocol).identity_type
            is AddressGroupIdentity
        )

    async def test_load_uses_address_group_type(self, mock_client: FakeResolverClient):
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

    async def test_resolve(self, resolver: ServiceGroupResolver):
        identity = await resolver.resolve("Web Ports")
        assert identity.name == "Web Ports"
        assert identity.id == 60
        assert identity.logical_id == SG_UUID1

    async def test_resolve_unknown(self, resolver: ServiceGroupResolver):
        with pytest.raises(KeyError, match="Unknown service group"):
            _ = await resolver.resolve("nonexistent")

    async def test_reverse_by_id(self, resolver: ServiceGroupResolver):
        name = await resolver.reverse_by("id", 60)
        assert name == "Web Ports"

    def test_identity_type(self, mock_client: FakeResolverClient):
        assert (
            ServiceGroupResolver(mock_client.protocol).identity_type
            is ServiceGroupIdentity
        )

    async def test_load_uses_port_group_type(self, mock_client: FakeResolverClient):
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

    async def test_resolve_by_name(self, resolver: LicenseResolver):
        identity = await resolver.resolve("Enterprise 1G")
        assert identity.name == "Enterprise 1G"
        assert identity.id == 100
        assert identity.license_id == 200
        assert identity.sku == "ENT-BWR-1G"

    async def test_resolve_unknown(self, resolver: LicenseResolver):
        with pytest.raises(KeyError, match="Unknown license"):
            _ = await resolver.resolve("nonexistent")

    async def test_resolve_key_name(self, resolver: LicenseResolver):
        identity = await resolver.resolve_key(LicenseName("Premium 2G"))
        assert identity.id == 101

    async def test_resolve_key_sku(self, resolver: LicenseResolver):
        identity = await resolver.resolve_key(LicenseSku("ENT-BWR-1G"))
        assert identity.name == "Enterprise 1G"

    async def test_resolve_key_id(self, resolver: LicenseResolver):
        identity = await resolver.resolve_key(LicenseId(101))
        assert identity.name == "Premium 2G"

    async def test_reverse_by_id(self, resolver: LicenseResolver):
        name = await resolver.reverse_by("id", 100)
        assert name == "Enterprise 1G"

    async def test_reverse_by_sku(self, resolver: LicenseResolver):
        name = await resolver.reverse_by("sku", "PRM-BWR-2G")
        assert name == "Premium 2G"

    async def test_reverse_by_license_id(self, resolver: LicenseResolver):
        name = await resolver.reverse_by("license_id", 200)
        assert name == "Enterprise 1G"

    def test_identity_type(self, mock_client: FakeResolverClient):
        assert LicenseResolver(mock_client.protocol).identity_type is LicenseIdentity
