"""Tests for edge filter auto-wrapping and typed validation on GetEnterpriseEdges.

The VCO /portal/ backend rejects bare single-filter payloads with
``filters.and is not iterable`` — it requires a top-level and/or group.
EdgeGroupFilters auto-wraps bare filters via a BeforeValidator so callers
can stay concise without leaking the backend's wire requirement.
"""

from pydantic_core import MISSING
from pyvelo.client.schemas.edge_filters import (
    EdgeActivationStateFilter,
    EdgeFiltersAnd,
    EdgeFiltersOr,
    EdgeHaStateFilter,
    EdgeStateFilter,
    EdgeStringValueFilter,
)
from pyvelo.client.schemas.enterprise import GetEnterpriseEdgeList, GetEnterpriseEdges

from ._type_support import require_present


class TestEdgeGroupFiltersBareWrap:
    def test_bare_dict_is_wrapped_into_and(self):
        req = GetEnterpriseEdges.model_validate(
            {"filters": {"field": "edgeState", "operator": "is", "value": "CONNECTED"}}
        )
        assert isinstance(req.filters, EdgeFiltersAnd)
        assert len(req.filters.and_) == 1
        assert isinstance(req.filters.and_[0], EdgeStateFilter)
        assert req.filters.and_[0].field == "edgeState"
        assert req.filters.and_[0].operator == "is"
        assert req.filters.and_[0].value == "CONNECTED"

    def test_bare_dict_with_field_alias_is_wrapped(self):
        req = GetEnterpriseEdges.model_validate(
            {"filters": {"fieldName": "edgeState", "op": "is", "value": "CONNECTED"}}
        )
        assert isinstance(req.filters, EdgeFiltersAnd)
        assert isinstance(req.filters.and_[0], EdgeStateFilter)
        assert req.filters.and_[0].field == "edgeState"

    def test_bare_filter_instance_is_wrapped(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": EdgeStateFilter.model_validate(
                    {"field": "edgeState", "operator": "is", "value": "CONNECTED"}
                )
            }
        )
        assert isinstance(req.filters, EdgeFiltersAnd)
        assert isinstance(req.filters.and_[0], EdgeStateFilter)
        assert req.filters.and_[0].field == "edgeState"

    def test_and_group_passes_through(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": {
                    "and": [
                        {"field": "edgeState", "operator": "is", "value": "CONNECTED"}
                    ]
                }
            }
        )
        assert isinstance(req.filters, EdgeFiltersAnd)
        assert isinstance(req.filters.and_[0], EdgeStateFilter)
        assert req.filters.and_[0].field == "edgeState"

    def test_or_group_passes_through(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": {
                    "or": [
                        {"field": "edgeState", "operator": "is", "value": "CONNECTED"},
                        {"field": "edgeState", "operator": "is", "value": "DEGRADED"},
                    ]
                }
            }
        )
        assert isinstance(req.filters, EdgeFiltersOr)
        assert len(req.filters.or_) == 2

    def test_missing_filters_stays_missing(self):
        req = GetEnterpriseEdges.model_validate({})
        assert req.filters is MISSING

    def test_wrapped_serializes_with_and_key(self):
        req = GetEnterpriseEdges.model_validate(
            {"filters": {"field": "edgeState", "operator": "is", "value": "CONNECTED"}}
        )
        dumped = req.model_dump(by_alias=True, exclude_unset=False)
        assert "and" in dumped["filters"]
        assert dumped["filters"]["and"][0]["field"] == "edgeState"


class TestEdgeFilterFieldDiscrimination:
    def test_activation_state_filter(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": {
                    "and": [
                        {
                            "field": "activationState",
                            "operator": "in",
                            "value": ["ACTIVATED", "PENDING"],
                        }
                    ]
                }
            }
        )
        filters = require_present(req.filters)
        assert isinstance(filters, EdgeFiltersAnd)
        f = filters.and_[0]
        assert isinstance(f, EdgeActivationStateFilter)
        assert f.field == "activationState"

    def test_string_field_filter(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": {
                    "and": [
                        {
                            "field": "name",
                            "operator": "contains",
                            "value": "branch",
                        }
                    ]
                }
            }
        )
        filters = require_present(req.filters)
        assert isinstance(filters, EdgeFiltersAnd)
        f = filters.and_[0]
        assert isinstance(f, EdgeStringValueFilter)
        assert f.field == "name"
        assert f.operator == "contains"

    def test_ha_state_filter(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": {
                    "and": [
                        {
                            "field": "haState",
                            "operator": "is",
                            "value": "FAILED",
                        }
                    ]
                }
            }
        )
        filters = require_present(req.filters)
        assert isinstance(filters, EdgeFiltersAnd)
        f = filters.and_[0]
        assert isinstance(f, EdgeHaStateFilter)
        assert f.field == "haState"
        assert f.value == "FAILED"

    def test_ha_state_filter_in_operator(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": {
                    "and": [
                        {
                            "field": "haState",
                            "operator": "in",
                            "value": ["READY", "FAILED"],
                        }
                    ]
                }
            }
        )
        filters = require_present(req.filters)
        assert isinstance(filters, EdgeFiltersAnd)
        f = filters.and_[0]
        assert isinstance(f, EdgeHaStateFilter)
        assert f.operator == "in"
        assert f.value == ["READY", "FAILED"]

    def test_ha_state_filter_on_edge_list(self):
        """Reproduce the exact input from the reported bug."""
        req = GetEnterpriseEdgeList.model_validate(
            {
                "limit": 20,
                "enterpriseId": 2653,
                "filters": {
                    "and_": [
                        {
                            "value": "FAILED",
                            "operator": "is",
                            "field": "haState",
                        }
                    ]
                },
            }
        )
        assert isinstance(req.filters, EdgeFiltersAnd)
        filters = require_present(req.filters)
        assert isinstance(filters, EdgeFiltersAnd)
        f = filters.and_[0]
        assert isinstance(f, EdgeHaStateFilter)
        assert f.value == "FAILED"

    def test_mixed_fields_in_and_group(self):
        req = GetEnterpriseEdges.model_validate(
            {
                "filters": {
                    "and": [
                        {"field": "edgeState", "operator": "is", "value": "CONNECTED"},
                        {"field": "name", "operator": "startsWith", "value": "dc-"},
                        {
                            "field": "activationState",
                            "operator": "is",
                            "value": "ACTIVATED",
                        },
                    ]
                }
            }
        )
        filters = require_present(req.filters)
        assert isinstance(filters, EdgeFiltersAnd)
        assert isinstance(filters.and_[0], EdgeStateFilter)
        assert isinstance(filters.and_[1], EdgeStringValueFilter)
        assert isinstance(filters.and_[2], EdgeActivationStateFilter)
