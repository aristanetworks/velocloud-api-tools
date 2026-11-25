"""A profile module records the entries the pipeline adds to it, and plans against them.

``plan_module`` plans a profile module against its raw data plus its additions
(R'), so an added entry nobody edited is written exactly as the template, while
one module alone, without the pipeline, cannot change its set of segments.
"""

import copy
import json
import pickle
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.exceptions import (
    FixedListError,
    SegmentSetError,
)
from pyvelo.client.models.configuration.profile_segments import (
    AddedDefaults,
    AddedSegment,
    default_rules,
    new_segment_entry,
    with_additions,
)
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import (
    DeviceSettingsConfigurationModule,
    FirewallConfigurationModule,
    QosConfigurationModule,
    WanConfigurationModule,
)
from pyvelo.core import ConfigContext
from pyvelo.core.layers import Edge, Profile
from pyvelo.core.rebase import Path
from pyvelo.testing import make_module_json, store_of
from refs_helpers import SEG1_OBJECT, SEGMENT
from segment_helpers import (
    GLOBAL,
    GUEST,
    ds_entry,
    identity,
    profile_rows,
    qos_entry,
    segment_registry,
)

from ._type_support import MockVcoClientFactory

GUEST_QOS = 'segments["Guest"]'


def profile_modules(
    ds: list[dict[str, Any]],
    qos: list[dict[str, Any]],
    firewall: list[dict[str, Any]],
) -> tuple[
    DeviceSettingsConfigurationModule,
    QosConfigurationModule[Profile],
    FirewallConfigurationModule[Profile],
]:
    """The three modules of a profile, read as a profile's are."""
    context = {"config_level": ConfigContext.PROFILE}
    rows = profile_rows(ds, qos, firewall)
    return (
        DeviceSettingsConfigurationModule.model_validate(rows[0], context=context),
        QosConfigurationModule[Profile].model_validate(rows[1]),
        FirewallConfigurationModule[Profile].model_validate(rows[2], context=context),
    )


def global_qos() -> QosConfigurationModule[Profile]:
    return profile_modules([ds_entry(GLOBAL)], [qos_entry(GLOBAL)], [])[1]


def global_ds() -> DeviceSettingsConfigurationModule:
    return profile_modules([ds_entry(GLOBAL)], [], [])[0]


def guest_qos_entry() -> dict[str, Any]:
    return new_segment_entry("QOS", identity(GUEST))


def add_guest_to_qos() -> tuple[QosConfigurationModule[Profile], dict[str, Any], Any]:
    module = global_qos()
    entry = guest_qos_entry()
    item = module.add_segment_entry(entry)
    return module, entry, item


# --- an added entry is the baseline -----------------------------------------


def test_an_added_entry_nobody_edited_is_written_as_the_template() -> None:
    module, entry, _ = add_guest_to_qos()
    plan = plan_module(module, via_pipeline=True)
    assert plan.operation == "update"
    assert plan.changes == ()
    assert plan.added_segments == ("Guest",)
    assert json.dumps(plan.data["segments"][1], sort_keys=False) == json.dumps(
        entry, sort_keys=False
    )
    assert plan.data["segments"][0] == module.raw_data["segments"][0]  # pyright: ignore[reportIndexIssue]


def test_an_edit_to_an_added_entry_is_a_change_from_the_template() -> None:
    module, entry, item = add_guest_to_qos()
    original = entry["rules"][0]["name"]
    item.rules[0].name = "Box2"
    plan = plan_module(module, via_pipeline=True)
    (change,) = plan.changes
    assert change.path == Path.parse(f'{GUEST_QOS}.rules["{original}"].name')
    assert (change.before, change.after) == (original, "Box2")
    expected = copy.deepcopy(entry)
    expected["rules"][0]["name"] = "Box2"
    assert plan.data["segments"][1] == expected


def test_the_render_lists_added_segments() -> None:
    module, _, _ = add_guest_to_qos()
    plan = plan_module(module, via_pipeline=True)
    assert "  added segments: Guest" in plan.render().splitlines()


def test_the_render_of_a_plan_with_nothing_added_has_no_such_line() -> None:
    plan = plan_module(global_qos())
    assert "added segments" not in plan.render()
    assert plan.added_segments == ()


def test_added_defaults_are_part_of_the_baseline() -> None:
    entry = qos_entry(GLOBAL, defaults=MISSING)
    row = profile_rows([ds_entry(GLOBAL)], [entry], [])[1]
    module = QosConfigurationModule[Profile].model_validate(row)
    rules = new_segment_entry("QOS", identity(GLOBAL))["defaults"]
    module.add_default_entries(GLOBAL[0], rules)
    plan = plan_module(module)
    assert plan.operation == "update"
    assert plan.changes == ()
    assert plan.added_segments == ()
    assert plan.data["segments"][0]["defaults"] == rules
    (addition,) = module.additions
    assert isinstance(addition, AddedDefaults)
    assert (addition.segment_id, addition.name) == (0, "Global Segment")
    assert [r["name"] for r in addition.entries] == [r["name"] for r in rules]


def test_an_added_entry_with_its_template_defaults_is_not_a_fixed_list_error() -> None:
    module, entry, _ = add_guest_to_qos()
    assert len(entry["defaults"]) == 4
    plan = plan_module(module, via_pipeline=True)
    assert plan.data["segments"][1]["defaults"] == entry["defaults"]


def test_an_edit_to_the_fixed_part_of_an_added_default_is_refused() -> None:
    module, _, item = add_guest_to_qos()
    item.defaults[0].match.proto = 99
    with pytest.raises(FixedListError):
        plan_module(module, via_pipeline=True)


# --- the refs are not planned against the additions -------------------------


async def test_an_added_device_settings_entry_adds_its_segment_row() -> None:
    module = global_ds()
    module.add_segment_entry(new_segment_entry("deviceSettings", identity(GUEST)))
    registry = await segment_registry(GLOBAL, GUEST)
    plan = plan_module(module, via_pipeline=True, resolvers=registry)
    assert plan.operation == "update"
    assert plan.changes == ()
    (row_change,) = plan.ref_changes
    assert row_change.added
    assert row_change.name == SEGMENT
    assert row_change.row["enterpriseObjectId"] == SEG1_OBJECT
    assert plan.refs is not None
    as_read = module.raw_refs[SEGMENT]  # pyright: ignore[reportIndexIssue]
    rows = plan.refs[SEGMENT]
    assert rows == [as_read, row_change.row]


# --- one module alone cannot change its segments ----------------------------


def test_a_one_module_plan_refuses_a_new_segment() -> None:
    module, _, _ = add_guest_to_qos()
    with pytest.raises(SegmentSetError) as caught:
        plan_module(module)
    error = caught.value
    assert (error.module_name, error.segments, error.modules) == (
        "QOS",
        ("Guest",),
        ("QOS",),
    )
    assert '{"segments": {"Guest": {"_op": "add"}}}' in str(error)
    assert 'pipeline.assign_segment(state, "Guest")' in str(error)


def test_a_one_module_plan_refuses_a_removed_segment() -> None:
    module = global_qos()
    module.data.segments.clear()
    with pytest.raises(SegmentSetError) as caught:
        plan_module(module)
    error = caught.value
    assert error.segments == ("Global Segment",)
    message = str(error)
    assert "removed from all three modules together, which is not supported" in message
    assert "_op" not in message
    assert "assign_segment" not in message


def test_a_removed_segment_is_planned_through_the_pipeline() -> None:
    module = global_qos()
    module.data.segments.clear()
    plan = plan_module(module, via_pipeline=True)
    assert plan.operation == "update"
    assert plan.data["segments"] == []


async def test_update_module_refuses_a_segment_set_change(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module = global_qos()
    store = store_of(module)
    client = await mock_vco_client(store)
    data = module.mutable_data()
    data.segments.append(
        type(data.segments[0]).model_validate(
            guest_qos_entry(), context={"config_level": ConfigContext.PROFILE}
        )
    )
    with pytest.raises(SegmentSetError) as caught:
        _ = await client.update_module(module, data)
    assert caught.value.segments == ("Guest",)
    assert [method for method, _ in store.requests] == []


def raw_with_a_guest_segment() -> dict[str, Any]:
    """The data of the Global QoS module with a Guest entry, as a plain dict."""
    raw = copy.deepcopy(global_qos().raw_data)
    assert isinstance(raw, dict)
    return {"segments": [*raw["segments"], guest_qos_entry()]}


def test_a_dict_with_a_new_segment_is_refused_at_the_profile_level() -> None:
    module = global_qos()
    with pytest.raises(SegmentSetError) as caught:
        plan_module(module, raw_with_a_guest_segment())
    assert caught.value.segments == ("Guest",)


def test_a_dict_without_a_segment_is_refused_at_the_profile_level() -> None:
    module = global_qos()
    with pytest.raises(SegmentSetError) as caught:
        plan_module(module, {"segments": []})
    assert caught.value.segments == ("Global Segment",)


def test_a_hand_built_model_with_a_new_segment_is_refused_at_the_profile_level() -> (
    None
):
    module = global_qos()
    data = type(module.data).model_validate(raw_with_a_guest_segment())
    assert (
        data.config_context is ConfigContext.PROFILE
    )  # a layer, not a context, now says so
    with pytest.raises(SegmentSetError) as caught:
        plan_module(module, data)
    assert caught.value.segments == ("Guest",)


async def test_update_module_refuses_a_segment_set_change_of_a_hand_built_model(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    module = global_qos()
    store = store_of(module)
    client = await mock_vco_client(store)
    data = type(module.data).model_validate(raw_with_a_guest_segment())
    assert (
        data.config_context is ConfigContext.PROFILE
    )  # a layer, not a context, now says so
    with pytest.raises(SegmentSetError) as caught:
        _ = await client.update_module(module, data)
    assert caught.value.segments == ("Guest",)
    assert [method for method, _ in store.requests] == []


def test_the_edge_level_is_not_refused() -> None:
    profile = global_qos()
    edge_row = make_module_json(
        "QOS", {"segments": []}, module_id=21, configuration_id=200
    )
    edge = QosConfigurationModule[Edge].model_validate(edge_row)
    plan = plan_module(edge, profile=profile, level=ConfigContext.EDGE)
    assert plan.operation == "none"
    assert plan.reconciled_segments == ("Global Segment",)
    edge.data.bind_profile(profile.raw_data)
    _ = edge.data.segment("Global Segment")  # a lookup adds its placeholder
    again = plan_module(edge, profile=profile, level=ConfigContext.EDGE)
    assert again.operation == "none"


# --- the module methods ------------------------------------------------------


def test_add_segment_entry_returns_the_item_and_records_the_addition() -> None:
    module, entry, item = add_guest_to_qos()
    assert module.data.segments[1] is item
    assert item.segment.name == "Guest"
    (addition,) = module.additions
    assert isinstance(addition, AddedSegment)
    assert (addition.segment_id, addition.name) == (2, "Guest")
    assert addition.entry == entry
    entry["rules"][0]["name"] = "changed after the call"
    assert addition.entry != entry  # the record holds a copy


def test_add_segment_entry_refuses_an_edge_module_and_a_segment_already_there() -> None:
    edge = QosConfigurationModule[Edge].model_validate(
        make_module_json("QOS", {"segments": []}, module_id=21, configuration_id=200)
    )
    with pytest.raises(ValueError, match="profile"):
        _ = edge.add_segment_entry(guest_qos_entry())
    module = global_qos()
    with pytest.raises(ValueError, match="already"):
        _ = module.add_segment_entry(new_segment_entry("QOS", identity(GLOBAL)))
    assert module.additions == ()
    module.add_segment_entry(guest_qos_entry())
    with pytest.raises(ValueError, match="already"):
        _ = module.add_segment_entry(guest_qos_entry())
    assert len(module.additions) == 1


def test_add_segment_entry_refuses_a_module_without_segments() -> None:
    wan = WanConfigurationModule.model_validate(
        make_module_json("WAN", {"links": []}, module_id=30, configuration_id=100),
        context={"config_level": ConfigContext.PROFILE},
    )
    with pytest.raises(ValueError, match="segments"):
        _ = wan.add_segment_entry(guest_qos_entry())


def test_add_default_entries_is_for_qos_and_a_segment_it_holds() -> None:
    ds = global_ds()
    with pytest.raises(TypeError, match="QoS"):
        ds.add_default_entries(0, default_rules())
    module = global_qos()
    with pytest.raises(ValueError, match="segment"):
        module.add_default_entries(2, default_rules())
    assert module.additions == ()


def test_add_default_entries_parses_the_rules_into_the_segment() -> None:
    row = profile_rows([ds_entry(GLOBAL)], [qos_entry(GLOBAL, defaults=MISSING)], [])[1]
    module = QosConfigurationModule[Profile].model_validate(row)
    module.add_default_entries(0, default_rules())
    defaults = module.data.segments[0].defaults
    assert isinstance(defaults, list)
    assert len(defaults) == 4
    assert all(isinstance(rule, QosRule) for rule in defaults)


def test_additions_survive_deepcopy_and_pickle() -> None:
    module, _, _ = add_guest_to_qos()
    module.add_default_entries(0, [])
    for copied in (
        copy.deepcopy(module),
        pickle.loads(pickle.dumps(module)),
    ):
        assert copied.additions == module.additions
        (segment, defaults) = copied.additions
        assert isinstance(segment, AddedSegment)
        assert isinstance(defaults, AddedDefaults)
        assert plan_module(copied, via_pipeline=True).added_segments == ("Guest",)
    deep = copy.deepcopy(module)
    assert isinstance(deep.additions[0], AddedSegment)
    assert deep.additions[0].entry is not module.additions[0].entry  # pyright: ignore[reportAttributeAccessIssue]


def test_with_data_parsed_from_carries_only_the_given_additions() -> None:
    module, _, _ = add_guest_to_qos()
    raw = copy.deepcopy(module.raw_data)
    carried = module.with_data_parsed_from(raw)
    assert carried.additions == ()
    assert module.additions != ()  # the original keeps its own
    given = module.additions
    again = module.with_data_parsed_from(with_additions(raw, given), additions=given)
    assert again.additions == given
    # the copy's additions are its own: another copy given none has none
    assert module.with_data_parsed_from(raw).additions == ()
    assert again.additions == given


# --- with_additions ----------------------------------------------------------


def test_with_additions_appends_an_entry_and_leaves_raw_alone() -> None:
    raw = {"segments": [qos_entry(GLOBAL)]}
    before = copy.deepcopy(raw)
    entry = guest_qos_entry()
    added = with_additions(raw, [AddedSegment(2, "Guest", entry)])
    assert raw == before
    assert added == {"segments": [*before["segments"], entry]}


def test_with_additions_makes_a_list_of_an_absent_or_null_segments() -> None:
    entry = guest_qos_entry()
    for raw in ({}, {"segments": None}):
        added = with_additions(raw, [AddedSegment(2, "Guest", entry)])
        assert added == {"segments": [entry]}


def test_with_additions_inserts_defaults_into_an_unset_list() -> None:
    raw = {"segments": [qos_entry(GLOBAL, defaults=MISSING)]}
    rules = default_rules()
    added = with_additions(raw, [AddedDefaults(0, "Global Segment", tuple(rules))])
    assert added["segments"][0]["defaults"] == rules  # pyright: ignore[reportIndexIssue]
    assert "defaults" not in raw["segments"][0]


def test_with_additions_refuses_a_segment_that_is_there_and_defaults_that_have_none() -> (  # noqa: E501 (a test's name)
    None
):
    raw = {"segments": [qos_entry(GLOBAL)]}
    with pytest.raises(ValueError, match="already"):
        _ = with_additions(raw, [AddedSegment(0, "Global Segment", qos_entry(GLOBAL))])
    with pytest.raises(ValueError, match="no segment"):
        _ = with_additions(raw, [AddedDefaults(2, "Guest", ())])
