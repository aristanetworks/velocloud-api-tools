"""Tests for the Resolvable Annotated marker."""

import copy
import importlib
import pickle
import pkgutil
import uuid
from collections.abc import Callable
from typing import Annotated, Literal, get_type_hints

import pytest
import pyvelo
from pydantic import MISSING, Field
from pyvelo.client.models.configuration.firewall.rule_match import OutboundRuleMatch
from pyvelo.client.models.configuration.qos.common import BackhaulEdgeType
from pyvelo.core import (
    ByPresence,
    Default,
    Edge,
    Inheritable,
    Layered,
    VcoModel,
)
from pyvelo.core.enums import NetworkSegmentType
from pyvelo.core.fields import Alias, Resolvable, get_resolvable
from pyvelo.core.identity import (
    AddressGroupIdentity,
    ApplicationIdentity,
    HubIdentity,
    SegmentIdentity,
)

GUEST = SegmentIdentity(
    name="Guest",
    segment_id=3,
    object_id=30,
    logical_id=uuid.UUID(int=3),
    type_=NetworkSegmentType.REGULAR,
)


class HasResolvable(VcoModel):
    segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)] = 0
    segment_logical_id: Annotated[
        uuid.UUID, Resolvable(SegmentIdentity, lambda i: i.logical_id)
    ] = uuid.UUID(int=0)
    app_id: Annotated[int, Resolvable(ApplicationIdentity, lambda i: i.id)] = -1


class TestResolvableMarker:
    def test_processed_marker_in_metadata(self):
        fi = HasResolvable.model_fields["segment_id"]
        processed = get_resolvable(fi)
        assert processed is not None
        assert processed.identity_type is SegmentIdentity
        assert processed.attribute == "segment_id"

    def test_different_attribute_for_same_identity(self):
        fi = HasResolvable.model_fields["segment_logical_id"]
        processed = get_resolvable(fi)
        assert processed is not None
        assert processed.attribute == "logical_id"

    def test_different_identity_type(self):
        fi = HasResolvable.model_fields["app_id"]
        processed = get_resolvable(fi)
        assert processed is not None
        assert processed.identity_type is ApplicationIdentity
        assert processed.attribute == "id"

    def test_model_validates_normally(self):
        m = HasResolvable(segment_id=42)
        assert m.segment_id == 42


class TestAliasWithResolvableOnSubclass:
    """Regression: Alias must be applied even when a parent already processed Resolvable."""

    def test_subclass_alias_applied_alongside_inherited_resolvable(self):
        class Parent(VcoModel):
            app_id: Annotated[int, Resolvable(ApplicationIdentity, lambda i: i.id)] = -1

        class Child(Parent):
            app_id: Annotated[
                int, Alias("appid"), Resolvable(ApplicationIdentity, lambda i: i.id)
            ] = -1

        fi = Child.model_fields["app_id"]
        assert fi.validation_alias == "appid"
        assert fi.serialization_alias == "appid"
        processed = get_resolvable(fi)
        assert processed is not None

    def test_alias_roundtrip_on_subclass(self):
        class Parent(VcoModel):
            app_id: Annotated[int, Resolvable(ApplicationIdentity, lambda i: i.id)] = -1

        class Child(Parent):
            app_id: Annotated[
                int, Alias("appid"), Resolvable(ApplicationIdentity, lambda i: i.id)
            ] = -1

        obj = Child.model_validate({"appid": 42})
        assert obj.app_id == 42
        assert obj.model_dump(by_alias=True) == {"appid": 42}


def test_an_accessor_is_read_and_applied() -> None:
    class Rule(VcoModel):
        segment_id: Annotated[int, Resolvable(SegmentIdentity, lambda i: i.segment_id)]

    found = get_resolvable(Rule.model_fields["segment_id"])
    assert found is not None and found.attribute == "segment_id"
    assert found.accessor(GUEST) == GUEST.segment_id


BAD_ACCESSORS: list[Callable[[SegmentIdentity], object] | str] = [
    lambda i: i.segmnt_id,  # pyright: ignore[reportAttributeAccessIssue]
    lambda i: i.segment_id + 1,
    lambda i: str(i.segment_id),
    lambda i: (i.segment_id, i.object_id),
    # a falsy ID (the Global Segment's 0) would give None or another field
    lambda i: i.segment_id or None,
    lambda i: i.segment_id or i.object_id,
    "segment_id",
]

# Accessors that read no field, so no field can be suggested from what they read.
NO_FIELD_ACCESSORS: list[object] = [lambda i: 0, lambda i: 1 / 0, 5]  # pyright: ignore[reportUnknownLambdaType]


@pytest.mark.parametrize("accessor", BAD_ACCESSORS)
def test_an_accessor_must_read_one_field_and_return_it(accessor: object) -> None:
    with pytest.raises(TypeError, match="lambda i: i\\.segment_id"):
        _ = Resolvable(SegmentIdentity, accessor)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("accessor", NO_FIELD_ACCESSORS)
def test_an_accessor_that_reads_no_field_is_refused(accessor: object) -> None:
    with pytest.raises(TypeError, match=r"Resolvable\(SegmentIdentity, lambda i: i\."):
        _ = Resolvable(SegmentIdentity, accessor)  # pyright: ignore[reportArgumentType]


def test_an_accessor_that_raises_is_refused_with_what_it_raised() -> None:
    with pytest.raises(TypeError) as caught:
        _ = Resolvable(SegmentIdentity, lambda i: 1 / 0)
    text = str(caught.value)
    assert "raised" in text
    assert "ZeroDivisionError" in text
    assert "division by zero" in text
    assert "It read no field" not in text


def test_an_attribute_the_field_cannot_hold_is_refused_at_class_creation() -> None:
    with pytest.raises(TypeError) as caught:

        class Rule(VcoModel):  # pyright: ignore[reportUnusedClass]
            logical_id: Annotated[
                uuid.UUID, Resolvable(SegmentIdentity, lambda i: i.segment_id)
            ]

    text = str(caught.value)
    for part in ("Rule.logical_id", "SegmentIdentity.segment_id", "int", "UUID"):
        assert part in text
    assert "lambda i: i.logical_id" in text  # the accessor that fits


def test_none_and_missing_are_ignored_and_literal_values_must_fit() -> None:
    class Ok(VcoModel):
        edge_id: Annotated[
            int | None | MISSING, Resolvable(HubIdentity, lambda h: h.edge_id)
        ] = MISSING
        type_: Annotated[BackhaulEdgeType, Resolvable(HubIdentity, lambda h: h.type_)]

    assert get_resolvable(Ok.model_fields["edge_id"]) is not None
    with pytest.raises(TypeError, match="edgeHubCluster"):

        class Narrow(VcoModel):  # pyright: ignore[reportUnusedClass]
            type_: Annotated[
                Literal["edgeHub"], Resolvable(HubIdentity, lambda h: h.type_)
            ]


def test_an_optional_attribute_needs_a_field_that_allows_none() -> None:
    with pytest.raises(TypeError) as caught:

        class Refused(VcoModel):  # pyright: ignore[reportUnusedClass]
            hub: Annotated[int, Resolvable(HubIdentity, lambda h: h.edge_id)]

    text = str(caught.value)
    for part in ("Refused.hub", "HubIdentity.edge_id", "None", "int"):
        assert part in text

    class Ok(VcoModel):
        hub: Annotated[int | None, Resolvable(HubIdentity, lambda h: h.edge_id)]

    assert get_resolvable(Ok.model_fields["hub"]) is not None

    with pytest.raises(TypeError, match="None"):

        class MissingOnly(VcoModel):  # pyright: ignore[reportUnusedClass]
            hub: Annotated[
                int | MISSING, Resolvable(HubIdentity, lambda h: h.edge_id)
            ] = MISSING


def test_a_resolvable_inside_a_kind_is_checked_against_the_inner_type() -> None:
    class Fits(Layered[Edge]):
        group: Annotated[
            Inheritable[
                Annotated[
                    uuid.UUID, Resolvable(AddressGroupIdentity, lambda i: i.logical_id)
                ]
            ],
            ByPresence(),
            Default(uuid.UUID(int=0)),
        ] = Field(default=...)

    assert get_resolvable(Fits.model_fields["group"]) is not None
    with pytest.raises(TypeError, match="lambda i: i.logical_id"):

        class Refused(Layered[Edge]):  # pyright: ignore[reportUnusedClass]
            group: Annotated[
                Inheritable[
                    Annotated[
                        uuid.UUID, Resolvable(AddressGroupIdentity, lambda i: i.id)
                    ]
                ],
                ByPresence(),
                Default(uuid.UUID(int=0)),
            ] = Field(default=...)


def _vco_models() -> list[type[VcoModel]]:
    for module in pkgutil.walk_packages(pyvelo.__path__, "pyvelo."):
        _ = importlib.import_module(module.name)
    found: list[type[VcoModel]] = []
    pending = [VcoModel]
    while pending:
        model = pending.pop()
        for sub in model.__subclasses__():
            if sub not in found:
                found.append(sub)
                pending.append(sub)
    return found


def test_every_resolvable_in_pyvelo_is_an_accessor_on_a_field_of_its_identity() -> None:
    carrying = 0
    for model in _vco_models():
        if not model.__module__.startswith("pyvelo."):
            continue  # a model a test defined
        for name, fi in model.model_fields.items():
            found = get_resolvable(fi)
            if found is None:
                continue
            carrying += 1
            assert found.attribute in get_type_hints(found.identity_type), (
                f"{model.__name__}.{name}"
            )
    assert carrying > 200


def test_a_model_with_resolvable_fields_pickles_and_copies() -> None:
    rule = OutboundRuleMatch.model_validate({"applicationId": 3})
    assert get_resolvable(OutboundRuleMatch.model_fields["application_id"]) is not None
    assert pickle.loads(pickle.dumps(rule)) == rule
    assert copy.deepcopy(rule) == rule
