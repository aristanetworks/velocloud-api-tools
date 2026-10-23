"""Tests for firewall module markers (KeyField, OverrideFlag)."""

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rule_action import OutboundRuleAction
from pyvelo.client.models.configuration.firewall.rule_match import OutboundRuleMatch
from pyvelo.client.models.configuration.firewall.rules import FirewallOutboundRule
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.core.fields import get_key_field, has_override_flag


def _make_segment(name: str = "Global Segment", segment_id: int = 0) -> FirewallSegment:
    return FirewallSegment(
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
    def test_firewall_data_segments_has_key_field(self):
        """FirewallData.segments should have KeyField('segment.name')."""
        fi = FirewallData.model_fields["segments"]
        key = get_key_field(fi)
        assert key == "segment.name"

    def test_firewall_segment_outbound_has_key_field(self):
        """FirewallSegment.outbound should have KeyField('name')."""
        fi = FirewallSegment.model_fields["outbound"]
        key = get_key_field(fi)
        assert key == "name"

    def test_firewall_data_inbound_has_key_field(self):
        """FirewallData.inbound should have KeyField('name')."""
        fi = FirewallData.model_fields["inbound"]
        key = get_key_field(fi)
        assert key == "name"


class TestOverrideFlagMarkers:
    def test_firewall_segment_has_override_flag(self):
        """FirewallSegment should have an OverrideFlag field."""
        fi = FirewallSegment.model_fields["outbound_override"]
        assert has_override_flag(fi)

    def test_override_flag_default_false(self):
        """OverrideFlag fields should default to False."""
        seg = _make_segment()
        assert seg.outbound_override is False


class TestMergeWithMarkers:
    def test_keyed_merge_adds_rule(self):
        """Merging a rule into outbound by key should add the rule."""
        seg = _make_segment()
        seg.outbound.append(_make_rule("existing-rule"))

        fw = FirewallData(segments=[seg])
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {
                            "new-rule": {
                                "name": "new-rule",
                                "action": {"allow_or_deny": "allow"},
                                "match": {"proto": 17},
                            }
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

    def test_keyed_merge_deletes_rule(self):
        """Setting a key to None should delete the rule."""
        seg = _make_segment()
        seg.outbound.append(_make_rule("delete-me"))
        seg.outbound.append(_make_rule("keep-me"))

        fw = FirewallData(segments=[seg])
        _ = fw.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "outbound": {
                            "delete-me": None,
                        }
                    }
                }
            }
        )

        segment = fw.get_segment(name="Global Segment")
        assert len(segment.outbound) == 1
        assert segment.outbound[0].name == "keep-me"

    def test_override_flag_auto_set_on_outbound_change(self):
        """Modifying outbound rules should auto-set the override flag."""
        seg = _make_segment()
        assert seg.outbound_override is False

        _ = seg.merge_patch(
            {
                "outbound": {
                    "block-ssh": {
                        "name": "block-ssh",
                        "action": {"allow_or_deny": "deny"},
                        "match": {"proto": 6, "dport_low": 22},
                    }
                }
            }
        )

        assert seg.outbound_override is True
