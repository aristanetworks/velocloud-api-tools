"""Tests for firewall module markers (KeyField) and the absence of an override flag."""

import pytest
from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rule_action import OutboundRuleAction
from pyvelo.client.models.configuration.firewall.rule_match import OutboundRuleMatch
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.core import ConfigContext, KeySpec, add, delete
from pyvelo.core.fields import get_key_spec, has_override_flag
from pyvelo.core.layers import Edge


def _make_segment(
    name: str = "Global Segment", segment_id: int = 0
) -> FirewallSegment[Edge]:
    return FirewallSegment[Edge](
        segment=SegmentMetadata(
            name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
        ),
        outbound=[],
    )


def _make_rule(name: str = "test-rule") -> FirewallOutboundRule:
    return FirewallOutboundRule(
        name=name,
        action=OutboundRuleAction(allow_or_deny="deny"),
        match=OutboundRuleMatch(
            proto=6, destination_port_low=22, destination_port_high=22
        ),
    )


class TestKeyFieldMarkers:
    def test_firewall_data_segments_has_key_field(self) -> None:
        """FirewallData.segments should be keyed by segment.name, alt key segment.segment_id."""
        fi = FirewallData.model_fields["segments"]
        assert get_key_spec(fi) == KeySpec(("segment.name",), alt="segment.segment_id")

    def test_firewall_segment_outbound_has_key_field(self) -> None:
        """FirewallSegment.outbound should be keyed by name, alt key rule_logical_id."""
        fi = FirewallSegment.model_fields["outbound"]
        assert get_key_spec(fi) == KeySpec(("name",), alt="rule_logical_id")

    def test_firewall_data_inbound_has_key_field(self) -> None:
        """FirewallData.inbound should be keyed by name, alt key rule_logical_id."""
        fi = FirewallData.model_fields["inbound"]
        assert get_key_spec(fi) == KeySpec(("name",), alt="rule_logical_id")


class TestNoOverrideFlag:
    """A firewall segment has no outbound override flag: the VCO has none."""

    def test_firewall_segment_declares_no_override_flag(self) -> None:
        flagged = [
            name
            for name, fi in FirewallSegment.model_fields.items()
            if has_override_flag(fi)
        ]
        assert flagged == []

    def test_firewall_segment_has_no_outbound_override_field(self) -> None:
        assert "outbound_override" not in FirewallSegment.model_fields
        assert "outbound_override" not in _make_segment().model_dump()
        assert "outboundOverride" not in _make_segment().model_dump(by_alias=True)


class TestMergeWithMarkers:
    def test_keyed_merge_adds_rule(self) -> None:
        """Merging a rule into outbound by key should add the rule."""
        seg = _make_segment()
        seg.outbound.append(_make_rule("existing-rule"))

        fw = FirewallData[Edge](segments=[seg])
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {
                            "new-rule": add(
                                {
                                    "name": "new-rule",
                                    "action": {"allow_or_deny": "allow"},
                                    "match": {"proto": 17},
                                }
                            )
                        }
                    }
                }
            }
        )

        segment = fw.get_segment(name="Global Segment")
        assert len(segment.outbound) == 2
        names = [r.name for r in segment.outbound]
        assert "existing-rule" in names
        assert "new-rule" in names

    def test_keyed_merge_deletes_rule(self) -> None:
        """delete() under a key should delete the rule."""
        seg = _make_segment()
        seg.outbound.append(_make_rule("delete-me"))
        seg.outbound.append(_make_rule("keep-me"))

        fw = FirewallData[Edge](segments=[seg])
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {
                            "delete-me": delete(),
                        }
                    }
                }
            }
        )

        segment = fw.get_segment(name="Global Segment")
        assert len(segment.outbound) == 1
        assert segment.outbound[0].name == "keep-me"

    def test_outbound_change_sets_no_override_flag(self) -> None:
        """Modifying outbound rules changes the rules and nothing else."""
        seg = _make_segment()
        before = seg.model_dump(mode="json", by_alias=True)

        _ = seg.merge_patch(
            {
                "outbound": {
                    "block-ssh": add(
                        {
                            "name": "block-ssh",
                            "action": {"allow_or_deny": "deny"},
                            "match": {"proto": 6, "dport_low": 22},
                        }
                    )
                }
            }
        )

        after = seg.model_dump(mode="json", by_alias=True)
        assert {k for k in after if after[k] != before.get(k)} == {"outbound"}
        assert "outboundOverride" not in after


class TestOutboundRuleMatchKeepsItsConfigLevel:
    """``OutboundRuleMatch.model_post_init`` calls up, so the level is not dropped."""

    @pytest.mark.parametrize("version", ["IPv4", "IPv6", "IPv4v6"])
    @pytest.mark.parametrize("level", [ConfigContext.EDGE, ConfigContext.PROFILE])
    def test_a_match_keeps_the_config_level_it_was_validated_at(
        self, version: str, level: ConfigContext
    ) -> None:
        match = OutboundRuleMatch.model_validate(
            {"ipVersion": version}, context={"config_level": level}
        )
        assert match.config_context is level

    def test_a_match_validated_without_a_level_has_none(self) -> None:
        assert OutboundRuleMatch.model_validate({}).config_context is None
