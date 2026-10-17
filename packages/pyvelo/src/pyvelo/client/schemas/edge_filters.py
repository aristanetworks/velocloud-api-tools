"""Typed filter models for enterprise/getEnterpriseEdges.

Provides a discriminated-union filter type that restricts field names,
operators, and value types to exactly what the VCO accepts.  Follows the
same pattern as the event filters in ``schemas/event.py``.

Filterable fields and their operator sets:

Enum fields (activationState, edgeState, haState):
    is, isNot, in, notIn — value is the enum or a list thereof.

String fields (buildNumber, logicalId, modelNumber, name, serialNumber,
softwareVersion):
    is, isNot, in, notIn, contains, notContains, startsWith,
    notStartsWith, endsWith, notEndsWith — value is str | list[str].
    isNull, isNotNull — no value.

Date fields (created, softwareUpdated):
    is, isNot, in, notIn — value is str | list[str].
    isNull, isNotNull — no value.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import annotated_types
from pydantic import AliasChoices, BeforeValidator, Discriminator, Field, Tag

from pyvelo.client.enums.edge import ActivationState, EdgeState, HaState
from pyvelo.core import Alias, VcoModel

# ---------------------------------------------------------------------------
# Operator type aliases
# ---------------------------------------------------------------------------

_EdgeEnumOperator = Literal["is", "isNot", "in", "notIn"]

_EdgeStringValueOperator = Literal[
    "is",
    "isNot",
    "in",
    "notIn",
    "contains",
    "notContains",
    "startsWith",
    "notStartsWith",
    "endsWith",
    "notEndsWith",
]

_EdgeNullOperator = Literal["isNull", "isNotNull"]

_EdgeDateValueOperator = Literal["is", "isNot", "in", "notIn"]

# ---------------------------------------------------------------------------
# Field name type aliases
# ---------------------------------------------------------------------------

_EdgeStringField = Literal[
    "buildNumber",
    "logicalId",
    "modelNumber",
    "name",
    "serialNumber",
    "softwareVersion",
]

_EdgeDateField = Literal["created", "softwareUpdated"]

# ---------------------------------------------------------------------------
# Lookup sets for discriminator functions
# ---------------------------------------------------------------------------

_ENUM_FIELDS: frozenset[str] = frozenset({"activationState", "edgeState", "haState"})
_STRING_FIELDS: frozenset[str] = frozenset(
    {
        "buildNumber",
        "logicalId",
        "modelNumber",
        "name",
        "serialNumber",
        "softwareVersion",
    }
)
_DATE_FIELDS: frozenset[str] = frozenset({"created", "softwareUpdated"})
_NULL_OPS: frozenset[str] = frozenset({"isNull", "isNotNull"})

# ---------------------------------------------------------------------------
# Per-field filter classes — enum fields
# ---------------------------------------------------------------------------


class EdgeActivationStateFilter(VcoModel):
    field: Literal["activationState"] = Field(
        validation_alias=AliasChoices("field", "fieldName")
    )
    operator: _EdgeEnumOperator = Field(validation_alias=AliasChoices("operator", "op"))
    value: ActivationState | list[ActivationState]


class EdgeStateFilter(VcoModel):
    field: Literal["edgeState"] = Field(
        validation_alias=AliasChoices("field", "fieldName")
    )
    operator: _EdgeEnumOperator = Field(validation_alias=AliasChoices("operator", "op"))
    value: EdgeState | list[EdgeState]


class EdgeHaStateFilter(VcoModel):
    field: Literal["haState"] = Field(
        validation_alias=AliasChoices("field", "fieldName")
    )
    operator: _EdgeEnumOperator = Field(validation_alias=AliasChoices("operator", "op"))
    value: HaState | list[HaState]


# ---------------------------------------------------------------------------
# Per-field filter classes — string fields
# ---------------------------------------------------------------------------


class EdgeStringValueFilter(VcoModel):
    field: _EdgeStringField = Field(validation_alias=AliasChoices("field", "fieldName"))
    operator: _EdgeStringValueOperator = Field(
        validation_alias=AliasChoices("operator", "op")
    )
    value: str | list[str]


class EdgeStringNullFilter(VcoModel):
    field: _EdgeStringField = Field(validation_alias=AliasChoices("field", "fieldName"))
    operator: _EdgeNullOperator = Field(validation_alias=AliasChoices("operator", "op"))


# ---------------------------------------------------------------------------
# Per-field filter classes — date fields
# ---------------------------------------------------------------------------


class EdgeDateValueFilter(VcoModel):
    field: _EdgeDateField = Field(validation_alias=AliasChoices("field", "fieldName"))
    operator: _EdgeDateValueOperator = Field(
        validation_alias=AliasChoices("operator", "op")
    )
    value: str | list[str]


class EdgeDateNullFilter(VcoModel):
    field: _EdgeDateField = Field(validation_alias=AliasChoices("field", "fieldName"))
    operator: _EdgeNullOperator = Field(validation_alias=AliasChoices("operator", "op"))


# ---------------------------------------------------------------------------
# Discriminators
# ---------------------------------------------------------------------------


def _edge_nullable_filter_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
        op = v.get("operator") or v.get("op")
        return "null" if op in _NULL_OPS else "value"
    if isinstance(v, (EdgeStringNullFilter, EdgeDateNullFilter)):
        return "null"
    return "value"


type EdgeStringFilter = Annotated[
    (
        Annotated[EdgeStringValueFilter, Tag("value")]
        | Annotated[EdgeStringNullFilter, Tag("null")]
    ),
    Discriminator(_edge_nullable_filter_discriminator),
]

type EdgeDateFilter = Annotated[
    (
        Annotated[EdgeDateValueFilter, Tag("value")]
        | Annotated[EdgeDateNullFilter, Tag("null")]
    ),
    Discriminator(_edge_nullable_filter_discriminator),
]


def _edge_filter_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
        f = v.get("field") or v.get("fieldName")
        if f == "activationState":
            return "activationState"
        if f == "edgeState":
            return "edgeState"
        if f == "haState":
            return "haState"
        if f in _STRING_FIELDS:
            return "string"
        if f in _DATE_FIELDS:
            return "date"
    if isinstance(v, EdgeActivationStateFilter):
        return "activationState"
    if isinstance(v, EdgeStateFilter):
        return "edgeState"
    if isinstance(v, EdgeHaStateFilter):
        return "haState"
    if isinstance(v, (EdgeStringValueFilter, EdgeStringNullFilter)):
        return "string"
    if isinstance(v, (EdgeDateValueFilter, EdgeDateNullFilter)):
        return "date"
    return None


type EdgeFilter = Annotated[
    (
        Annotated[EdgeActivationStateFilter, Tag("activationState")]
        | Annotated[EdgeStateFilter, Tag("edgeState")]
        | Annotated[EdgeHaStateFilter, Tag("haState")]
        | Annotated[EdgeStringFilter, Tag("string")]
        | Annotated[EdgeDateFilter, Tag("date")]
    ),
    Discriminator(_edge_filter_discriminator),
]

type _EdgeFilterList = Annotated[list[EdgeFilter], annotated_types.Len(min_length=1)]


# ---------------------------------------------------------------------------
# And/Or group wrappers
# ---------------------------------------------------------------------------


class EdgeFiltersAnd(VcoModel):
    and_: Annotated[_EdgeFilterList, Alias("and")]


class EdgeFiltersOr(VcoModel):
    or_: Annotated[_EdgeFilterList, Alias("or")]


# ---------------------------------------------------------------------------
# Top-level discriminated union (individual filter | and | or)
# ---------------------------------------------------------------------------


def _edge_filters_discriminator(v: Any) -> str | None:
    if isinstance(v, dict):
        if "and" in v or "and_" in v:
            return "and"
        if "or" in v or "or_" in v:
            return "or"
        return _edge_filter_discriminator(v)
    if isinstance(v, EdgeFiltersAnd):
        return "and"
    if isinstance(v, EdgeFiltersOr):
        return "or"
    return _edge_filter_discriminator(v)


type EdgeFilters = Annotated[
    (
        Annotated[EdgeActivationStateFilter, Tag("activationState")]
        | Annotated[EdgeStateFilter, Tag("edgeState")]
        | Annotated[EdgeHaStateFilter, Tag("haState")]
        | Annotated[EdgeStringFilter, Tag("string")]
        | Annotated[EdgeDateFilter, Tag("date")]
        | Annotated[EdgeFiltersAnd, Tag("and")]
        | Annotated[EdgeFiltersOr, Tag("or")]
    ),
    Discriminator(_edge_filters_discriminator),
]


# ---------------------------------------------------------------------------
# Group-only variant with bare-filter auto-wrapping
# ---------------------------------------------------------------------------


def _wrap_bare_edge_filter(v: Any) -> Any:
    if isinstance(
        v,
        (
            EdgeActivationStateFilter,
            EdgeStateFilter,
            EdgeHaStateFilter,
            EdgeStringValueFilter,
            EdgeStringNullFilter,
            EdgeDateValueFilter,
            EdgeDateNullFilter,
        ),
    ):
        return EdgeFiltersAnd(and_=[v])
    if isinstance(v, dict):
        # LLMs confuse JSON Schema keywords with filter operator keys.
        if "anyOf" in v or "oneOf" in v:
            return {"or": v.get("anyOf") or v.get("oneOf")}
        if "allOf" in v:
            return {"and": v["allOf"]}
        if not any(k in v for k in ("and", "and_", "or", "or_")):
            f = v.get("field") or v.get("fieldName")
            if f in _ENUM_FIELDS or f in _STRING_FIELDS or f in _DATE_FIELDS:
                return {"and": [v]}
    return v


type EdgeGroupFilters = Annotated[
    EdgeFiltersAnd | EdgeFiltersOr,
    BeforeValidator(_wrap_bare_edge_filter),
]
