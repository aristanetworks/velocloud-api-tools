"""Tests for metrics schema models and field typing."""

from typing import cast

from pyvelo.client.enums.edge import SortDirection
from pyvelo.client.enums.metrics import FlowVisibilityMetric, FlowVisibilitySortMetric
from pyvelo.client.schemas.metrics import (
    GetEdgeFlowVisibilityMetrics,
)

from ._type_support import JsonObject, require_present


def _schema_object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return cast(JsonObject, value)


def _property_description(schema: JsonObject, field: str) -> str:
    properties = _schema_object(schema["properties"])
    definition = _schema_object(properties[field])
    description = definition.get("description", "")
    if not isinstance(description, str):
        raise TypeError(f"{field} description was not a string")
    return description


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
        schema = cast(JsonObject, GetEdgeFlowVisibilityMetrics.model_json_schema())
        desc = _property_description(schema, "sort")
        assert "sortOrder" in desc

    def test_sort_by_description_guides_to_sort(self):
        schema = cast(JsonObject, GetEdgeFlowVisibilityMetrics.model_json_schema())
        desc = _property_description(schema, "sortBy")
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
