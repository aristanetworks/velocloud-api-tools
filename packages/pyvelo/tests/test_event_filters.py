"""Tests for typed recursive enterprise-event filters."""

from typing import cast

import pytest
from pydantic import ValidationError
from pyvelo.client.schemas.event import (
    EnterpriseEventFiltersAnd,
    EnterpriseEventFiltersOr,
    EnterpriseEventSeverityFilter,
    EnterpriseEventStringNullFilter,
    EnterpriseEventTypeFilter,
    GetEnterpriseEvents,
)

from ._type_support import JsonObject


def _json_object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return cast(JsonObject, value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("expected an array of strings")
    items = cast(list[object], value)
    strings = [item for item in items if isinstance(item, str)]
    if len(strings) != len(items):
        raise TypeError("expected an array of strings")
    return strings


def test_recursive_event_filters_validate_and_serialize():
    request = GetEnterpriseEvents.model_validate(
        {
            "enterpriseId": 7,
            "filters": {
                "and_": [
                    {
                        "field": "severity",
                        "operator": "in",
                        "value": ["ERROR", "WARNING"],
                    },
                    {
                        "or_": [
                            {
                                "field": "event",
                                "operator": "is",
                                "value": "LINK_FLAPPING",
                            },
                            {"field": "edgeName", "operator": "isNotNull"},
                        ]
                    },
                ]
            },
        }
    )

    assert isinstance(request.filters, EnterpriseEventFiltersAnd)
    assert isinstance(request.filters.and_[0], EnterpriseEventSeverityFilter)
    nested = request.filters.and_[1]
    assert isinstance(nested, EnterpriseEventFiltersOr)
    assert isinstance(nested.or_[0], EnterpriseEventTypeFilter)
    assert isinstance(nested.or_[1], EnterpriseEventStringNullFilter)
    assert request.model_dump(by_alias=True)["filters"] == {
        "and": [
            {
                "field": "severity",
                "operator": "in",
                "value": ["ERROR", "WARNING"],
            },
            {
                "or": [
                    {
                        "field": "event",
                        "operator": "is",
                        "value": "LINK_FLAPPING",
                    },
                    {"field": "edgeName", "operator": "isNotNull"},
                ]
            },
        ]
    }


def test_recursive_event_filter_groups_must_not_be_empty():
    with pytest.raises(ValidationError):
        _ = GetEnterpriseEvents.model_validate({"filters": {"and_": [{"or_": []}]}})


def test_enterprise_event_filters_require_and_at_root():
    with pytest.raises(ValidationError):
        _ = GetEnterpriseEvents.model_validate(
            {
                "filters": {
                    "or_": [
                        {
                            "field": "severity",
                            "operator": "is",
                            "value": "ALERT",
                        },
                        {
                            "field": "severity",
                            "operator": "is",
                            "value": "NOTICE",
                        },
                    ]
                }
            }
        )


@pytest.mark.parametrize(
    ("operator", "value"),
    [
        ("is", ["LINK_FLAPPING"]),
        ("isNot", ["LINK_FLAPPING"]),
        ("in", "LINK_FLAPPING"),
        ("notIn", "LINK_FLAPPING"),
        ("in", []),
    ],
)
def test_event_filter_operator_value_cardinality(operator: str, value: object):
    with pytest.raises(ValidationError):
        _ = GetEnterpriseEvents.model_validate(
            {
                "filters": {
                    "and": [{"field": "event", "operator": operator, "value": value}]
                }
            }
        )


def test_string_filter_supports_in_with_multiple_values():
    request = GetEnterpriseEvents.model_validate(
        {
            "filters": {
                "and": [
                    {
                        "field": "edgeName",
                        "operator": "in",
                        "value": ["Branch A", "Branch B"],
                    }
                ]
            }
        }
    )

    assert request.model_dump(by_alias=True)["filters"] == {
        "and": [
            {
                "field": "edgeName",
                "operator": "in",
                "value": ["Branch A", "Branch B"],
            }
        ]
    }


def test_json_schema_exposes_operator_value_cardinality():
    schema = cast(JsonObject, GetEnterpriseEvents.model_json_schema(by_alias=True))

    defs = _json_object(schema["$defs"])
    event_filter = _json_object(defs["EnterpriseEventTypeFilter"])
    assert event_filter["oneOf"] == [
        {
            "properties": {
                "operator": {"enum": ["is", "isNot"]},
                "value": {"not": {"type": "array"}},
            },
            "required": ["operator", "value"],
        },
        {
            "properties": {
                "operator": {"enum": ["in", "notIn"]},
                "value": {"minItems": 1, "type": "array"},
            },
            "required": ["operator", "value"],
        },
    ]

    string_filter = _json_object(defs["EnterpriseEventStringValueFilter"])
    string_variants = string_filter["oneOf"]
    assert isinstance(string_variants, list)
    scalar_variant = _json_object(string_variants[0])
    scalar_properties = _json_object(scalar_variant["properties"])
    operator_property = _json_object(scalar_properties["operator"])
    scalar_operators = _string_list(operator_property["enum"])
    assert set(scalar_operators) == {
        "is",
        "isNot",
        "contains",
        "notContains",
        "startsWith",
        "notStartsWith",
        "endsWith",
        "notEndsWith",
    }
    array_variant = _json_object(string_variants[1])
    array_properties = _json_object(array_variant["properties"])
    assert array_properties == {
        "operator": {"enum": ["in", "notIn"]},
        "value": {"minItems": 1, "type": "array"},
    }
