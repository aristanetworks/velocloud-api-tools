import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

import annotated_types
from pydantic import (
    MISSING,
    AfterValidator,
    AliasChoices,
    ConfigDict,
    Discriminator,
    Field,
    SerializeAsAny,
    Tag,
    model_validator,
)
from pydantic.functional_validators import PlainValidator

from pyvelo.client.enums.event import (
    EventCategory,
    EventSeverity,
    EventType,
    GetEnterpriseAlertsWith,
    SortingDirection,
)
from pyvelo.core import (
    Alias,
    ApiInterval,
    PaginatedVcoModel,
    VcoModel,
    VcoTimestamp,
)
from pyvelo.core.fields import Resolvable
from pyvelo.core.identity import EdgeIdentity


def _lenient_enum(enum_cls: type) -> Any:
    def _validate(v: Any) -> Any:
        if isinstance(v, enum_cls):
            return v
        try:
            return enum_cls(v)
        except ValueError:
            return v

    return _validate


def check_int_zero(v: int | MISSING) -> int | MISSING:
    if v == 0:
        return MISSING

    return v


def check_string_empty(s: str | MISSING) -> str | MISSING:
    if isinstance(s, str) and len(s) == 0:
        return MISSING

    return s


_EnterpriseEventSortAttribute = Literal[
    "eventTime",
    "event",
    "segmentName",
    "edgeName",
    "enterpriseUsername",
    "severity",
    "message",
]

_EnterpriseEventStringField = Literal[
    "enterpriseUsername", "detail", "segmentName", "message", "edgeName"
]

# Operators valid for enum fields (event, severity): always require a value.
_EnterpriseEventEnumOperator = Literal["is", "isNot", "in", "notIn"]

# Operators valid for string fields that carry a value.
_EnterpriseEventStringOperator = Literal[
    "is",
    "isNot",
    "contains",
    "notContains",
    "startsWith",
    "notStartsWith",
    "endsWith",
    "notEndsWith",
    "in",
    "notIn",
]

# Null-check operators: no value field is sent.
_EnterpriseEventNullOperator = Literal["isNull", "isNotNull"]

_STRING_NULL_OPS: frozenset[str] = frozenset({"isNull", "isNotNull"})
_STRING_FIELDS: frozenset[str] = frozenset(
    {"enterpriseUsername", "detail", "segmentName", "message", "edgeName"}
)
_LIST_OPERATORS: frozenset[str] = frozenset({"in", "notIn"})


def _filter_cardinality_json_schema(
    scalar_operators: tuple[str, ...],
) -> dict[str, Any]:
    """Expose operator/value cardinality to JSON Schema consumers."""
    return {
        "oneOf": [
            {
                "properties": {
                    "operator": {"enum": list(scalar_operators)},
                    "value": {"not": {"type": "array"}},
                },
                "required": ["operator", "value"],
            },
            {
                "properties": {
                    "operator": {"enum": sorted(_LIST_OPERATORS)},
                    "value": {"type": "array", "minItems": 1},
                },
                "required": ["operator", "value"],
            },
        ]
    }


def _validate_filter_value_cardinality(operator: str, value: Any) -> None:
    """Match VCO's scalar versus list requirements for API filters."""
    if operator in _LIST_OPERATORS:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{operator} requires one or more values")
    elif isinstance(value, list):
        raise ValueError(f"{operator} requires one scalar value")


class EventSorting(VcoModel):
    attribute: _EnterpriseEventSortAttribute
    type_: Annotated[SortingDirection, Alias("type")]


class EnterpriseEventTypeFilter(VcoModel):
    """Filter by event type. Scalar value for is/isNot; list for in/notIn."""

    model_config = ConfigDict(
        json_schema_extra=_filter_cardinality_json_schema(("is", "isNot"))
    )

    field: Literal["event"] = Field(validation_alias=AliasChoices("field", "fieldName"))
    operator: _EnterpriseEventEnumOperator = Field(
        validation_alias=AliasChoices("operator", "op")
    )
    value: EventType | list[EventType]

    @model_validator(mode="after")
    def _validate_value_cardinality(self) -> Self:
        _validate_filter_value_cardinality(self.operator, self.value)
        return self


class EnterpriseEventSeverityFilter(VcoModel):
    """Filter by severity. Scalar value for is/isNot; list for in/notIn."""

    model_config = ConfigDict(
        json_schema_extra=_filter_cardinality_json_schema(("is", "isNot"))
    )

    field: Literal["severity"] = Field(
        validation_alias=AliasChoices("field", "fieldName")
    )
    operator: _EnterpriseEventEnumOperator = Field(
        validation_alias=AliasChoices("operator", "op")
    )
    value: EventSeverity | list[EventSeverity]

    @model_validator(mode="after")
    def _validate_value_cardinality(self) -> Self:
        _validate_filter_value_cardinality(self.operator, self.value)
        return self


class EnterpriseEventStringValueFilter(VcoModel):
    """Filter a string field using a scalar or list-valued operator."""

    model_config = ConfigDict(
        json_schema_extra=_filter_cardinality_json_schema(
            (
                "is",
                "isNot",
                "contains",
                "notContains",
                "startsWith",
                "notStartsWith",
                "endsWith",
                "notEndsWith",
            )
        )
    )

    field: _EnterpriseEventStringField = Field(
        validation_alias=AliasChoices("field", "fieldName")
    )
    operator: _EnterpriseEventStringOperator = Field(
        validation_alias=AliasChoices("operator", "op")
    )
    value: str | list[str]

    @model_validator(mode="after")
    def _validate_value_cardinality(self) -> Self:
        _validate_filter_value_cardinality(self.operator, self.value)
        return self


class EnterpriseEventStringNullFilter(VcoModel):
    """Filter a string field using isNull or isNotNull (no value sent)."""

    field: _EnterpriseEventStringField = Field(
        validation_alias=AliasChoices("field", "fieldName")
    )
    operator: _EnterpriseEventNullOperator = Field(
        validation_alias=AliasChoices("operator", "op")
    )


def _enterprise_event_string_filter_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
        op = v.get("operator") or v.get("op")
        return "null" if op in _STRING_NULL_OPS else "value"
    if isinstance(v, EnterpriseEventStringNullFilter):
        return "null"
    return "value"


type EnterpriseEventStringFilter = Annotated[
    (
        Annotated[EnterpriseEventStringValueFilter, Tag("value")]
        | Annotated[EnterpriseEventStringNullFilter, Tag("null")]
    ),
    Discriminator(_enterprise_event_string_filter_discriminator),
]


def _enterprise_event_filter_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
        f = v.get("field") or v.get("fieldName")
        if f == "event":
            return "event"
        if f == "severity":
            return "severity"
        if f in _STRING_FIELDS:
            return "string"
    if isinstance(v, EnterpriseEventTypeFilter):
        return "event"
    if isinstance(v, EnterpriseEventSeverityFilter):
        return "severity"
    if isinstance(
        v, (EnterpriseEventStringValueFilter, EnterpriseEventStringNullFilter)
    ):
        return "string"
    return None


type EnterpriseEventFilter = Annotated[
    (
        Annotated[EnterpriseEventTypeFilter, Tag("event")]
        | Annotated[EnterpriseEventSeverityFilter, Tag("severity")]
        | Annotated[EnterpriseEventStringFilter, Tag("string")]
    ),
    Discriminator(_enterprise_event_filter_discriminator),
]


class EnterpriseEventFiltersAnd(VcoModel):
    and_: Annotated[
        Sequence[SerializeAsAny["EnterpriseEventFilters"]],
        annotated_types.Len(min_length=1),
        Alias("and"),
    ]


class EnterpriseEventFiltersOr(VcoModel):
    or_: Annotated[
        Sequence[SerializeAsAny["EnterpriseEventFilters"]],
        annotated_types.Len(min_length=1),
        Alias("or"),
    ]


def _enterprise_event_filters_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
        if "and" in v or "and_" in v:
            return "and"
        if "or" in v or "or_" in v:
            return "or"
        return _enterprise_event_filter_discriminator(v)
    if isinstance(v, EnterpriseEventFiltersAnd):
        return "and"
    if isinstance(v, EnterpriseEventFiltersOr):
        return "or"
    return _enterprise_event_filter_discriminator(v)


type EnterpriseEventFilters = Annotated[
    (
        Annotated[EnterpriseEventTypeFilter, Tag("event")]
        | Annotated[EnterpriseEventSeverityFilter, Tag("severity")]
        | Annotated[EnterpriseEventStringFilter, Tag("string")]
        | Annotated[EnterpriseEventFiltersAnd, Tag("and")]
        | Annotated[EnterpriseEventFiltersOr, Tag("or")]
    ),
    Discriminator(_enterprise_event_filters_discriminator),
]

EnterpriseEventFiltersAnd.model_rebuild(_types_namespace=globals())
EnterpriseEventFiltersOr.model_rebuild(_types_namespace=globals())


class GetEnterpriseEvents(PaginatedVcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: list[int] | int | MISSING = MISSING
    filters: EnterpriseEventFiltersAnd | MISSING = MISSING
    interval: ApiInterval | MISSING = MISSING
    sort_by: list[EventSorting] | MISSING = MISSING
    limit: int | MISSING = MISSING
    next_page_link: str | MISSING = MISSING


class GetEnterpriseEventsItem(VcoModel):
    """A single event from the VCO event log."""

    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    logical_id: uuid.UUID
    enterprise_name: str
    enterprise_username: Annotated[
        str | MISSING, AfterValidator(check_string_empty)
    ] = MISSING
    edge_id: Annotated[
        int | MISSING, AfterValidator(check_int_zero), Resolvable(EdgeIdentity, "id")
    ] = MISSING
    edge_name: Annotated[str | MISSING, AfterValidator(check_string_empty)] = MISSING
    segment_name: Annotated[str | MISSING, AfterValidator(check_string_empty)] = MISSING
    created: VcoTimestamp
    event_time: VcoTimestamp = Field(description="When the event occurred on the Edge")
    event: Annotated[EventType | str, PlainValidator(_lenient_enum(EventType))] = Field(
        description="Event type identifier (e.g. LINK_ALIVE, EDGE_DOWN)"
    )
    category: Annotated[
        EventCategory | str, PlainValidator(_lenient_enum(EventCategory))
    ] = Field(description="Event category (SYSTEM, NETWORK, SECURITY, etc.)")
    severity: Annotated[
        EventSeverity | str, PlainValidator(_lenient_enum(EventSeverity))
    ] = Field(
        description="Severity level (EMERGENCY, ALERT, CRITICAL, WARNING, NOTICE, INFO)"
    )
    message: str = Field(description="Human-readable event summary")
    detail: str = Field(
        description="Extended event details (JSON-encoded for some event types)"
    )


# ---------------------------------------------------------------------------
# Enterprise Alerts request / response
# ---------------------------------------------------------------------------


class EventFilterRule(VcoModel):
    field: str
    op: str
    values: list[str]


class EventFilter(VcoModel):
    rules: list[EventFilterRule] | MISSING = MISSING
    limit: int | MISSING = MISSING


class GetEnterpriseAlerts(VcoModel):
    enterprise_id: int | MISSING = MISSING
    limit: int | MISSING = MISSING
    interval: ApiInterval | MISSING = MISSING
    filter: Annotated[EventFilter | dict[str, Any] | MISSING, Alias("filter")] = MISSING
    with_: Annotated[list[GetEnterpriseAlertsWith] | MISSING, Alias("with")] = MISSING


class EnterpriseAlertItem(VcoModel):
    """An alert triggered by VCO event processing rules."""

    id_: Annotated[int | MISSING, Alias("id")] = MISSING
    alert_type: str | MISSING = Field(
        MISSING, description="Alert type (e.g. EDGE_DOWN, LINK_DOWN, HA_FAILOVER)"
    )
    trigger_time: VcoTimestamp | MISSING = Field(
        MISSING, description="When the alert was triggered"
    )
    severity: EventSeverity | MISSING = Field(
        MISSING, description="Alert severity level"
    )
    edge_name: str | MISSING = Field(
        MISSING, description="Name of the Edge that triggered the alert"
    )
    edge_id: Annotated[int | MISSING, Resolvable(EdgeIdentity, "id")] = MISSING
    enterprise_id: int | MISSING = MISSING
    state: str | MISSING = Field(
        MISSING, description="Alert lifecycle state (TRIGGERED, CLEARED)"
    )
    comment: str | MISSING = MISSING
    name: str | MISSING = MISSING
    description: str | MISSING = MISSING


# ---------------------------------------------------------------------------
# Edge Events (convenience wrapper using event/getEnterpriseEvents with edgeId)
# ---------------------------------------------------------------------------


class GetEdgeEvents(PaginatedVcoModel):
    enterprise_id: int | MISSING = MISSING
    edge_id: list[int] | int
    interval: ApiInterval | MISSING = MISSING
    filter: Annotated[EventFilter | dict[str, Any] | MISSING, Alias("filter")] = MISSING
    limit: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Alerts by Type (convenience wrapper using enterprise/getEnterpriseAlerts
# with filter narrowed to a specific alert type)
# ---------------------------------------------------------------------------


class GetAlertsByType(VcoModel):
    enterprise_id: int | MISSING = MISSING
    alert_type: str
    interval: ApiInterval | MISSING = MISSING
    limit: int | MISSING = MISSING


# ---------------------------------------------------------------------------
# Operator Events (event/getOperatorEvents)
# ---------------------------------------------------------------------------


class GetOperatorEvents(PaginatedVcoModel):
    network_id: int = Field(description="VCO network (operator) ID")
    interval: dict[str, Any] | MISSING = MISSING
    limit: int | MISSING = MISSING
    filter: Annotated[dict[str, Any] | MISSING, Alias("filter")] = MISSING
    gateway_id: list[int] | MISSING = MISSING


class OperatorEventItem(VcoModel):
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Proxy Events (event/getProxyEvents)
# ---------------------------------------------------------------------------


class GetProxyEvents(PaginatedVcoModel):
    enterprise_proxy_id: int = Field(description="VCO partner/MSP proxy ID")
    interval: dict[str, Any] | MISSING = MISSING
    limit: int | MISSING = MISSING
    filter: Annotated[dict[str, Any] | MISSING, Alias("filter")] = MISSING
    enterprise_id: list[int] | MISSING = MISSING


class ProxyEventItem(VcoModel):
    model_config = ConfigDict(extra="allow")
