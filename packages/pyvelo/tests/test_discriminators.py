"""Tests for discriminated union functions."""

from pydantic import TypeAdapter
from pyvelo.client.models.enterprise.object_group import (
    AddressGroupEntry,
    DomainAddressEntry,
    IcmpPortEntry,
    IpAddressEntry,
    PortGroupEntry,
    TcpUdpPortEntry,
    _address_entry_discriminator,
    _port_entry_discriminator,
)
from pyvelo.client.schemas.edge_filters import (
    EdgeActivationStateFilter,
    EdgeDateNullFilter,
    EdgeDateValueFilter,
    EdgeHaStateFilter,
    EdgeStateFilter,
    EdgeStringNullFilter,
    EdgeStringValueFilter,
    _edge_filter_discriminator,
    _edge_filters_discriminator,
    _edge_nullable_filter_discriminator,
)
from pyvelo.core.base import (
    _api_filters_discriminator,
)


class TestApiFiltersDiscriminator:
    def test_dict_with_and_key(self):
        assert (
            _api_filters_discriminator(
                {"and": [{"field": "x", "operator": "is", "value": "1"}]}
            )
            == "and"
        )

    def test_dict_with_and_underscore_key(self):
        assert (
            _api_filters_discriminator(
                {"and_": [{"field": "x", "operator": "is", "value": "1"}]}
            )
            == "and"
        )

    def test_dict_with_or_key(self):
        assert (
            _api_filters_discriminator(
                {"or": [{"field": "x", "operator": "is", "value": "1"}]}
            )
            == "or"
        )

    def test_dict_with_or_underscore_key(self):
        assert (
            _api_filters_discriminator(
                {"or_": [{"field": "x", "operator": "is", "value": "1"}]}
            )
            == "or"
        )

    def test_dict_with_field_key(self):
        assert (
            _api_filters_discriminator(
                {"field": "name", "operator": "is", "value": "test"}
            )
            == "filter"
        )

    def test_non_dict_returns_none(self):
        assert _api_filters_discriminator("not_a_dict") is None


class TestAddressEntryDiscriminator:
    def test_dict_with_domain(self):
        assert (
            _address_entry_discriminator(
                {"domain": "example.com", "entry_type": "domain"}
            )
            == "domain"
        )

    def test_dict_with_ip(self):
        assert (
            _address_entry_discriminator(
                {"ip": "10.0.0.1", "rule_type": "exact", "mask": "255.255.255.255"}
            )
            == "ip"
        )

    def test_dict_with_both_prefers_domain(self):
        """If both domain and ip are present, domain takes precedence."""
        assert (
            _address_entry_discriminator({"domain": "example.com", "ip": "10.0.0.1"})
            == "domain"
        )

    def test_object_with_domain_attr(self):
        entry = DomainAddressEntry(domain="example.com")
        assert _address_entry_discriminator(entry) == "domain"

    def test_object_without_domain_returns_ip(self):
        entry = IpAddressEntry(ip="10.0.0.1", rule_type="exact", mask="255.255.255.255")
        assert _address_entry_discriminator(entry) == "ip"


class TestPortEntryDiscriminator:
    def test_dict_with_port_low(self):
        assert (
            _port_entry_discriminator({"port_low": 80, "port_high": 443, "proto": 6})
            == "port"
        )

    def test_dict_with_type(self):
        assert (
            _port_entry_discriminator(
                {"type": 8, "code_low": 0, "code_high": 0, "proto": 1}
            )
            == "icmp"
        )

    def test_dict_with_code_low(self):
        assert (
            _port_entry_discriminator({"code_low": 0, "code_high": 0, "proto": 1})
            == "icmp"
        )

    def test_object_with_port_low(self):
        entry = TcpUdpPortEntry(proto=6, port_low=80, port_high=443)
        assert _port_entry_discriminator(entry) == "port"

    def test_object_without_port_low(self):
        entry = IcmpPortEntry(proto=1, type_=8, code_low=0, code_high=0)
        assert _port_entry_discriminator(entry) == "icmp"


class TestAddressGroupEntryUnion:
    """ObjectGroupEntryBase uses raw BaseModel with snake_case fields (no alias_generator)."""

    def test_parse_ip_entry(self):
        ta = TypeAdapter(AddressGroupEntry)
        data = {
            "entry_type": "ip",
            "ip": "10.0.0.0",
            "rule_type": "prefix",
            "mask": "255.255.255.0",
        }
        result = ta.validate_python(data)
        assert isinstance(result, IpAddressEntry)

    def test_parse_domain_entry(self):
        ta = TypeAdapter(AddressGroupEntry)
        data = {"entry_type": "domain", "domain": "example.com"}
        result = ta.validate_python(data)
        assert isinstance(result, DomainAddressEntry)


class TestPortGroupEntryUnion:
    """ObjectGroupEntryBase uses raw BaseModel with snake_case fields (no alias_generator)."""

    def test_parse_port_entry(self):
        ta = TypeAdapter(PortGroupEntry)
        data = {"entry_type": "port", "proto": 6, "port_low": 80, "port_high": 443}
        result = ta.validate_python(data)
        assert isinstance(result, TcpUdpPortEntry)

    def test_parse_icmp_entry(self):
        ta = TypeAdapter(PortGroupEntry)
        data = {
            "entry_type": "icmp",
            "proto": 1,
            "type": 8,
            "code_low": 0,
            "code_high": 0,
        }
        result = ta.validate_python(data)
        assert isinstance(result, IcmpPortEntry)


class TestEdgeNullableFilterDiscriminator:
    def test_dict_value_operator(self):
        assert (
            _edge_nullable_filter_discriminator(
                {"field": "name", "operator": "is", "value": "x"}
            )
            == "value"
        )

    def test_dict_null_operator(self):
        assert (
            _edge_nullable_filter_discriminator({"field": "name", "operator": "isNull"})
            == "null"
        )

    def test_dict_op_alias(self):
        assert (
            _edge_nullable_filter_discriminator({"field": "name", "op": "isNotNull"})
            == "null"
        )

    def test_instance_null(self):
        f = EdgeStringNullFilter(field="name", operator="isNull")
        assert _edge_nullable_filter_discriminator(f) == "null"

    def test_instance_value(self):
        f = EdgeStringValueFilter(field="name", operator="is", value="x")
        assert _edge_nullable_filter_discriminator(f) == "value"

    def test_date_null_instance(self):
        f = EdgeDateNullFilter(field="created", operator="isNull")
        assert _edge_nullable_filter_discriminator(f) == "null"


class TestEdgeFilterDiscriminator:
    def test_dict_activation_state(self):
        assert (
            _edge_filter_discriminator(
                {"field": "activationState", "operator": "is", "value": "ACTIVATED"}
            )
            == "activationState"
        )

    def test_dict_edge_state(self):
        assert (
            _edge_filter_discriminator(
                {"field": "edgeState", "operator": "is", "value": "CONNECTED"}
            )
            == "edgeState"
        )

    def test_dict_ha_state(self):
        assert (
            _edge_filter_discriminator(
                {"field": "haState", "operator": "is", "value": "FAILED"}
            )
            == "haState"
        )

    def test_instance_ha_state(self):
        f = EdgeHaStateFilter.model_validate(
            {"field": "haState", "operator": "is", "value": "FAILED"}
        )
        assert _edge_filter_discriminator(f) == "haState"

    def test_dict_string_field(self):
        assert (
            _edge_filter_discriminator(
                {"field": "name", "operator": "contains", "value": "dc"}
            )
            == "string"
        )

    def test_dict_date_field(self):
        assert (
            _edge_filter_discriminator(
                {"field": "created", "operator": "is", "value": "2024-01-01"}
            )
            == "date"
        )

    def test_dict_fieldname_alias(self):
        assert (
            _edge_filter_discriminator(
                {"fieldName": "edgeState", "operator": "is", "value": "CONNECTED"}
            )
            == "edgeState"
        )

    def test_instance_activation_state(self):
        f = EdgeActivationStateFilter.model_validate(
            {"field": "activationState", "operator": "is", "value": "ACTIVATED"}
        )
        assert _edge_filter_discriminator(f) == "activationState"

    def test_instance_edge_state(self):
        f = EdgeStateFilter.model_validate(
            {"field": "edgeState", "operator": "is", "value": "CONNECTED"}
        )
        assert _edge_filter_discriminator(f) == "edgeState"

    def test_instance_string_value(self):
        f = EdgeStringValueFilter(field="name", operator="is", value="x")
        assert _edge_filter_discriminator(f) == "string"

    def test_instance_string_null(self):
        f = EdgeStringNullFilter(field="name", operator="isNull")
        assert _edge_filter_discriminator(f) == "string"

    def test_instance_date_value(self):
        f = EdgeDateValueFilter(field="created", operator="is", value="2024-01-01")
        assert _edge_filter_discriminator(f) == "date"

    def test_instance_date_null(self):
        f = EdgeDateNullFilter(field="created", operator="isNull")
        assert _edge_filter_discriminator(f) == "date"

    def test_unknown_returns_none(self):
        assert _edge_filter_discriminator("not_a_dict") is None


class TestEdgeFiltersDiscriminator:
    def test_dict_and(self):
        assert (
            _edge_filters_discriminator(
                {
                    "and": [
                        {"field": "edgeState", "operator": "is", "value": "CONNECTED"}
                    ]
                }
            )
            == "and"
        )

    def test_dict_and_underscore(self):
        assert (
            _edge_filters_discriminator(
                {
                    "and_": [
                        {"field": "edgeState", "operator": "is", "value": "CONNECTED"}
                    ]
                }
            )
            == "and"
        )

    def test_dict_or(self):
        assert (
            _edge_filters_discriminator(
                {"or": [{"field": "edgeState", "operator": "is", "value": "CONNECTED"}]}
            )
            == "or"
        )

    def test_dict_bare_filter_delegates(self):
        assert (
            _edge_filters_discriminator(
                {"field": "name", "operator": "is", "value": "test"}
            )
            == "string"
        )
