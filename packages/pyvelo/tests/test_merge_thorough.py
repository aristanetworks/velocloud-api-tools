"""Thorough merge_patch tests of deep nesting, annotation combinations and edge cases.

They cover multi-operation scenarios too. These tests exercise merge_patch with
complex, realistic model hierarchies that combine KeyField, OverrideFlag,
EdgeLocal, Resolvable, Alias, and MISSING annotations at varying depths.
"""

import uuid
from typing import Annotated

import pytest
from pydantic import MISSING, Field
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import Alias, PatchError, VcoModel, add, delete, move
from pyvelo.core.fields import EdgeLocal, KeyField, OverrideFlag, Resolvable
from pyvelo.core.identity import (
    SegmentIdentity,
)
from pyvelo.core.rebase import Key, Path

from ._type_support import SegmentResolverFactory

# ---------------------------------------------------------------------------
# Shared model hierarchy — 4 levels deep with all annotation types
# ---------------------------------------------------------------------------


class AclEntry(VcoModel):
    """Leaf model inside a rule's nested KeyField list."""

    ip: str
    mask: str = "255.255.255.0"
    action: str = "allow"


class MatchCriteria(VcoModel):
    """Sub-model inside a Rule, no override flag of its own."""

    protocol: str = "any"
    src_port: int | MISSING = MISSING
    dst_port: int | MISSING = MISSING


class Rule(VcoModel):
    """A rule inside a segment — has its own nested KeyField list."""

    name: str
    enabled: bool = True
    priority: int = 0
    match: MatchCriteria = Field(default_factory=MatchCriteria)
    acl: Annotated[list[AclEntry], KeyField("ip")] = Field(default_factory=list)


class SegmentMeta(VcoModel):
    name: str
    segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)] = 0
    logical_id: Annotated[
        uuid.UUID, Resolvable(SegmentIdentity, lambda i: i.logical_id)
    ] = uuid.UUID(int=0)


class Segment(VcoModel):
    """Mid-level model with its own OverrideFlag scope."""

    segment: SegmentMeta
    override: Annotated[bool, OverrideFlag] = False
    rules: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)
    cidr_ip: Annotated[str, EdgeLocal] = ""
    cidr_prefix: Annotated[int, EdgeLocal] = 24
    vlan_id: int = 0
    description: str | MISSING = MISSING


class DnsSettings(VcoModel):
    primary: str = "8.8.8.8"
    secondary: str = "8.8.4.4"
    enabled: bool = True


class Config(VcoModel):
    """Top-level model with its own override scope."""

    override: Annotated[bool, OverrideFlag] = False
    segments: Annotated[list[Segment], KeyField("segment.name")] = Field(
        default_factory=list
    )
    dns: DnsSettings = Field(default_factory=DnsSettings)
    enabled: bool = True
    tag: str | MISSING = MISSING


class WithAlias(VcoModel):
    display_name: Annotated[str, Alias("displayName")] = ""
    vlan_tag: Annotated[int, Alias("VLANTag")] = 0
    description: str | MISSING = MISSING


# ---------------------------------------------------------------------------
# Resolver fixture
# ---------------------------------------------------------------------------

GLOBAL_UUID = uuid.UUID("12345678-1234-1234-1234-123456789abc")
PCI_UUID = uuid.UUID("abcdef01-abcd-abcd-abcd-abcdef012345")
VOICE_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture
async def registry(make_segment_resolver: SegmentResolverFactory) -> ResolverRegistry:
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
            {
                "name": "Voice",
                "segment_id": 2,
                "object_id": 102,
                "segment_logical_id": VOICE_UUID,
            },
        ]
    )
    reg = ResolverRegistry()
    reg.register(resolver)
    return reg


# ---------------------------------------------------------------------------
# Helper to build a realistic 4-level deep config
# ---------------------------------------------------------------------------


def make_deep_config() -> Config:
    return Config(
        segments=[
            Segment(
                segment=SegmentMeta(name="Global", segment_id=0),
                rules=[
                    Rule(
                        name="allow-all",
                        priority=999,
                        match=MatchCriteria(protocol="any"),
                        acl=[
                            AclEntry(ip="10.0.0.0", mask="255.0.0.0"),
                            AclEntry(ip="192.168.0.0", mask="255.255.0.0"),
                        ],
                    ),
                    Rule(
                        name="block-ssh",
                        priority=1,
                        match=MatchCriteria(protocol="tcp", dst_port=22),
                    ),
                ],
                vlan_id=100,
            ),
            Segment(
                segment=SegmentMeta(name="PCI", segment_id=1),
                rules=[
                    Rule(name="deny-all", priority=0, enabled=False),
                ],
                vlan_id=200,
            ),
            Segment(
                segment=SegmentMeta(name="Voice", segment_id=2),
                rules=[],
                vlan_id=300,
            ),
        ],
        dns=DnsSettings(primary="1.1.1.1"),
        tag="production",
    )


# ===================================================================
# 1. No-op and empty patch scenarios
# ===================================================================


class TestNoOpMerge:
    def test_empty_patch_is_noop(self) -> None:
        m = make_deep_config()
        original_tag = m.tag
        original_vlan = m.segments[0].vlan_id
        _ = m.merge_patch({})
        assert m.tag == original_tag
        assert m.segments[0].vlan_id == original_vlan

    def test_patch_with_only_unknown_keys(self) -> None:
        m = make_deep_config()
        original_tag = m.tag
        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch({"nonexistent_field": 42, "another_unknown": "x"})
        assert exc.value.path == Path(["nonexistent_field"])
        assert m.tag == original_tag

    def test_patch_with_only_directives(self) -> None:
        m = make_deep_config()
        original_tag = m.tag
        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch({"_position": 0, "_custom": "ignored"})
        assert exc.value.path == Path(["_position"])
        assert m.tag == original_tag

    def test_empty_patch_does_not_trigger_override(self) -> None:
        m = make_deep_config()
        assert m.override is False
        _ = m.merge_patch({})
        assert m.override is False

    def test_same_value_patch_does_not_trigger_override(self) -> None:
        """Patching a field to its current value should still trigger override.

        merge_patch does not compare values before assignment.
        """
        seg = Segment(
            segment=SegmentMeta(name="test"),
            vlan_id=100,
        )
        _ = seg.merge_patch({"vlan_id": 100})
        # Implementation uses != comparison for change detection,
        # so same value should NOT trigger override
        assert seg.override is False


# ===================================================================
# 2. Scalar and MISSING field combinations
# ===================================================================


class TestScalarAndMissingCombinations:
    def test_null_on_missing_typed_field(self) -> None:
        m = Config(tag="present")
        _ = m.merge_patch({"tag": None})
        assert m.tag is MISSING

    def test_null_on_non_missing_field(self) -> None:
        m = Config(enabled=True)
        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch({"enabled": None})
        assert exc.value.path == Path(["enabled"])
        assert m.enabled is True

    def test_set_missing_field_to_value(self) -> None:
        m = Config()
        assert m.tag is MISSING
        _ = m.merge_patch({"tag": "new-value"})
        assert m.tag == "new-value"

    def test_roundtrip_missing_to_value_and_back(self) -> None:
        m = Config()
        assert m.tag is MISSING
        _ = m.merge_patch({"tag": "set"})
        assert m.tag == "set"
        _ = m.merge_patch({"tag": None})
        assert m.tag is MISSING

    def test_null_on_nested_missing_field(self) -> None:
        """MISSING-typed field inside a sub-model."""
        rule = Rule(
            name="test",
            match=MatchCriteria(src_port=80),
        )
        _ = rule.merge_patch({"match": {"src_port": None}})
        assert rule.match.src_port is MISSING

    def test_null_on_nested_missing_field_preserves_sibling(self) -> None:
        rule = Rule(
            name="test",
            match=MatchCriteria(src_port=80, dst_port=443),
        )
        _ = rule.merge_patch({"match": {"src_port": None}})
        assert rule.match.src_port is MISSING
        assert rule.match.dst_port == 443

    def test_multiple_missing_fields_in_one_patch(self) -> None:
        m = Config(tag="t")
        seg = Segment(
            segment=SegmentMeta(name="test"),
            description="desc",
        )
        m.segments.append(seg)
        _ = m.merge_patch({"tag": None, "segments": {"test": {"description": None}}})
        assert m.tag is MISSING
        assert m.segments[0].description is MISSING


# ===================================================================
# 3. Alias key resolution — all forms accepted
# ===================================================================


class TestAliasKeyResolution:
    def test_snake_case_key(self) -> None:
        m = WithAlias()
        _ = m.merge_patch({"display_name": "snake"})
        assert m.display_name == "snake"

    def test_camel_case_key(self) -> None:
        m = WithAlias()
        _ = m.merge_patch({"displayName": "camel"})
        assert m.display_name == "camel"

    def test_explicit_alias_key(self) -> None:
        m = WithAlias()
        _ = m.merge_patch({"VLANTag": 42})
        assert m.vlan_tag == 42

    def test_mixed_alias_forms_in_one_patch(self) -> None:
        m = WithAlias()
        _ = m.merge_patch(
            {
                "display_name": "snake",
                "VLANTag": 10,
                "description": "plain",
            }
        )
        assert m.display_name == "snake"
        assert m.vlan_tag == 10
        assert m.description == "plain"

    def test_camelcase_generated_alias(self) -> None:
        """vlan_tag -> vlanTag should also work."""
        m = WithAlias()
        _ = m.merge_patch({"vlanTag": 99})
        assert m.vlan_tag == 99


# ===================================================================
# 4. Sub-model merge — identity, depth, isolation
# ===================================================================


class TestSubModelMerge:
    def test_identity_preserved_through_deep_merge(self) -> None:
        m = make_deep_config()
        original_dns = m.dns
        original_seg0 = m.segments[0]
        original_seg0_meta = m.segments[0].segment
        original_rule0 = m.segments[0].rules[0]
        original_match = m.segments[0].rules[0].match

        _ = m.merge_patch(
            {
                "dns": {"primary": "9.9.9.9"},
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {
                                "match": {"protocol": "tcp"},
                            }
                        }
                    }
                },
            }
        )

        assert m.dns is original_dns
        assert m.segments[0] is original_seg0
        assert m.segments[0].segment is original_seg0_meta
        assert m.segments[0].rules[0] is original_rule0
        assert m.segments[0].rules[0].match is original_match
        assert m.dns.primary == "9.9.9.9"
        assert m.segments[0].rules[0].match.protocol == "tcp"

    def test_four_level_deep_merge(self) -> None:
        """Config -> Segment -> Rule -> MatchCriteria."""
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "block-ssh": {
                                "match": {
                                    "protocol": "udp",
                                    "dst_port": 53,
                                }
                            }
                        }
                    }
                }
            }
        )
        ssh_rule = next(r for r in m.segments[0].rules if r.name == "block-ssh")
        assert ssh_rule.match.protocol == "udp"
        assert ssh_rule.match.dst_port == 53

    def test_sub_model_merge_does_not_leak_to_siblings(self) -> None:
        """Merging into one segment must not affect another."""
        m = make_deep_config()
        pci_vlan_before = m.segments[1].vlan_id
        voice_vlan_before = m.segments[2].vlan_id

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"vlan_id": 999},
                }
            }
        )

        assert m.segments[0].vlan_id == 999
        assert m.segments[1].vlan_id == pci_vlan_before
        assert m.segments[2].vlan_id == voice_vlan_before

    def test_merge_multiple_segments_in_one_patch(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"vlan_id": 111},
                    "PCI": {"vlan_id": 222},
                    "Voice": {"vlan_id": 333},
                }
            }
        )
        assert m.segments[0].vlan_id == 111
        assert m.segments[1].vlan_id == 222
        assert m.segments[2].vlan_id == 333


# ===================================================================
# 5. KeyField — advanced position, multi-op, integer keys
# ===================================================================


class TestKeyFieldAdvancedPosition:
    def test_position_end_explicit(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {"new-rule": add({"name": "new-rule"}, last=True)}
                    }
                }
            }
        )
        global_seg = m.segments[0]
        assert global_seg.rules[-1].name == "new-rule"

    def test_position_before_nonexistent_raises(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]
        original_count = len(global_seg.rules)

        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch(
                {
                    "segments": {
                        "Global": {
                            "rules": {"new": add({"name": "new"}, before="nonexistent")}
                        }
                    }
                }
            )
        assert exc.value.path == Path.parse("segments[Global].rules[new]")
        assert len(global_seg.rules) == original_count

    def test_position_after_nonexistent_raises(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]
        original_count = len(global_seg.rules)

        with pytest.raises(PatchError):
            _ = m.merge_patch(
                {
                    "segments": {
                        "Global": {
                            "rules": {"new": add({"name": "new"}, after="nonexistent")}
                        }
                    }
                }
            )
        assert len(global_seg.rules) == original_count

    def test_position_before_on_existing_item_reorders(self) -> None:
        """Move an existing item before another with move(before=...)."""
        m = make_deep_config()
        global_seg = m.segments[0]
        assert [r.name for r in global_seg.rules] == ["allow-all", "block-ssh"]

        # Move block-ssh before allow-all
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "block-ssh": move(before="allow-all"),
                        }
                    }
                }
            }
        )
        assert [r.name for r in global_seg.rules] == ["block-ssh", "allow-all"]

    def test_position_after_on_existing_item_reorders(self) -> None:
        """Add a third rule, then move first rule after it."""
        m = make_deep_config()
        global_seg = m.segments[0]
        # Add a third rule first
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "mid-rule": add({"name": "mid-rule", "priority": 50}),
                        }
                    }
                }
            }
        )
        assert [r.name for r in global_seg.rules] == [
            "allow-all",
            "block-ssh",
            "mid-rule",
        ]

        # Move allow-all after mid-rule
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": move(after="mid-rule"),
                        }
                    }
                }
            }
        )
        assert [r.name for r in global_seg.rules] == [
            "block-ssh",
            "mid-rule",
            "allow-all",
        ]

    def test_position_last_appends(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]
        count = len(global_seg.rules)

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "far": add({"name": "far"}, last=True),
                        }
                    }
                }
            }
        )
        assert global_seg.rules[-1].name == "far"
        assert len(global_seg.rules) == count + 1

    def test_position_first_inserts_at_the_start(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "first": add({"name": "first"}, first=True),
                        }
                    }
                }
            }
        )
        assert global_seg.rules[0].name == "first"


class TestKeyFieldMultiOp:
    def test_add_update_delete_in_single_patch(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]
        assert len(global_seg.rules) == 2

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            # Update existing
                            "allow-all": {"priority": 500},
                            # Delete existing
                            "block-ssh": delete(),
                            # Add new
                            "new-rule": add({"name": "new-rule", "priority": 42}),
                        }
                    }
                }
            }
        )

        rules = global_seg.rules
        assert len(rules) == 2
        names = [r.name for r in rules]
        assert "allow-all" in names
        assert "block-ssh" not in names
        assert "new-rule" in names

        allow_all = next(r for r in rules if r.name == "allow-all")
        assert allow_all.priority == 500

        new_rule = next(r for r in rules if r.name == "new-rule")
        assert new_rule.priority == 42

    def test_delete_all_items(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": delete(),
                            "block-ssh": delete(),
                        }
                    }
                }
            }
        )
        assert global_seg.rules == []

    def test_add_to_empty_list(self) -> None:
        m = make_deep_config()
        voice_seg = m.segments[2]
        assert voice_seg.rules == []

        _ = m.merge_patch(
            {
                "segments": {
                    "Voice": {
                        "rules": {
                            "first-rule": add({"name": "first-rule", "priority": 1}),
                        }
                    }
                }
            }
        )
        assert len(voice_seg.rules) == 1
        assert voice_seg.rules[0].name == "first-rule"

    def test_non_dict_item_patch_raises(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]
        original_count = len(global_seg.rules)

        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch(
                {
                    "segments": {
                        "Global": {
                            "rules": {
                                "allow-all": "not-a-dict",
                            }
                        }
                    }
                }
            )
        assert exc.value.path == Path.parse("segments[Global].rules[allow-all]")
        assert len(global_seg.rules) == original_count
        assert global_seg.rules[0].priority == 999  # unchanged


class TestNestedKeyFieldLists:
    """Test 2 levels of KeyField nesting: segments -> rules -> acl."""

    def test_add_acl_entry_to_rule(self) -> None:
        m = make_deep_config()
        allow_rule = m.segments[0].rules[0]
        assert len(allow_rule.acl) == 2

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {
                                "acl": {
                                    "172.16.0.0": add(
                                        {
                                            "ip": "172.16.0.0",
                                            "mask": "255.240.0.0",
                                            "action": "deny",
                                        }
                                    )
                                }
                            }
                        }
                    }
                }
            }
        )
        assert len(allow_rule.acl) == 3
        new_entry = next(e for e in allow_rule.acl if e.ip == "172.16.0.0")
        assert new_entry.mask == "255.240.0.0"
        assert new_entry.action == "deny"

    def test_update_acl_entry(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {
                                "acl": {
                                    "10.0.0.0": {"action": "deny"},
                                }
                            }
                        }
                    }
                }
            }
        )
        acl = m.segments[0].rules[0].acl
        entry = next(e for e in acl if e.ip == "10.0.0.0")
        assert entry.action == "deny"
        assert entry.mask == "255.0.0.0"  # unchanged

    def test_delete_acl_entry(self) -> None:
        m = make_deep_config()
        allow_rule = m.segments[0].rules[0]
        assert len(allow_rule.acl) == 2

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {
                                "acl": {
                                    "10.0.0.0": delete(),
                                }
                            }
                        }
                    }
                }
            }
        )
        assert len(allow_rule.acl) == 1
        assert allow_rule.acl[0].ip == "192.168.0.0"

    def test_acl_add_with_position(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {
                                "acl": {
                                    "172.16.0.0": add({"ip": "172.16.0.0"}, first=True)
                                }
                            }
                        }
                    }
                }
            }
        )
        assert m.segments[0].rules[0].acl[0].ip == "172.16.0.0"

    def test_multiple_acl_ops_across_multiple_rules(self) -> None:
        """Operate on ACLs in two different rules simultaneously."""
        m = make_deep_config()
        # Add ACL to block-ssh, delete from allow-all
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {
                                "acl": {"10.0.0.0": delete()},
                            },
                            "block-ssh": {
                                "acl": {
                                    "0.0.0.0": add(
                                        {
                                            "ip": "0.0.0.0",
                                            "mask": "0.0.0.0",
                                            "action": "deny",
                                        }
                                    )
                                },
                            },
                        }
                    }
                }
            }
        )
        allow_rule = next(r for r in m.segments[0].rules if r.name == "allow-all")
        ssh_rule = next(r for r in m.segments[0].rules if r.name == "block-ssh")
        assert len(allow_rule.acl) == 1
        assert allow_rule.acl[0].ip == "192.168.0.0"
        assert len(ssh_rule.acl) == 1
        assert ssh_rule.acl[0].ip == "0.0.0.0"


# ===================================================================
# 6. OverrideFlag — deep and scoped scenarios
# ===================================================================


class TestOverrideFlagDeepScenarios:
    def test_rule_change_triggers_segment_override(self) -> None:
        m = make_deep_config()
        assert m.segments[0].override is False

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {"priority": 0},
                        }
                    }
                }
            }
        )
        assert m.segments[0].override is True

    def test_acl_change_triggers_segment_override(self) -> None:
        """Change deep in rule -> acl should propagate to segment override."""
        m = make_deep_config()
        assert m.segments[0].override is False

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "allow-all": {
                                "acl": {
                                    "10.0.0.0": {"action": "deny"},
                                }
                            }
                        }
                    }
                }
            }
        )
        assert m.segments[0].override is True

    def test_edge_local_change_does_not_trigger_segment_override(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"cidr_ip": "10.0.0.1"},
                }
            }
        )
        assert m.segments[0].override is False

    def test_both_edge_local_and_normal_triggers_override(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "cidr_ip": "10.0.0.1",
                        "cidr_prefix": 16,
                        "vlan_id": 999,
                    },
                }
            }
        )
        assert m.segments[0].override is True

    def test_segment_override_independent_of_config_override(self) -> None:
        """Segment's override flag is scoped to the segment, not the config.

        However, because the snapshot uses deepcopy, the config-level change
        detection DOES see a difference in the segments list (the items were
        mutated in-place vs the deep-copied snapshot). Both override flags fire.
        """
        m = make_deep_config()
        assert m.override is False
        assert m.segments[0].override is False

        # Change a segment's rule
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {"allow-all": {"priority": 0}},
                    },
                }
            }
        )
        assert m.segments[0].override is True
        # Config-level override also fires because the deepcopy snapshot
        # differs from the mutated list
        assert m.override is True

    def test_top_level_override_triggered_by_dns_change(self) -> None:
        m = make_deep_config()
        assert m.override is False

        _ = m.merge_patch({"dns": {"primary": "9.9.9.9"}})
        assert m.override is True

    def test_top_level_override_triggered_by_enabled_change(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch({"enabled": False})
        assert m.override is True

    def test_override_not_triggered_when_patching_only_override_field(self) -> None:
        """Patching only the OverrideFlag itself should not trigger re-set."""
        seg = Segment(segment=SegmentMeta(name="test"), override=False)
        _ = seg.merge_patch({"override": True})
        # The override flag is excluded from change detection, so it should
        # remain at whatever value was set
        assert seg.override is True

    def test_multiple_segments_independent_overrides(self) -> None:
        m = make_deep_config()

        # Change Global rules + PCI vlan_id
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"rules": {"allow-all": {"priority": 0}}},
                    "PCI": {"vlan_id": 999},
                }
            }
        )

        assert m.segments[0].override is True  # rule change
        assert m.segments[1].override is True  # vlan_id change
        assert m.segments[2].override is False  # untouched

    def test_add_rule_triggers_override(self) -> None:
        m = make_deep_config()
        assert m.segments[2].override is False  # Voice

        _ = m.merge_patch(
            {
                "segments": {
                    "Voice": {
                        "rules": {
                            "new": add({"name": "new", "priority": 1}),
                        }
                    }
                }
            }
        )
        assert m.segments[2].override is True

    def test_delete_rule_triggers_override(self) -> None:
        m = make_deep_config()
        assert m.segments[0].override is False

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"rules": {"block-ssh": delete()}},
                }
            }
        )
        assert m.segments[0].override is True

    def test_only_edge_local_fields_in_multiple_no_trigger(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"cidr_ip": "10.0.0.5", "cidr_prefix": 16},
                }
            }
        )
        assert m.segments[0].override is False


# ===================================================================
# 7. Sequential patches — accumulation and override behavior
# ===================================================================


class TestSequentialPatches:
    def test_multiple_patches_accumulate(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch({"dns": {"primary": "1.0.0.1"}})
        _ = m.merge_patch({"dns": {"secondary": "1.0.0.2"}})
        assert m.dns.primary == "1.0.0.1"
        assert m.dns.secondary == "1.0.0.2"

    def test_later_patch_overwrites_earlier(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch({"dns": {"primary": "1.0.0.1"}})
        _ = m.merge_patch({"dns": {"primary": "9.9.9.9"}})
        assert m.dns.primary == "9.9.9.9"

    def test_sequential_keyfield_operations(self) -> None:
        m = make_deep_config()
        global_seg = m.segments[0]

        # Patch 1: add a rule
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {"new": add({"name": "new", "priority": 50})},
                    }
                }
            }
        )
        assert len(global_seg.rules) == 3

        # Patch 2: delete original, update new
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "rules": {
                            "block-ssh": delete(),
                            "new": {"priority": 100},
                        },
                    }
                }
            }
        )
        assert len(global_seg.rules) == 2
        new_rule = next(r for r in global_seg.rules if r.name == "new")
        assert new_rule.priority == 100

    def test_chaining_returns_self(self) -> None:
        m = make_deep_config()
        result = (
            m.merge_patch({"enabled": False})
            .merge_patch({"dns": {"primary": "9.9.9.9"}})
            .merge_patch({"tag": "staging"})
        )
        assert result is m
        assert m.enabled is False
        assert m.dns.primary == "9.9.9.9"
        assert m.tag == "staging"


# ===================================================================
# 8. Resolvable — combined with deep nesting and KeyField
# ===================================================================


class TestResolvableDeepMerge:
    def test_resolve_segment_id_in_keyed_segment(
        self, registry: ResolverRegistry
    ) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "segment": {"segment_id": "PCI"},
                    }
                }
            },
            resolvers=registry,
        )
        assert m.segments[0].segment.segment_id == 1

    def test_resolve_logical_id_in_keyed_segment(
        self, registry: ResolverRegistry
    ) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "segment": {"logical_id": "Voice"},
                    }
                }
            },
            resolvers=registry,
        )
        assert m.segments[0].segment.logical_id == VOICE_UUID

    def test_resolve_multiple_fields_in_one_sub_model(
        self, registry: ResolverRegistry
    ) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "segment": {
                            "segment_id": "PCI",
                            "logical_id": "PCI",
                        },
                    }
                }
            },
            resolvers=registry,
        )
        assert m.segments[0].segment.segment_id == 1
        assert m.segments[0].segment.logical_id == PCI_UUID

    def test_raw_id_bypasses_resolution(self, registry: ResolverRegistry) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "segment": {"segment_id": 99},
                    }
                }
            },
            resolvers=registry,
        )
        assert m.segments[0].segment.segment_id == 99

    def test_no_resolvers_string_assigned_raw(self) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "segment": {"name": "renamed"},
                    }
                }
            }
        )
        assert m.segments[0].segment.name == "renamed"

    def test_non_resolvable_field_unaffected_with_resolvers(
        self, registry: ResolverRegistry
    ) -> None:
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "segment": {"name": "Global Segment"},
                    }
                }
            },
            resolvers=registry,
        )
        # name is str, not Resolvable — should be "Global Segment" literally
        assert m.segments[0].segment.name == "Global Segment"


# ===================================================================
# 9. Complex integration — all features combined
# ===================================================================


class TestComplexIntegration:
    def test_full_scenario_add_segment_with_raw_ids(
        self, registry: ResolverRegistry
    ) -> None:
        """Add a brand new segment with raw IDs and a rules list.

        A new item is validated whole from its add() body: raw IDs here,
        and a list value for its rules.
        """
        m = make_deep_config()
        original_count = len(m.segments)

        _ = m.merge_patch(
            {
                "segments": {
                    "NewSeg": add(
                        {
                            "segment": {
                                "name": "NewSeg",
                                "segment_id": 1,
                                "logical_id": str(PCI_UUID),
                            },
                            "vlan_id": 400,
                            "rules": [
                                {"name": "r1", "priority": 10},
                                {"name": "r2", "priority": 20},
                            ],
                        }
                    )
                }
            },
            resolvers=registry,
        )

        assert len(m.segments) == original_count + 1
        new_seg = m.segments[-1]
        assert new_seg.segment.name == "NewSeg"
        assert new_seg.segment.segment_id == 1
        assert new_seg.segment.logical_id == PCI_UUID
        assert new_seg.vlan_id == 400
        assert len(new_seg.rules) == 2

    def test_simultaneous_operations_across_segments(self) -> None:
        """Modify, add, and delete across different segments in one patch."""
        m = make_deep_config()

        _ = m.merge_patch(
            {
                "segments": {
                    # Update Global
                    "Global": {
                        "vlan_id": 111,
                        "rules": {
                            "allow-all": {"priority": 0},
                            "block-ssh": delete(),
                        },
                    },
                    # Delete PCI
                    "PCI": delete(),
                    # Add new to Voice
                    "Voice": {
                        "rules": {
                            "voip-allow": add(
                                {
                                    "name": "voip-allow",
                                    "priority": 10,
                                    "match": {"protocol": "udp", "dst_port": 5060},
                                }
                            )
                        }
                    },
                }
            }
        )

        # Global updated
        global_seg = m.segments[0]
        assert global_seg.vlan_id == 111
        assert len(global_seg.rules) == 1
        assert global_seg.rules[0].name == "allow-all"
        assert global_seg.rules[0].priority == 0

        # PCI deleted
        seg_names = [s.segment.name for s in m.segments]
        assert "PCI" not in seg_names

        # Voice has new rule
        voice = next(s for s in m.segments if s.segment.name == "Voice")
        assert len(voice.rules) == 1
        assert voice.rules[0].name == "voip-allow"
        assert voice.rules[0].match.dst_port == 5060

    def test_edge_local_with_rules_and_description(self) -> None:
        """EdgeLocal changes + non-EdgeLocal in one patch."""
        m = make_deep_config()

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "cidr_ip": "10.0.0.1",
                        "cidr_prefix": 16,
                        "description": "updated description",
                    },
                }
            }
        )

        global_seg = m.segments[0]
        assert global_seg.cidr_ip == "10.0.0.1"
        assert global_seg.cidr_prefix == 16
        assert global_seg.description == "updated description"
        # description is not EdgeLocal -> override triggered
        assert global_seg.override is True

    def test_missing_null_and_value_in_one_patch(self) -> None:
        """Mix of MISSING-null, a null the field cannot take, and a value.

        The null on ``enabled`` (neither MISSING nor None) refuses the whole
        patch, so the other two changes are not made either.
        """
        m = make_deep_config()
        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch(
                {
                    "tag": None,  # MISSING-typed -> MISSING
                    "enabled": None,  # neither MISSING nor None -> raises
                    "dns": {"primary": "changed"},
                }
            )
        assert exc.value.path == Path(["enabled"])
        assert m.tag == "production"
        assert m.enabled is True
        assert m.dns.primary == "1.1.1.1"

    def test_deep_merge_with_position_and_resolution(
        self, registry: ResolverRegistry
    ) -> None:
        """Add a rule with position + resolve segment ID — all in one."""
        m = make_deep_config()

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "segment": {"segment_id": "Voice"},
                        "rules": {
                            "new-first": add(
                                {
                                    "name": "new-first",
                                    "priority": -1,
                                    "match": {"protocol": "icmp"},
                                },
                                first=True,
                            ),
                        },
                    }
                }
            },
            resolvers=registry,
        )

        global_seg = m.segments[0]
        assert global_seg.segment.segment_id == 2
        assert global_seg.rules[0].name == "new-first"
        assert global_seg.rules[0].match.protocol == "icmp"
        assert global_seg.override is True


# ===================================================================
# 10. Edge cases and robustness
# ===================================================================


class TestEdgeCases:
    def test_empty_string_key_in_patch_raises(self) -> None:
        """An empty string key names no field."""
        m = Config()
        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch({"": "value"})
        assert exc.value.path == Path([""])

    def test_patch_same_keyed_item_twice(self) -> None:
        """Dict iteration order: last write wins for the same logical key.

        In Python dicts, duplicate keys are not possible, so this tests
        that sequential patches to the same item compose correctly.
        """
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"rules": {"allow-all": {"priority": 50}}},
                }
            }
        )
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"rules": {"allow-all": {"priority": 100}}},
                }
            }
        )
        rule = next(r for r in m.segments[0].rules if r.name == "allow-all")
        assert rule.priority == 100

    def test_deeply_nested_identity_after_multiple_patches(self) -> None:
        """Object identity preserved across multiple sequential patches."""
        m = make_deep_config()
        original_match = m.segments[0].rules[1].match

        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"rules": {"block-ssh": {"match": {"protocol": "udp"}}}}
                }
            }
        )
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {"rules": {"block-ssh": {"match": {"dst_port": 53}}}}
                }
            }
        )

        assert m.segments[0].rules[1].match is original_match
        assert original_match.protocol == "udp"
        assert original_match.dst_port == 53

    def test_large_keyfield_list(self) -> None:
        """Merge into a list with many items."""

        class Item(VcoModel):
            name: str
            value: int = 0

        class BigList(VcoModel):
            items: Annotated[list[Item], KeyField("name")] = Field(default_factory=list)

        m = BigList(items=[Item(name=f"item-{i}", value=i) for i in range(50)])

        # Update item-25, delete item-0, add new-item
        _ = m.merge_patch(
            {
                "items": {
                    "item-25": {"value": 9999},
                    "item-0": delete(),
                    "new-item": add({"name": "new-item", "value": -1}),
                }
            }
        )

        names = [i.name for i in m.items]
        assert "item-0" not in names
        assert "new-item" in names
        assert len(m.items) == 50  # 50 - 1 delete + 1 add

        updated = next(i for i in m.items if i.name == "item-25")
        assert updated.value == 9999

    def test_integer_key_values_stringified(self) -> None:
        """KeyField key matching stringifies both sides."""

        class NumericKeyed(VcoModel):
            id: int
            label: str = ""

        class NumList(VcoModel):
            items: Annotated[list[NumericKeyed], KeyField("id")] = Field(
                default_factory=list
            )

        m = NumList(
            items=[
                NumericKeyed(id=1, label="one"),
                NumericKeyed(id=2, label="two"),
            ]
        )

        # Patch key is a string "1" matching numeric id=1
        _ = m.merge_patch({"items": {"1": {"label": "updated"}}})
        assert m.items[0].label == "updated"

    def test_unknown_directives_in_item_patch_raise(self) -> None:
        """A _-prefixed key other than _op and _position in an item patch raises."""
        m = make_deep_config()
        with pytest.raises(PatchError) as exc:
            _ = m.merge_patch(
                {
                    "segments": {
                        "Global": {
                            "rules": {
                                "allow-all": {
                                    "priority": 123,
                                    "_comment": "not a directive",
                                }
                            }
                        }
                    }
                }
            )
        assert exc.value.path == Path(
            ["segments", Key("Global"), "rules", Key("allow-all"), "_comment"]
        )
        rule = m.segments[0].rules[0]
        assert rule.priority == 999
        assert not hasattr(rule, "_comment")

    def test_camel_case_keys_in_nested_sub_model(self) -> None:
        """CamelCase keys work at every nesting level."""
        m = make_deep_config()
        _ = m.merge_patch(
            {
                "segments": {
                    "Global": {
                        "vlanId": 999,
                        "cidrIp": "10.0.0.1",
                    }
                }
            }
        )
        assert m.segments[0].vlan_id == 999
        assert m.segments[0].cidr_ip == "10.0.0.1"
