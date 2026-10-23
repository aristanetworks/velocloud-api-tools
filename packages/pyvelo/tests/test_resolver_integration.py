"""Integration tests: resolver + merge end-to-end."""

import uuid
from typing import Annotated

import pytest
from pydantic import Field
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import VcoModel
from pyvelo.core.fields import EdgeLocal, KeyField, OverrideFlag, Resolvable
from pyvelo.core.identity import (
    SegmentIdentity,
)

from ._type_support import SegmentResolverFactory

# ---------------------------------------------------------------------------
# Test segment data
# ---------------------------------------------------------------------------

GLOBAL_UUID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PCI_UUID = uuid.UUID("22222222-2222-2222-2222-222222222222")
DMZ_UUID = uuid.UUID("33333333-3333-3333-3333-333333333333")

SEGMENT_DATA = [
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
    {
        "name": "DMZ",
        "segment_id": 2,
        "object_id": 102,
        "segment_logical_id": DMZ_UUID,
    },
]


# ---------------------------------------------------------------------------
# Models mimicking real VCO configuration structure
# ---------------------------------------------------------------------------


class SegmentMeta(VcoModel):
    segment_id: Annotated[int, Resolvable(SegmentIdentity, "segment_id")] = 0
    logical_id: Annotated[uuid.UUID, Resolvable(SegmentIdentity, "logical_id")] = (
        uuid.UUID(int=0)
    )
    name: str = ""


class Rule(VcoModel):
    name: str
    enabled: bool = True
    priority: int = 0


class FirewallSegment(VcoModel):
    segment: SegmentMeta
    override: Annotated[bool, OverrideFlag] = False
    rules: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)
    cidr_ip: Annotated[str, EdgeLocal] = ""


class FirewallConfig(VcoModel):
    segments: Annotated[list[FirewallSegment], KeyField("segment.name")] = Field(
        default_factory=list
    )
    enabled: bool = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def registry(make_segment_resolver: SegmentResolverFactory) -> ResolverRegistry:
    resolver = await make_segment_resolver(SEGMENT_DATA)
    reg = ResolverRegistry()
    reg.register(resolver)
    return reg


def _make_config() -> FirewallConfig:
    return FirewallConfig(
        segments=[
            FirewallSegment(
                segment=SegmentMeta(
                    segment_id=0,
                    logical_id=GLOBAL_UUID,
                    name="Global Segment",
                ),
                rules=[
                    Rule(name="allow-all", priority=999),
                    Rule(name="block-ssh", priority=1),
                ],
            ),
            FirewallSegment(
                segment=SegmentMeta(
                    segment_id=1,
                    logical_id=PCI_UUID,
                    name="PCI",
                ),
                rules=[Rule(name="deny-all")],
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestResolverMergeIntegration:
    def test_resolve_segment_id_in_nested_merge(self, registry: ResolverRegistry):
        """Resolve segment name to ID during nested merge patch."""
        config = _make_config()
        _ = config.merge_patch(
            {"segments": {"PCI": {"segment": {"segment_id": "PCI"}}}},
            resolvers=registry,
        )
        pci = config.segments[1]
        assert pci.segment.segment_id == 1

    def test_resolve_logical_id_in_merge(self, registry: ResolverRegistry):
        """Resolve segment name to logical_id during merge."""
        config = _make_config()
        _ = config.merge_patch(
            {"segments": {"Global Segment": {"segment": {"logical_id": "DMZ"}}}},
            resolvers=registry,
        )
        global_seg = config.segments[0]
        assert global_seg.segment.logical_id == DMZ_UUID

    def test_resolve_plus_rules_merge(self, registry: ResolverRegistry):
        """Combine resolvable field + keyed list merge + override flags."""
        config = _make_config()
        _ = config.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "segment": {"segment_id": "Global Segment"},
                        "rules": {
                            "block-ssh": {"priority": 0},
                            "new-rule": {"name": "new-rule", "priority": 5},
                        },
                    }
                }
            },
            resolvers=registry,
        )
        global_seg = config.segments[0]
        # Segment ID resolved
        assert global_seg.segment.segment_id == 0
        # Rule updated
        ssh = next(r for r in global_seg.rules if r.name == "block-ssh")
        assert ssh.priority == 0
        # New rule added
        assert len(global_seg.rules) == 3
        new = next(r for r in global_seg.rules if r.name == "new-rule")
        assert new.priority == 5
        # Override triggered (rules are not EdgeLocal)
        assert global_seg.override is True

    def test_raw_ids_still_work_with_resolvers(self, registry: ResolverRegistry):
        """Passing raw int IDs should work even when resolvers are provided."""
        config = _make_config()
        _ = config.merge_patch(
            {"segments": {"PCI": {"segment": {"segment_id": 99}}}},
            resolvers=registry,
        )
        assert config.segments[1].segment.segment_id == 99

    def test_edge_local_with_resolve_no_override(self, registry: ResolverRegistry):
        """EdgeLocal changes + resolve should not trigger override."""
        config = _make_config()
        _ = config.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "cidr_ip": "10.0.0.1",
                    }
                }
            },
            resolvers=registry,
        )
        assert config.segments[0].override is False

    async def test_reverse_resolution(self, registry: ResolverRegistry):
        """Test reverse resolution for YAML export scenario."""
        name = await registry.reverse(SegmentIdentity, "segment_id", 1)
        assert name == "PCI"
        name = await registry.reverse(SegmentIdentity, "logical_id", GLOBAL_UUID)
        assert name == "Global Segment"

    def test_unknown_segment_name_clear_error(self, registry: ResolverRegistry):
        """Resolution failure produces a clear error message."""
        config = _make_config()
        with pytest.raises(KeyError, match="Unknown segment.*Nonexistent"):
            _ = config.merge_patch(
                {
                    "segments": {
                        "Global Segment": {"segment": {"segment_id": "Nonexistent"}}
                    }
                },
                resolvers=registry,
            )

    def test_no_resolvers_no_resolution(self):
        """Without resolvers, string values are assigned directly."""
        config = _make_config()
        # Passing a string to a Resolvable field without resolvers
        # should just assign the string (no crash)
        _ = config.merge_patch(
            {"segments": {"PCI": {"segment": {"name": "Renamed PCI"}}}}
        )
        assert config.segments[1].segment.name == "Renamed PCI"

    def test_pci_untouched_when_only_global_patched(self, registry: ResolverRegistry):
        """Unmentioned segments are not affected."""
        config = _make_config()
        _ = config.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "segment": {"segment_id": "Global Segment"},
                    }
                }
            },
            resolvers=registry,
        )
        pci = config.segments[1]
        assert pci.segment.name == "PCI"
        assert pci.segment.segment_id == 1
        assert pci.override is False
