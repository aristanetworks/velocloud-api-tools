"""Tests for Resolvable field resolution during merge_patch."""

import uuid
from typing import Annotated

import pytest
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import VcoModel
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import (
    SegmentIdentity,
)

GLOBAL_UUID = uuid.UUID("12345678-1234-1234-1234-123456789abc")
PCI_UUID = uuid.UUID("abcdef01-abcd-abcd-abcd-abcdef012345")


class SegmentMeta(VcoModel):
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0
    logical_id: Annotated[uuid.UUID, Resolvable(SegmentIdentity, "logical_id")] = (
        uuid.UUID(int=0)
    )
    name: str = ""


class Config(VcoModel):
    segment: SegmentMeta = SegmentMeta()
    enabled: bool = True


@pytest.fixture
async def registry(make_segment_resolver) -> ResolverRegistry:
    resolver = await make_segment_resolver(
        [
            {
                "name": "Global Segment",
                "segment_id": 0,
                "object_id": 100,
                "segment_logical_id": GLOBAL_UUID,
            },
            {
                "name": "PCI",
                "segment_id": 1,
                "object_id": 101,
                "segment_logical_id": PCI_UUID,
            },
        ]
    )
    reg = ResolverRegistry()
    reg.register(resolver)
    return reg


class TestMergeResolvable:
    def test_string_resolves_to_segment_id(self, registry: ResolverRegistry):
        m = Config()
        m.merge_patch(
            {"segment": {"segment_id": "PCI"}},
            resolvers=registry,
        )
        assert m.segment.segment_id == 1

    def test_string_resolves_to_logical_id(self, registry: ResolverRegistry):
        m = Config()
        m.merge_patch(
            {"segment": {"logical_id": "Global Segment"}},
            resolvers=registry,
        )
        assert m.segment.logical_id == GLOBAL_UUID

    def test_numeric_value_passes_through(self, registry: ResolverRegistry):
        """Raw IDs should still work without resolution."""
        m = Config()
        m.merge_patch(
            {"segment": {"segment_id": 42}},
            resolvers=registry,
        )
        assert m.segment.segment_id == 42

    def test_no_resolvers_string_passes_through(self):
        """Without resolvers, string values are assigned directly (may fail validation)."""
        m = Config()
        # No resolvers -> string value assigned as-is
        m.merge_patch({"segment": {"name": "test"}})
        assert m.segment.name == "test"

    def test_unknown_name_raises(self, registry: ResolverRegistry):
        m = Config()
        with pytest.raises(KeyError, match="Unknown segment.*nonexistent"):
            m.merge_patch(
                {"segment": {"segment_id": "nonexistent"}},
                resolvers=registry,
            )

    def test_non_resolvable_string_field_unaffected(self, registry: ResolverRegistry):
        """String fields without Resolvable marker aren't resolved."""
        m = Config()
        m.merge_patch(
            {"segment": {"name": "PCI"}},
            resolvers=registry,
        )
        # name is a plain str field, not Resolvable — should be "PCI" literally
        assert m.segment.name == "PCI"

    def test_multiple_resolvable_fields(self, registry: ResolverRegistry):
        """Multiple Resolvable fields can be resolved in one merge."""
        m = Config()
        m.merge_patch(
            {
                "segment": {
                    "segment_id": "Global Segment",
                    "logical_id": "PCI",
                }
            },
            resolvers=registry,
        )
        assert m.segment.segment_id == 0
        assert m.segment.logical_id == PCI_UUID
