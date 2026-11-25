"""Tests for QoS data model annotations."""

from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import QosRuleMatch
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.core import KeySpec
from pyvelo.core.fields import get_key_spec, get_resolvable


class TestQosKeyFields:
    def test_segments_keyed(self):
        assert get_key_spec(QosData.model_fields["segments"]) == KeySpec(
            ("segment.name",), alt="segment.segment_id"
        )

    def test_rules_keyed(self):
        assert get_key_spec(QosSegment.model_fields["rules"]) == KeySpec(
            ("name",), alt="rule_logical_id"
        )

    def test_defaults_keyed(self):
        assert get_key_spec(QosSegment.model_fields["defaults"]) == KeySpec(
            ("name",), alt="rule_logical_id"
        )


class TestQosResolvable:
    def test_application_id(self):
        r = get_resolvable(QosRuleMatch.model_fields["application_id"])
        assert r is not None
        assert r.attribute == "id"

    def test_source_address_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["source_address_group"])
        assert r is not None
        assert r.attribute == "logical_id"

    def test_destination_address_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["destination_address_group"])
        assert r is not None

    def test_source_port_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["source_port_group"])
        assert r is not None

    def test_destination_port_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["destination_port_group"])
        assert r is not None
