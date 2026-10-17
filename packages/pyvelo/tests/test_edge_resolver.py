"""Tests for EdgeResolver."""

import uuid
from unittest.mock import AsyncMock

import pytest
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.edge import EdgeResolver
from pyvelo.core.identity import EdgeIdentity

EDGE1_UUID = uuid.UUID("11111111-1111-1111-1111-111111111111")
EDGE2_UUID = uuid.UUID("22222222-2222-2222-2222-222222222222")


class FakeEdge:
    """Minimal edge object matching EdgeObject shape."""

    def __init__(
        self,
        id_: int,
        name: str | None,
        logical_id: uuid.UUID,
        model_number: str | None = None,
    ):
        self.id_ = id_
        self.name = name
        self.logical_id = logical_id
        self.model_number = model_number


class TestEdgeResolver:
    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        client.get_enterprise_edges.return_value = [
            FakeEdge(id_=1, name="Branch-NYC", logical_id=EDGE1_UUID),
            FakeEdge(id_=2, name="Branch-LAX", logical_id=EDGE2_UUID),
        ]
        return client

    @pytest.fixture
    async def resolver(self, mock_client: AsyncMock) -> EdgeResolver:
        r = EdgeResolver(mock_client)
        await r.load(ResolverContext(enterprise_id=42))
        return r

    async def test_resolve_by_name(self, resolver: EdgeResolver):
        identity = await resolver.resolve("Branch-NYC")
        assert identity.name == "Branch-NYC"
        assert identity.id == 1
        assert identity.logical_id == EDGE1_UUID

    async def test_resolve_second_edge(self, resolver: EdgeResolver):
        identity = await resolver.resolve("Branch-LAX")
        assert identity.name == "Branch-LAX"
        assert identity.id == 2

    async def test_resolve_unknown(self, resolver: EdgeResolver):
        with pytest.raises(KeyError, match="Unknown edge.*nonexistent"):
            await resolver.resolve("nonexistent")

    async def test_reverse_by_id(self, resolver: EdgeResolver):
        name = await resolver.reverse_by("id", 1)
        assert name == "Branch-NYC"

    async def test_reverse_by_logical_id(self, resolver: EdgeResolver):
        name = await resolver.reverse_by("logical_id", EDGE2_UUID)
        assert name == "Branch-LAX"

    async def test_reverse_unknown(self, resolver: EdgeResolver):
        with pytest.raises(KeyError, match="No edge with"):
            await resolver.reverse_by("id", 999)

    def test_identity_type(self, mock_client: AsyncMock):
        assert EdgeResolver(mock_client).identity_type is EdgeIdentity

    async def test_load_passes_enterprise_id(self, mock_client: AsyncMock):
        r = EdgeResolver(mock_client)
        await r.load(ResolverContext(enterprise_id=99))
        call_args = mock_client.get_enterprise_edges.call_args
        request = call_args[0][0]
        assert request.enterprise_id == 99

    async def test_load_without_enterprise_id(self, mock_client: AsyncMock):
        r = EdgeResolver(mock_client)
        await r.load(ResolverContext())
        # Should still work, enterprise_id stays MISSING
        result = await r.resolve("Branch-NYC")
        assert result.id == 1

    async def test_edges_with_none_name_skipped(self, mock_client: AsyncMock):
        mock_client.get_enterprise_edges.return_value = [
            FakeEdge(id_=1, name="Branch-NYC", logical_id=EDGE1_UUID),
            FakeEdge(id_=3, name=None, logical_id=uuid.UUID(int=3)),  # type: ignore[arg-type]
        ]
        r = EdgeResolver(mock_client)
        await r.load(ResolverContext())
        assert len(r._by_name) == 1

    async def test_resolve_case_insensitive(self, resolver: EdgeResolver):
        identity = await resolver.resolve("branch-nyc")
        assert identity.name == "Branch-NYC"
        assert identity.id == 1

    async def test_resolve_case_insensitive_upper(self, resolver: EdgeResolver):
        identity = await resolver.resolve("BRANCH-LAX")
        assert identity.name == "Branch-LAX"
        assert identity.id == 2

    async def test_resolve_fuzzy_single_match(self, mock_client: AsyncMock):
        mock_client.get_enterprise_edges.return_value = [
            FakeEdge(id_=1, name="Example-Edge", logical_id=EDGE1_UUID),
            FakeEdge(id_=2, name="DC-Primary", logical_id=EDGE2_UUID),
        ]
        r = EdgeResolver(mock_client)
        await r.load(ResolverContext())
        identity = await r.resolve("Example-Edg")
        assert identity.name == "Example-Edge"

    async def test_resolve_fuzzy_ambiguous(self, resolver: EdgeResolver):
        with pytest.raises(KeyError, match="Did you mean"):
            await resolver.resolve("Branch")
