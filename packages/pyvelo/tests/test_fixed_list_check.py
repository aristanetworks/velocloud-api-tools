"""A payload that changes a fixed part of the QoS ``defaults`` is refused.

It is refused whatever made the change.
"""

import copy
from typing import Any, overload

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import FixedListError, LossyFieldError
from pyvelo.client.models.configuration.qos.root import QosData
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.core import ConfigContext, Edge, Profile
from pyvelo.core.fixed_list import fixed_list_violations
from pyvelo.core.rebase import Path
from pyvelo.testing import make_module_json
from segment_helpers import GLOBAL, GUEST, qos_entry

DEFAULTS = 'segments["Guest"].defaults'
FIRST = f'{DEFAULTS}["Default-Internet-UDP"]'


def data(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"segments": list(entries)}


def guest_defaults(payload: dict[str, Any]) -> list[dict[str, Any]]:
    (entry,) = payload["segments"]
    return entry["defaults"]


def violations(
    baseline: dict[str, Any], payload: dict[str, Any]
) -> list[tuple[Path, str]]:
    return fixed_list_violations(QosData, baseline, payload)


def test_untouched_defaults_pass() -> None:
    baseline = data(qos_entry(GUEST))
    assert violations(baseline, copy.deepcopy(baseline)) == []


def test_an_action_change_passes() -> None:
    baseline = data(qos_entry(GUEST))
    payload = copy.deepcopy(baseline)
    guest_defaults(payload)[0]["action"]["routeType"] = "gateway"
    guest_defaults(payload)[3]["action"] = {"changed": True}
    assert violations(baseline, payload) == []


def test_a_match_change_is_refused_at_the_rule() -> None:
    baseline = data(qos_entry(GUEST))
    payload = copy.deepcopy(baseline)
    guest_defaults(payload)[0]["match"]["proto"] = 99
    [(path, reason)] = violations(baseline, payload)
    assert path == Path.parse(FIRST)
    assert "match" in reason


def test_a_removed_rule_is_refused() -> None:
    baseline = data(qos_entry(GUEST))
    payload = copy.deepcopy(baseline)
    del guest_defaults(payload)[1]
    assert violations(baseline, payload) == [
        (Path.parse(DEFAULTS), "an item added, removed, renamed or moved")
    ]


def test_a_reordered_list_is_refused() -> None:
    baseline = data(qos_entry(GUEST))
    payload = copy.deepcopy(baseline)
    rules = guest_defaults(payload)
    rules[0], rules[1] = rules[1], rules[0]
    assert violations(baseline, payload) == [
        (Path.parse(DEFAULTS), "an item added, removed, renamed or moved")
    ]


def test_a_renamed_rule_is_refused() -> None:
    baseline = data(qos_entry(GUEST))
    payload = copy.deepcopy(baseline)
    guest_defaults(payload)[2]["name"] = "Renamed"
    assert [p for p, _ in violations(baseline, payload)] == [Path.parse(DEFAULTS)]


def test_a_list_gone_is_refused() -> None:
    baseline = data(qos_entry(GUEST))
    payload = data(qos_entry(GUEST, defaults=MISSING))
    assert violations(baseline, payload) == [(Path.parse(DEFAULTS), "the list is gone")]


def test_a_list_built_where_there_was_none_is_refused() -> None:
    baseline = data(qos_entry(GUEST, defaults=MISSING))
    payload = data(qos_entry(GUEST))
    assert violations(baseline, payload) == [
        (Path.parse(DEFAULTS), "a list built where there was none")
    ]


def test_a_new_segment_with_defaults_is_refused() -> None:
    baseline = data(qos_entry(GLOBAL))
    payload = data(qos_entry(GLOBAL), qos_entry(GUEST))
    assert violations(baseline, payload) == [
        (Path.parse(DEFAULTS), "a list built where there was none")
    ]


def test_no_list_on_either_side_passes() -> None:
    for empty in (MISSING, None):
        baseline = data(qos_entry(GUEST, defaults=empty))
        assert violations(baseline, copy.deepcopy(baseline)) == []
    assert (
        violations(
            data(qos_entry(GUEST, defaults=MISSING)),
            data(qos_entry(GUEST, defaults=None)),
        )
        == []
    )


def test_an_edit_to_the_rules_beside_the_defaults_passes() -> None:
    baseline = data(qos_entry(GUEST))
    payload = copy.deepcopy(baseline)
    payload["segments"][0]["rules"].append({"name": "new"})
    assert violations(baseline, payload) == []


def test_items_pair_by_key_so_a_segment_moved_in_its_list_passes() -> None:
    baseline = data(qos_entry(GLOBAL), qos_entry(GUEST))
    payload = data(*reversed(baseline["segments"]))
    assert violations(baseline, copy.deepcopy(payload)) == []


# --- through plan_module ----------------------------------------------------


@overload
def qos_module(
    *entries: dict[str, Any], layer: type[Edge]
) -> QosConfigurationModule[Edge]: ...
@overload
def qos_module(
    *entries: dict[str, Any], layer: type[Profile]
) -> QosConfigurationModule[Profile]: ...
def qos_module(
    *entries: dict[str, Any], layer: type[Edge] | type[Profile]
) -> QosConfigurationModule[Edge] | QosConfigurationModule[Profile]:
    """A QoS module of the layer *layer*, read with *entries* as its segments."""
    row = make_module_json("QOS", copy.deepcopy(data(*entries)), refs={})
    if layer is Edge:
        return QosConfigurationModule[Edge].model_validate(row)
    return QosConfigurationModule[Profile].model_validate(row)


def test_plan_module_raises_fixed_list_error_for_an_assigned_match_edit() -> None:
    module = qos_module(qos_entry(GUEST), layer=Profile)
    module.data.segments[0].defaults[0].match.proto = 99  # pyright: ignore[reportIndexIssue]
    with pytest.raises(FixedListError) as caught:
        plan_module(module, level=ConfigContext.PROFILE)
    assert caught.value.module_name == "QOS"
    assert caught.value.paths == (Path.parse(FIRST),)
    assert f"{Path.parse(FIRST)}: match" in str(caught.value)


def test_plan_module_passes_an_action_assignment() -> None:
    module = qos_module(qos_entry(GUEST), layer=Profile)
    module.data.segments[0].defaults[0].action.route_type = "gateway"  # pyright: ignore[reportIndexIssue, reportAttributeAccessIssue]
    plan = plan_module(module, level=ConfigContext.PROFILE)
    assert plan.operation == "update"


def test_plan_module_raises_for_a_rule_appended_to_defaults() -> None:
    module = qos_module(qos_entry(GUEST), layer=Profile)
    defaults = module.data.segments[0].defaults
    assert isinstance(defaults, list)
    defaults.append(defaults[0].model_copy(update={"name": "Extra"}))
    with pytest.raises(FixedListError) as caught:
        plan_module(module, level=ConfigContext.PROFILE)
    assert caught.value.paths == (Path.parse(DEFAULTS),)


def test_the_message_says_what_can_change_and_that_missing_entries_are_repaired() -> (
    None
):
    module = qos_module(qos_entry(GUEST), layer=Profile)
    module.data.segments[0].defaults[0].match.proto = 99  # pyright: ignore[reportIndexIssue]
    with pytest.raises(FixedListError) as caught:
        plan_module(module, level=ConfigContext.PROFILE)
    text = str(caught.value)
    assert (
        '{"segments": {"<segment>": {"defaults": {"<rule>": {"action": {...}}}}}}'
        in text
    )
    assert "repaired" in text


def test_the_check_runs_at_the_edge_level_too() -> None:
    entry = qos_entry(GUEST)
    entry["rules"] = [
        {
            "name": "r1",
            "match": {},
            "action": {
                "edge2EdgeRouteAction": {},
                "edge2DataCenterRouteAction": {},
                "edge2CloudRouteAction": {},
                "QoS": {},
            },
        },
    ]
    module = qos_module(entry, layer=Edge)
    # An edge write dumps no `defaults`: only the raw data keeps them.
    module.data.segments[0].rules[0].name = "r2"
    assert plan_module(module, level=ConfigContext.EDGE).operation == "update"

    changed = copy.deepcopy(data(entry))
    changed["segments"][0]["defaults"][0]["match"]["proto"] = 99
    with pytest.raises(FixedListError) as caught:
        plan_module(module, changed, level=ConfigContext.EDGE)
    assert caught.value.paths == (Path.parse(FIRST),)


def test_a_plain_dict_data_that_leaves_the_defaults_alone_passes() -> None:
    entry = qos_entry(GUEST)
    module = qos_module(entry, layer=Profile)
    assert (
        plan_module(module, data(entry), level=ConfigContext.PROFILE).operation
        == "none"
    )


# --- the edge layer has no `defaults` ------------------------------------------


def test_an_edge_segment_holds_no_defaults_and_a_write_keeps_the_raw_ones() -> None:
    entry = qos_entry(GUEST)
    raw_defaults = copy.deepcopy(entry["defaults"])
    module = qos_module(entry, layer=Edge)
    segment = module.data.segments[0]

    # The edge layer reads no `defaults` and dumps none.
    assert segment.defaults is MISSING  # pyright: ignore[reportAttributeAccessIssue]
    assert "defaults" not in segment.model_dump(by_alias=True, mode="json")

    # Nothing is planned from the unedited segment, and the raw `defaults` stay.
    plan = plan_module(module, level=ConfigContext.EDGE)
    assert (plan.operation, plan.data) == ("none", data(entry))

    # An edit through the model is refused as lossy, never written.
    segment.defaults = []
    with pytest.raises(LossyFieldError, match="defaults"):
        plan_module(module, level=ConfigContext.EDGE)
    segment.defaults = MISSING

    # A write for another edit sends the raw `defaults` as read.
    segment.rules.append(QosRule.model_validate({**entry["defaults"][0], "name": "r1"}))
    plan = plan_module(module, level=ConfigContext.EDGE)
    assert plan.operation == "update"
    assert guest_defaults(plan.data) == raw_defaults
    assert module.raw_data == data(entry)

    # The profile layer holds them.
    profile = qos_module(qos_entry(GUEST), layer=Profile)
    held = profile.data.segments[0].defaults
    assert isinstance(held, list)
    assert len(held) == len(raw_defaults)
    assert "defaults" in profile.data.segments[0].model_dump(by_alias=True, mode="json")
