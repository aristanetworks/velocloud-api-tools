"""Tests for firewall data model annotations."""

from pyvelo.client.models.configuration.firewall.root import FirewallData
from pyvelo.client.models.configuration.firewall.rule_match import OutboundRuleMatch
from pyvelo.client.models.configuration.firewall.segment import FirewallSegment
from pyvelo.core.fields import (
    get_key_field,
    get_resolvable,
)


class TestFirewallKeyFields:
    def test_segments_has_keyfield(self):
        fi = FirewallData.model_fields["segments"]
        assert get_key_field(fi) == "segment.name"

    def test_inbound_has_keyfield(self):
        fi = FirewallData.model_fields["inbound"]
        assert get_key_field(fi) == "name"

    def test_inbound_v6_has_keyfield(self):
        fi = FirewallData.model_fields["inbound_v6"]
        assert get_key_field(fi) == "name"

    def test_outbound_has_keyfield(self):
        fi = FirewallSegment.model_fields["outbound"]
        assert get_key_field(fi) == "name"


class TestFirewallResolvable:
    def test_application_id_resolvable(self):
        fi = OutboundRuleMatch.model_fields["application_id"]
        r = get_resolvable(fi)
        assert r is not None
        assert r.id_attr == "id"

    def test_source_address_group_resolvable(self):
        fi = OutboundRuleMatch.model_fields["source_address_group"]
        r = get_resolvable(fi)
        assert r is not None
        assert r.id_attr == "logical_id"

    def test_destination_address_group_resolvable(self):
        fi = OutboundRuleMatch.model_fields["destination_address_group"]
        r = get_resolvable(fi)
        assert r is not None
        assert r.id_attr == "logical_id"

    def test_source_port_group_resolvable(self):
        fi = OutboundRuleMatch.model_fields["source_port_group"]
        r = get_resolvable(fi)
        assert r is not None

    def test_destination_port_group_resolvable(self):
        fi = OutboundRuleMatch.model_fields["destination_port_group"]
        r = get_resolvable(fi)
        assert r is not None
