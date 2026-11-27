"""A FixedList takes an edit of its editable fields only.

Entries, keys and order are fixed.
"""

import copy
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from pydantic import Field
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.core import (
    FixedList,
    KeyField,
    PatchError,
    Profile,
    VcoModel,
    add,
    at,
    delete,
    get_fixed_list,
    move,
)
from segment_helpers import GUEST, qos_entry

from .patch_models import FixedRule, FixedSegment

IDS = [uuid.uuid4() for _ in range(3)]


def segment() -> FixedSegment:
    return FixedSegment(
        defaults=[
            FixedRule(
                name=name,
                rule_id=IDS[index],
                match={"port": index},
                action={"route": "edge"},
            )
            for index, name in enumerate(("Alpha", "Beta", "Gamma"))
        ]
    )


def refusal(model: VcoModel, patch: dict[str, object]) -> PatchError:
    with pytest.raises(PatchError) as exc:
        _ = model.merge_patch(patch)
    assert exc.value.kind == "fixed_list"
    return exc.value


def given(index: int, **changes: Any) -> dict[str, Any]:
    """The JSON of item *index* of ``segment()``, with *changes* (and its ID)."""
    item = segment().defaults[index]  # pyright: ignore[reportIndexIssue]
    return {**item.model_dump(mode="json"), **changes}


def test_the_marker_is_read_from_the_field() -> None:
    fixed = get_fixed_list(FixedSegment.model_fields["defaults"])
    assert fixed == FixedList(editable=("action",))
    assert get_fixed_list(FixedSegment.model_fields["label"]) is None


def test_an_action_change_is_merged() -> None:
    seg = segment()
    _ = seg.merge_patch({"defaults": {"Beta": {"action": {"route": "cloud"}}}})
    assert seg.defaults[1].action == {"route": "cloud"}  # pyright: ignore[reportIndexIssue]
    assert [r.name for r in seg.defaults] == ["Alpha", "Beta", "Gamma"]  # pyright: ignore[reportGeneralTypeIssues]


def test_a_change_selected_by_the_alt_key_is_merged() -> None:
    seg = segment()
    _ = seg.merge_patch({"defaults": {str(IDS[2]): {"action": {"route": "cloud"}}}})
    assert seg.defaults[2].action == {"route": "cloud"}  # pyright: ignore[reportIndexIssue]


def test_a_match_change_is_refused() -> None:
    error = refusal(segment(), {"defaults": {"Beta": {"match": {"port": 9}}}})
    assert error.path == "defaults[Beta]"
    assert "match" in error.message
    assert "action" in error.message
    assert '{"defaults": {"Beta": {"action": {...}}}}' in error.message


def test_a_change_of_a_fixed_and_an_editable_field_is_refused() -> None:
    error = refusal(
        segment(),
        {"defaults": {"Beta": {"match": {"port": 9}, "action": {"route": "x"}}}},
    )
    assert error.path == "defaults[Beta]"


def test_a_rename_is_refused() -> None:
    error = refusal(segment(), {"defaults": {"Beta": {"name": "Delta"}}})
    assert error.path == "defaults[Beta]"
    assert "name" in error.message


def test_an_alt_key_change_is_refused() -> None:
    error = refusal(segment(), {"defaults": {"Beta": {"rule_id": str(uuid.uuid4())}}})
    assert error.path == "defaults[Beta]"
    assert "ruleId" in error.message


@pytest.mark.parametrize(
    ("op", "path"),
    [
        (
            {"Delta": add({"action": {"route": "x"}})},
            "defaults[Delta]",
        ),
        ({"Beta": delete()}, "defaults[Beta]"),
        ({"Beta": move(first=True)}, "defaults[Beta]"),
        (
            {"Beta": {"_position": "last", "action": {"route": "x"}}},
            "defaults[Beta]",
        ),
        ({"+": add({"name": "Delta"})}, 'defaults["+"]'),
        ({"Beta": {"_position": {"before": "Alpha"}}}, "defaults[Beta]"),
        ({at(1): delete()}, "defaults[Beta]"),
    ],
    ids=["add", "delete", "move", "position", "add-plus", "before", "by-position"],
)
def test_add_delete_move_and_a_position_are_refused(
    op: dict[object, object], path: str
) -> None:
    error = refusal(segment(), {"defaults": op})
    assert error.path == path
    assert "only" in error.message
    assert '{"defaults": {' in error.message


def test_add_to_an_unset_list_is_refused() -> None:
    error = refusal(FixedSegment(), {"defaults": {"A": add()}})
    assert error.path == "defaults[A]"


def test_a_list_value_changed_only_in_actions_is_taken() -> None:
    seg = segment()
    value = [given(i) for i in range(3)]
    value[1]["action"] = {"route": "cloud"}
    _ = seg.merge_patch({"defaults": value})
    assert [r.action for r in seg.defaults] == [  # pyright: ignore[reportGeneralTypeIssues]
        {"route": "edge"},
        {"route": "cloud"},
        {"route": "edge"},
    ]
    assert [r.rule_id for r in seg.defaults] == IDS  # pyright: ignore[reportGeneralTypeIssues]


def without_alt_key(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in ("rule_id", "ruleId")}


def test_a_list_value_without_alt_keys_keeps_the_current_ones() -> None:
    seg = segment()
    value = [without_alt_key(given(i)) for i in range(3)]
    assert all("ruleId" not in item and "rule_id" not in item for item in value)
    value[0]["action"] = {"route": "cloud"}
    _ = seg.merge_patch({"defaults": value})
    assert [r.rule_id for r in seg.defaults] == IDS  # pyright: ignore[reportGeneralTypeIssues]
    assert seg.defaults[0].action == {"route": "cloud"}  # pyright: ignore[reportIndexIssue]


@pytest.mark.parametrize("name", ["rule_id", "ruleId"])
def test_a_list_value_that_gives_another_alt_key_under_any_name_is_refused(
    name: str,
) -> None:
    value = [without_alt_key(given(i)) for i in range(3)]
    value[1][name] = str(uuid.uuid4())
    error = refusal(segment(), {"defaults": value})
    assert error.path == "defaults[Beta]"


@pytest.mark.parametrize("name", ["rule_id", "ruleId"])
def test_a_list_value_that_gives_the_current_alt_key_under_any_name_is_taken(
    name: str,
) -> None:
    seg = segment()
    value = [without_alt_key(given(i)) for i in range(3)]
    for index, item in enumerate(value):
        item[name] = str(IDS[index])
    value[2]["action"] = {"route": "cloud"}
    _ = seg.merge_patch({"defaults": value})
    assert [r.rule_id for r in seg.defaults] == IDS  # pyright: ignore[reportGeneralTypeIssues]
    assert seg.defaults[2].action == {"route": "cloud"}  # pyright: ignore[reportIndexIssue]


def test_a_list_value_that_gives_another_alt_key_says_to_leave_it_out() -> None:
    value = [without_alt_key(given(i)) for i in range(3)]
    value[1]["ruleId"] = str(uuid.uuid4())
    error = refusal(segment(), {"defaults": value})
    assert "changes ruleId of" in error.message
    assert "Leave `ruleId` out of the item and it keeps the current one" in (
        error.message
    )


def test_a_list_value_that_changes_a_field_other_than_the_alt_key_does_not_say_it() -> (
    None
):
    error = refusal(segment(), {"defaults": [given(0), given(1, match={}), given(2)]})
    assert "keeps the current one" not in error.message


def _reordered() -> list[dict[str, Any]]:
    return [given(1), given(0), given(2)]


def _dropped() -> list[dict[str, Any]]:
    return [given(0), given(1)]


def _added() -> list[dict[str, Any]]:
    return [given(0), given(1), given(2), {"name": "Delta"}]


def _renamed() -> list[dict[str, Any]]:
    return [given(0), given(1, name="Delta"), given(2)]


def _match_changed() -> list[dict[str, Any]]:
    return [given(0), given(1, match={"port": 9}), given(2)]


def _other_alt_key() -> list[dict[str, Any]]:
    return [given(0), given(1, ruleId=str(uuid.uuid4())), given(2)]


@pytest.mark.parametrize(
    "build",
    [_reordered, _dropped, _added, _renamed, _match_changed, _other_alt_key],
    ids=["reorders", "drops", "adds", "renames", "match", "alt-key"],
)
def test_a_list_value_that_reorders_drops_adds_or_changes_a_match_is_refused(
    build: Callable[[], list[dict[str, Any]]],
) -> None:
    error = refusal(segment(), {"defaults": build()})
    assert error.path.startswith("defaults")
    assert "action" in error.message


def test_a_list_value_item_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(PatchError):
        _ = segment().merge_patch({"defaults": ["Alpha", "Beta", "Gamma"]})


def test_a_list_value_where_there_was_none_is_refused() -> None:
    seg = FixedSegment()
    error = refusal(seg, {"defaults": [given(0)]})
    assert error.path == "defaults"
    assert "none" in error.message or "unset" in error.message


def test_a_list_value_where_the_list_is_none_is_refused() -> None:
    seg = FixedSegment.model_construct(defaults=None)
    error = refusal(seg, {"defaults": [given(0)]})
    assert error.path == "defaults"


@pytest.mark.parametrize(
    "patch",
    [
        {"label": "x", "defaults": {"Beta": {"match": {"port": 9}}}},
        {"label": "x", "defaults": {"Beta": {"action": {"a": "b"}}, "Gamma": delete()}},
        {"label": "x", "defaults": {"Beta": {"name": "Delta"}}},
        {"label": "x", "defaults": {"Beta": {"rule_id": str(uuid.uuid4())}}},
        {"label": "x", "defaults": [given(1), given(0), given(2)]},
    ],
    ids=["match", "delete", "rename", "alt-key", "list-value"],
)
def test_each_refusal_leaves_the_model_as_it_was(patch: dict[str, object]) -> None:
    seg = segment()
    dump = copy.deepcopy(seg.model_dump())
    fields_set = set(seg.model_fields_set)
    _ = refusal(seg, patch)
    assert seg.model_dump() == dump
    assert seg.model_fields_set == fields_set


class Holder(VcoModel):
    segments: Annotated[list[FixedSegment], KeyField("label")] = Field(
        default_factory=list
    )


def test_operations_on_the_fixed_list_of_an_item_the_patch_adds_are_refused() -> None:
    holder = Holder()
    error = refusal(
        holder,
        {
            "segments": {
                "S": add({"defaults": {"A": add({"match": {"port": 1}})}}),
            }
        },
    )
    assert error.path == "segments[S].defaults[A]"
    assert holder.segments == []


def test_a_list_value_in_an_item_the_patch_adds_is_left_to_plan() -> None:
    holder = Holder()
    _ = holder.merge_patch(
        {"segments": {"S": add({"defaults": [{"name": "A", "match": {"port": 1}}]})}}
    )
    assert [r.name for r in holder.segments[0].defaults] == ["A"]  # pyright: ignore[reportGeneralTypeIssues]


def qos() -> QosData[Profile]:
    return QosData[Profile].model_validate({"segments": [qos_entry(GUEST)]})


def test_qos_defaults_take_an_action_edit_by_name() -> None:
    data = qos()
    _ = data.merge_patch(
        {
            "segments": {
                "Guest": {
                    "defaults": {
                        "Default-Any-UDP": {"action": {"routeType": "edge2Cloud"}}
                    }
                }
            }
        }
    )
    defaults = {r.name: r for r in data.segments[0].defaults}  # pyright: ignore[reportGeneralTypeIssues]
    assert defaults["Default-Any-UDP"].action.route_type == "edge2Cloud"
    assert defaults["Default-Any-Other"].action.route_type == "edge2Any"


def test_qos_defaults_refuse_a_match_edit() -> None:
    data = qos()
    dump = copy.deepcopy(data.model_dump())
    error = refusal(
        data,
        {
            "segments": {
                "Guest": {
                    "defaults": {"Default-Any-UDP": {"match": {"sip": "10.0.0.1"}}}
                }
            }
        },
    )
    assert error.path == "segments[Guest].defaults[Default-Any-UDP]"
    assert data.model_dump() == dump


def test_qos_rules_of_a_segment_stay_editable() -> None:
    data = qos()
    rule = copy.deepcopy(qos_entry(GUEST)["defaults"][0])
    rule["name"] = "R"
    rule["ruleLogicalId"] = str(uuid.uuid4())
    _ = data.merge_patch({"segments": {"Guest": {"rules": {"R": add(rule)}}}})
    assert [r.name for r in data.segments[0].rules] == ["R"]


def test_a_qos_round_trip_without_rule_ids_keeps_the_rule_ids() -> None:
    entry = qos_entry(GUEST)
    sent_ids = [r["ruleLogicalId"] for r in entry["defaults"]]
    data = QosData[Profile].model_validate({"segments": [entry]})
    sent = copy.deepcopy(entry["defaults"])
    for rule in sent:
        del rule["ruleLogicalId"]
    sent[2]["action"]["routeType"] = "edge2DataCenter"
    _ = data.merge_patch({"segments": {"Guest": {"defaults": sent}}})
    defaults = data.segments[0].defaults
    assert isinstance(defaults, list)
    assert [str(r.rule_logical_id) for r in defaults] == sent_ids
    assert defaults[2].action.route_type == "edge2DataCenter"
