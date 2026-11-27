"""Tests for the KeyField Annotated marker."""

from collections.abc import Callable
from typing import Annotated

import pytest
from pydantic import Field
from pyvelo.client.models.configuration.device_settings.lan import LanSettings
from pyvelo.client.models.configuration.device_settings.segment import (
    NetflowCollector,
    SegmentNetflow,
    SegmentOspf,
)
from pyvelo.core import VcoModel, item_selector, select_items
from pyvelo.core.fields import KeyField, get_key_spec


class SegmentMeta(VcoModel):
    name: str
    segment_id: int = 0


class Rule(VcoModel):
    name: str
    enabled: bool = True


class HasKeyField(VcoModel):
    segments: Annotated[list[SegmentMeta], KeyField("name")] = Field(
        default_factory=list
    )


class HasSegmentMeta(VcoModel):
    meta: SegmentMeta
    value: int = 0


class HasNestedKeyField(VcoModel):
    items: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)


class HasDottedKeyField(VcoModel):
    segments: Annotated[list[HasSegmentMeta], KeyField("meta.name")] = Field(
        default_factory=list
    )


class HasFoldedKeyField(VcoModel):
    segments: Annotated[list[SegmentMeta], KeyField("name", fold_case=True)] = Field(
        default_factory=list
    )


class TestKeyFieldMarker:
    def test_processed_marker_in_metadata(self) -> None:
        fi = HasKeyField.model_fields["segments"]
        spec = get_key_spec(fi)
        assert spec is not None
        assert spec.paths == ("name",)

    def test_nested_key_path(self) -> None:
        fi = HasDottedKeyField.model_fields["segments"]
        spec = get_key_spec(fi)
        assert spec is not None
        assert spec.paths == ("meta.name",)

    def test_fold_case_is_kept_in_the_spec(self) -> None:
        folded = get_key_spec(HasFoldedKeyField.model_fields["segments"])
        exact = get_key_spec(HasKeyField.model_fields["segments"])
        assert folded is not None
        assert exact is not None
        assert folded.fold_case is True
        assert exact.fold_case is False

    def test_model_still_validates_list(self) -> None:
        m = HasKeyField(segments=[SegmentMeta(name="Global", segment_id=0)])
        assert len(m.segments) == 1
        assert m.segments[0].name == "Global"

    def test_model_validates_from_dict(self) -> None:
        m = HasKeyField.model_validate(
            {"segments": [{"name": "Global", "segmentId": 0}]}
        )
        assert m.segments[0].name == "Global"


_FIRST_ID = "11111111-1111-4111-8111-111111111111"
_SECOND_ID = "22222222-2222-4222-8222-222222222222"
_THIRD_ID = "33333333-3333-4333-8333-333333333333"
_IDS = (_FIRST_ID, _SECOND_ID, _THIRD_ID)
_NAMES = ("Corp", "Lab", "Lab")


def _lan() -> VcoModel:
    networks = [
        {"vlanId": vlan, "name": name}
        for vlan, name in zip((10, 20, 30), _NAMES, strict=True)
    ]
    return LanSettings.model_validate({"networks": networks})


def _ospf() -> VcoModel:
    areas = [
        {"id": area, "name": name} for area, name in zip((1, 2, 3), _NAMES, strict=True)
    ]
    return SegmentOspf.model_validate({"areas": areas})


def _netflow_filters() -> VcoModel:
    filters = [
        {"logicalId": logical_id, "name": name, "type": "IPFIX"}
        for logical_id, name in zip(_IDS, _NAMES, strict=True)
    ]
    return NetflowCollector.model_validate(
        {
            "logicalId": _FIRST_ID,
            "name": "Collector",
            "type": "IPFIX",
            "filterList": filters,
        }
    )


def _netflow_collectors() -> VcoModel:
    collectors = [
        {"logicalId": logical_id, "name": name, "type": "IPFIX"}
        for logical_id, name in zip(_IDS, _NAMES, strict=True)
    ]
    return SegmentNetflow.model_validate({"enabled": False, "collectors": collectors})


@pytest.mark.parametrize(
    ("build", "field", "second_key"),
    [
        (_lan, "networks", "20"),
        (_ospf, "areas", "2"),
        (_netflow_filters, "filter_list", _SECOND_ID),
        (_netflow_collectors, "collectors", _SECOND_ID),
    ],
    ids=["lan-networks", "ospf-areas", "netflow-filters", "netflow-collectors"],
)
def test_an_item_with_a_name_is_named_by_it(
    build: Callable[[], VcoModel], field: str, second_key: str
) -> None:
    holder = build()
    assert item_selector(holder, field, 0) == "Corp"
    assert select_items(holder, field, "Corp") == [0]
    assert item_selector(holder, field, 1) == second_key


def test_an_ospf_area_read_without_a_name_is_selected_by_the_empty_text() -> None:
    holder = SegmentOspf.model_validate(
        {"areas": [{"id": 1, "name": "Corp"}, {"id": 2}]}
    )
    assert holder.areas[1].name == ""
    assert select_items(holder, "areas", "") == [1]
