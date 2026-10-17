"""Tests for QoS data model annotations."""

from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import QosRuleMatch
from pyvelo.client.models.configuration.qos.segment import QosSegment
from pyvelo.core.fields import get_key_field, get_resolvable


class TestQosKeyFields:
    def test_segments_keyed(self):
        assert get_key_field(QosData.model_fields["segments"]) == "segment.name"

    def test_rules_keyed(self):
        assert get_key_field(QosSegment.model_fields["rules"]) == "name"

    def test_defaults_keyed(self):
        assert get_key_field(QosSegment.model_fields["defaults"]) == "name"


class TestQosResolvable:
    def test_application_id(self):
        r = get_resolvable(QosRuleMatch.model_fields["application_id"])
        assert r is not None
        assert r.id_attr == "id"

    def test_source_address_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["source_address_group"])
        assert r is not None
        assert r.id_attr == "logical_id"

    def test_destination_address_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["destination_address_group"])
        assert r is not None

    def test_source_port_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["source_port_group"])
        assert r is not None

    def test_destination_port_group(self):
        r = get_resolvable(QosRuleMatch.model_fields["destination_port_group"])
        assert r is not None
