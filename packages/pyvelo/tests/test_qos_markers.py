"""Tests for QoS module markers: KeyField, the cosMapping flag, Resolvable."""

from pyvelo.client.enums.enterprise import NetworkSegmentType
from pyvelo.client.models.configuration.common import SegmentMetadata
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import QosRuleMatch
from pyvelo.client.models.configuration.qos.segment import CosMapping, QosSegment
from pyvelo.core import ByFlag, Edge, KeySpec, add, field_kind
from pyvelo.core.fields import get_key_spec, get_resolvable, has_override_flag


def _seg_meta(name: str = "Global Segment", segment_id: int = 0) -> SegmentMetadata:
    return SegmentMetadata(
        name=name, segment_id=segment_id, type_=NetworkSegmentType.REGULAR
    )


class TestQosKeyFields:
    def test_segments_keyed_by_name(self) -> None:
        """QosData.segments should be keyed by segment.name, alt key segment.segment_id."""
        assert get_key_spec(QosData.model_fields["segments"]) == KeySpec(
            ("segment.name",), alt="segment.segment_id"
        )

    def test_rules_keyed_by_name(self) -> None:
        """QosSegment.rules should be keyed by name, alt key rule_logical_id."""
        assert get_key_spec(QosSegment.model_fields["rules"]) == KeySpec(
            ("name",), alt="rule_logical_id"
        )


class TestQosCosMappingFlag:
    def test_cos_mapping_override_is_the_flag_of_its_encoding(self) -> None:
        """CosMapping.override is a plain field; the segment's cos_mapping is ByFlag on it."""
        assert not has_override_flag(CosMapping.model_fields["override"])
        found = field_kind(QosSegment[Edge].model_fields["cos_mapping"])
        assert found.encoding == ByFlag("override", presence_wins=True)


class TestQosResolvable:
    def test_application_id_resolvable(self) -> None:
        """QosRuleMatch.application_id should be Resolvable."""
        r = get_resolvable(QosRuleMatch.model_fields["application_id"])
        assert r is not None
        assert r.attribute == "id"


class TestQosMerge:
    def test_merge_rule_into_segment(self) -> None:
        """Merge a rule into a QoS segment by name."""
        seg = QosSegment[Edge](
            segment=_seg_meta(),
            rules=[],
        )
        data = QosData[Edge](segments=[seg])

        _ = data.merge_patch(
            {
                "segments": {
                    "Global Segment": {
                        "rules": {
                            "new-rule": add(
                                {
                                    "name": "new-rule",
                                    "match": {},
                                    "action": {
                                        "route_type": "edge2Any",
                                        "edge2EdgeRouteAction": {"interface": "auto"},
                                        "edge2DataCenterRouteAction": {
                                            "interface": "auto"
                                        },
                                        "edge2CloudRouteAction": {"interface": "auto"},
                                        "QoS": {"type": "transactional"},
                                    },
                                }
                            )
                        }
                    }
                }
            }
        )

        assert len(data.segments[0].rules) == 1
        assert data.segments[0].rules[0].name == "new-rule"
