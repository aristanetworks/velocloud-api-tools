"""Tests for metrics schema models and field typing."""

from pyvelo.client.enums.edge import SortDirection
from pyvelo.client.enums.metrics import FlowVisibilityMetric, FlowVisibilitySortMetric
from pyvelo.client.schemas.metrics import (
    GetEdgeFlowVisibilityMetrics,
)

from ._type_support import require_present


class TestFlowVisibilitySortOrder:
    def test_sort_order_field_in_schema(self):
        schema = GetEdgeFlowVisibilityMetrics.model_json_schema()
        assert "sortOrder" in schema["properties"]

    def test_sort_order_round_trip(self):
        obj = GetEdgeFlowVisibilityMetrics.model_validate(
            {
                "edgeId": 1,
                "interval": {"start": "2024-01-01T00:00:00Z"},
                "sort": "totalBytes",
                "sortOrder": "DESC",
            }
        )
        assert obj.sort == FlowVisibilityMetric.TOTAL_BYTES
        assert obj.sort_order == SortDirection.DESC

    def test_sort_description_mentions_sort_order(self):
        schema = GetEdgeFlowVisibilityMetrics.model_json_schema()
        desc = schema["properties"]["sort"].get("description", "")
        assert "sortOrder" in desc

    def test_sort_by_description_guides_to_sort(self):
        schema = GetEdgeFlowVisibilityMetrics.model_json_schema()
        desc = schema["properties"]["sortBy"].get("description", "")
        assert "attribute" in desc
        assert "sort" in desc.lower()

    def test_sort_by_round_trip(self):
        obj = GetEdgeFlowVisibilityMetrics.model_validate(
            {
                "edgeId": 1,
                "interval": {"start": "2024-01-01T00:00:00Z"},
                "sortBy": [{"attribute": "application", "type": "DESC"}],
            }
        )
        sort_by = require_present(obj.sort_by)
        assert sort_by[0].attribute == FlowVisibilitySortMetric.APPLICATION
        assert sort_by[0].type_ == SortDirection.DESC
