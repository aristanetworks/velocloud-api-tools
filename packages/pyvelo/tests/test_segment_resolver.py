"""Tests for SegmentResolver."""

import uuid
from collections.abc import Sequence
from typing import cast

import pytest
from pyvelo.client.protocol import VcoClientProtocol
from pyvelo.client.schemas.enterprise import GetEnterpriseNetworkSegments
from pyvelo.config.resolver import ResolverContext
from pyvelo.config.resolvers.segment import SegmentResolver
from pyvelo.core.identity import (
    SegmentIdentity,
)
from pyvelo.testing import FakeSegment

GLOBAL_UUID = uuid.UUID("12345678-1234-1234-1234-123456789abc")
PCI_UUID = uuid.UUID("abcdef01-abcd-abcd-abcd-abcdef012345")


def _fake_segments() -> list[FakeSegment]:
    return [
        FakeSegment(
            id_=100, name="Global Segment", logical_id=GLOBAL_UUID, segment_id=0
        ),
        FakeSegment(id_=101, name="PCI", logical_id=PCI_UUID, segment_id=1),
    ]


class FakeSegmentClient:
    def __init__(self, segments: Sequence[FakeSegment]) -> None:
        self.segments: list[FakeSegment] = list(segments)
        self.requests: list[GetEnterpriseNetworkSegments] = []

    async def get_enterprise_network_segments(
        self, request: GetEnterpriseNetworkSegments
    ) -> Sequence[FakeSegment]:
        self.requests.append(request)
        return self.segments


def _client_protocol(client: FakeSegmentClient) -> VcoClientProtocol:
    return cast(VcoClientProtocol, cast(object, client))


class TestSegmentResolver:
    @pytest.fixture
    def mock_client(self) -> FakeSegmentClient:
        return FakeSegmentClient(_fake_segments())

    @pytest.fixture
    async def resolver(self, mock_client: FakeSegmentClient) -> SegmentResolver:
        r = SegmentResolver(_client_protocol(mock_client))
        await r.load(ResolverContext(enterprise_id=42))
        return r

    async def test_resolve_by_name(self, resolver: SegmentResolver):
        identity = await resolver.resolve("Global Segment")
        assert identity.name == "Global Segment"
        assert identity.segment_id == 0
        assert identity.object_id == 100
        assert identity.logical_id == GLOBAL_UUID

    async def test_resolve_second_segment(self, resolver: SegmentResolver):
        identity = await resolver.resolve("PCI")
        assert identity.name == "PCI"
        assert identity.segment_id == 1
        assert identity.object_id == 101

    async def test_resolve_unknown_name(self, resolver: SegmentResolver):
        with pytest.raises(KeyError, match="Unknown segment.*nonexistent"):
            _ = await resolver.resolve("nonexistent")

    async def test_reverse_by_segment_id(self, resolver: SegmentResolver):
        name = await resolver.reverse_by("segment_id", 0)
        assert name == "Global Segment"

    async def test_reverse_by_object_id(self, resolver: SegmentResolver):
        name = await resolver.reverse_by("object_id", 101)
        assert name == "PCI"

    async def test_reverse_by_logical_id(self, resolver: SegmentResolver):
        name = await resolver.reverse_by("logical_id", GLOBAL_UUID)
        assert name == "Global Segment"

    async def test_reverse_unknown_id(self, resolver: SegmentResolver):
        with pytest.raises(KeyError, match="No segment with"):
            _ = await resolver.reverse_by("segment_id", 999)

    async def test_reverse_unknown_attr(self, resolver: SegmentResolver):
        with pytest.raises(KeyError, match="No reverse index"):
            _ = await resolver.reverse_by("bad_attr", 0)

    def test_identity_type(self, mock_client: FakeSegmentClient):
        assert (
            SegmentResolver(_client_protocol(mock_client)).identity_type
            is SegmentIdentity
        )

    async def test_load_passes_enterprise_id(self, mock_client: FakeSegmentClient):
        r = SegmentResolver(_client_protocol(mock_client))
        await r.load(ResolverContext(enterprise_id=99))
        request = mock_client.requests[0]
        assert request.enterprise_id == 99

    async def test_load_without_enterprise_id(self, mock_client: FakeSegmentClient):
        r = SegmentResolver(_client_protocol(mock_client))
        await r.load(ResolverContext())
        result = await r.resolve("Global Segment")
        assert result.segment_id == 0
