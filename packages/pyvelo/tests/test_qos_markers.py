"""Tests for QoS module markers: KeyField, OverrideFlag, Resolvable."""

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import QosRuleMatch
from pyvelo.client.models.configuration.qos.segment import CosMapping, QosSegment
from pyvelo.core.fields import get_key_field, get_resolvable, has_override_flag


def _seg_meta(name: str = "Global Segment", segment_id: int = 0) -> SegmentMetadata:
    return SegmentMetadata(
        name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
    )


class TestQosKeyFields:
    def test_segments_keyed_by_name(self):
        """QosData.segments should be keyed by segment.name."""
        key = get_key_field(QosData.model_fields["segments"])
        assert key == "segment.name"

    def test_rules_keyed_by_name(self):
        """QosSegment.rules should be keyed by name."""
        key = get_key_field(QosSegment.model_fields["rules"])
        assert key == "name"


class TestQosOverrideFlag:
    def test_cos_mapping_override(self):
        """CosMapping.override should be marked with OverrideFlag."""
        assert has_override_flag(CosMapping.model_fields["override"])


class TestQosResolvable:
    def test_application_id_resolvable(self):
        """QosRuleMatch.application_id should be Resolvable."""
        r = get_resolvable(QosRuleMatch.model_fields["application_id"])
        assert r is not None
        assert r.id_attr == "id"


class TestQosMerge:
    def test_merge_rule_into_segment(self):
        """Merge a rule into a QoS segment by name."""
        seg = QosSegment(
            segment=_seg_meta(),
            rules=[],
        )
        data = QosData(segments=[seg])

        data.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "rules": {
                            "new-rule": {
                                "name": "new-rule",
                                "match": {},
                                "action": {
                                    "route_type": "edge2Any",
                                    "edge2EdgeRouteAction": {"interface": "auto"},
                                    "edge2DataCenterRouteAction": {"interface": "auto"},
                                    "edge2CloudRouteAction": {"interface": "auto"},
                                    "QoS": {"type": "transactional"},
                                },
                            }
                        }
                    }
                }
            }
        )

        assert len(data.segments[0].rules) == 1
        assert data.segments[0].rules[0].name == "new-rule"
