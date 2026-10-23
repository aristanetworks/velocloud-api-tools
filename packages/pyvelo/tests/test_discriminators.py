"""Tests for the public discriminated-union model types."""

import pytest
from pydantic import TypeAdapter, ValidationError
from pyvelo.client.enums.edge import EdgeState
from pyvelo.client.models.enterprise.object_group import (
    AddressGroupEntry,
    DomainAddressEntry,
    IcmpPortEntry,
    IpAddressEntry,
    PortGroupEntry,
    TcpUdpPortEntry,
)
from pyvelo.client.schemas.edge_filters import (
    EdgeActivationStateFilter,
    EdgeDateNullFilter,
    EdgeDateValueFilter,
    EdgeFilters,
    EdgeFiltersAnd,
    EdgeFiltersOr,
    EdgeHaStateFilter,
    EdgeStateFilter,
    EdgeStringNullFilter,
    EdgeStringValueFilter,
)
from pyvelo.core.base import ApiFilter, ApiFilters, ApiFiltersAnd, ApiFiltersOr


class TestApiFilters:
    @pytest.mark.parametrize(
        ("payload", "expected_type"),
        [
            (
                {"and": [{"field": "x", "operator": "is", "value": "1"}]},
                ApiFiltersAnd,
            ),
            (
                {"and_": [{"field": "x", "operator": "is", "value": "1"}]},
                ApiFiltersAnd,
            ),
            (
                {"or": [{"field": "x", "operator": "is", "value": "1"}]},
                ApiFiltersOr,
            ),
            (
                {"or_": [{"field": "x", "operator": "is", "value": "1"}]},
                ApiFiltersOr,
            ),
            ({"field": "name", "operator": "is", "value": "test"}, ApiFilter),
        ],
    )
    def test_payload_selects_variant(self, payload: object, expected_type: type):
        result = TypeAdapter(ApiFilters).validate_python(payload)
        assert isinstance(result, expected_type)

    def test_non_filter_payload_is_rejected(self):
        with pytest.raises(ValidationError):
            _ = TypeAdapter(ApiFilters).validate_python("not a filter")


class TestAddressGroupEntry:
    def test_parse_ip_entry(self):
        result = TypeAdapter(AddressGroupEntry).validate_python(
            {
                "entry_type": "ip",
                "ip": "10.0.0.0",
                "rule_type": "prefix",
                "mask": "255.255.255.0",
            }
        )
        assert isinstance(result, IpAddressEntry)

    def test_parse_domain_entry(self):
        result = TypeAdapter(AddressGroupEntry).validate_python(
            {"entry_type": "domain", "domain": "example.com"}
        )
        assert isinstance(result, DomainAddressEntry)

    def test_domain_takes_precedence_when_both_shapes_are_present(self):
        result = TypeAdapter(AddressGroupEntry).validate_python(
            {"domain": "example.com", "ip": "10.0.0.1"}
        )
        assert isinstance(result, DomainAddressEntry)

    def test_existing_model_instance_is_accepted(self):
        entry = DomainAddressEntry(domain="example.com")
        assert TypeAdapter(AddressGroupEntry).validate_python(entry) is entry


class TestPortGroupEntry:
    def test_parse_port_entry(self):
        result = TypeAdapter(PortGroupEntry).validate_python(
            {"entry_type": "port", "proto": 6, "port_low": 80, "port_high": 443}
        )
        assert isinstance(result, TcpUdpPortEntry)

    def test_parse_icmp_entry(self):
        result = TypeAdapter(PortGroupEntry).validate_python(
            {
                "entry_type": "icmp",
                "proto": 1,
                "type": 8,
                "code_low": 0,
                "code_high": 0,
            }
        )
        assert isinstance(result, IcmpPortEntry)

    def test_existing_model_instance_is_accepted(self):
        entry = TcpUdpPortEntry(proto=6, port_low=80, port_high=443)
        assert TypeAdapter(PortGroupEntry).validate_python(entry) is entry


class TestEdgeFilterUnions:
    @pytest.mark.parametrize(
        ("payload", "expected_type"),
        [
            (
                {"field": "activationState", "operator": "is", "value": "ACTIVATED"},
                EdgeActivationStateFilter,
            ),
            (
                {"field": "edgeState", "operator": "is", "value": "CONNECTED"},
                EdgeStateFilter,
            ),
            (
                {"field": "haState", "operator": "is", "value": "FAILED"},
                EdgeHaStateFilter,
            ),
            (
                {"field": "name", "operator": "contains", "value": "dc"},
                EdgeStringValueFilter,
            ),
            (
                {"field": "name", "operator": "isNull"},
                EdgeStringNullFilter,
            ),
            (
                {"field": "created", "operator": "is", "value": "2024-01-01"},
                EdgeDateValueFilter,
            ),
            (
                {"field": "created", "operator": "isNull"},
                EdgeDateNullFilter,
            ),
            (
                {"fieldName": "edgeState", "operator": "is", "value": "CONNECTED"},
                EdgeStateFilter,
            ),
        ],
    )
    def test_edge_field_selects_variant(self, payload: object, expected_type: type):
        result = TypeAdapter(EdgeFilters).validate_python(payload)
        assert isinstance(result, expected_type)

    def test_groups_and_bare_filters_are_accepted(self):
        adapter = TypeAdapter(EdgeFilters)
        and_group = adapter.validate_python(
            {"and": [{"field": "edgeState", "operator": "is", "value": "CONNECTED"}]}
        )
        or_group = adapter.validate_python(
            {
                "or": [
                    {"field": "name", "operator": "is", "value": "Branch"},
                    {"field": "created", "operator": "isNull"},
                ]
            }
        )
        assert isinstance(and_group, EdgeFiltersAnd)
        assert isinstance(or_group, EdgeFiltersOr)

    def test_bare_filter_instance_is_accepted(self):
        edge_state = EdgeStateFilter(
            field="edgeState", operator="is", value=EdgeState.CONNECTED
        )
        result = TypeAdapter(EdgeFilters).validate_python(edge_state)
        assert result is edge_state
