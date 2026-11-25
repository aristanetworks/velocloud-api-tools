"""A QoS segment of a profile holds the template's four ``defaults``; a write repairs one that lacks some.

``plan_module`` pairs the ``defaults`` of each segment with the template's
entries, one to one. The entries nothing pairs with are added when the module
is written for another reason, each with a warning either way. A list the
template does not describe is left alone, with a warning.
"""

import copy
import uuid
from typing import Any, cast

from pydantic import MISSING
from pyvelo.client.models.configuration.profile_segments import (
    DEFAULT_NAMES,
    default_rules,
    insert_defaults,
    new_segment_entry,
    pair_defaults,
    repaired_entry,
)
from pyvelo.client.models.configuration.qos.rule import QosRule
from pyvelo.client.module_write import MissingDefaults, plan_module
from pyvelo.client.schemas.configuration import QosConfigurationModule
from pyvelo.core import ConfigContext, Edge, Profile
from pyvelo.testing import make_module_json, store_of
from refs_helpers import QOS_ID
from segment_helpers import GLOBAL, GUEST, ds_entry, identity, profile_rows, qos_entry

from ._type_support import MockVcoClientFactory

TEMPLATE = {rule["name"]: rule for rule in default_rules()}
INTERNET_UDP, INTERNET_OTHER, ANY_UDP, ANY_OTHER = DEFAULT_NAMES
GUEST_ID = GUEST[0]
GUEST_DEFAULTS = 'segments["Guest"].defaults'


def present(*names: str) -> list[dict[str, Any]]:
    """The template entries *names*, each with an ID, in the order given."""
    rules = [copy.deepcopy(TEMPLATE[name]) for name in names]
    for rule in rules:
        rule["ruleLogicalId"] = str(uuid.uuid4())
    return rules


def rules_of_template() -> list[dict[str, Any]]:
    return new_segment_entry("QOS", identity(GLOBAL))["rules"]


def qos_module(
    guest_defaults: list[dict[str, Any]] | None | MISSING,
) -> QosConfigurationModule[Profile]:
    """A profile QoS module of Global (complete) and Guest (with *guest_defaults*)."""
    rows = profile_rows(
        [ds_entry(GLOBAL), ds_entry(GUEST)],
        [
            qos_entry(GLOBAL, rules=rules_of_template()),
            qos_entry(GUEST, defaults=guest_defaults, rules=rules_of_template()),
        ],
        [],
    )
    return QosConfigurationModule[Profile].model_validate(rows[1])


def raw_of(
    module: QosConfigurationModule[Edge] | QosConfigurationModule[Profile],
) -> dict[str, Any]:
    return cast("dict[str, Any]", module.raw_data)


def edit_global(module: QosConfigurationModule[Profile]) -> None:
    """An edit outside the defaults, so that the module is written."""
    module.data.segments[0].rules[0].name = "Edited"


def guest_defaults_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data["segments"][1]["defaults"]


def names_of(rules: list[dict[str, Any]]) -> list[str]:
    return [rule["name"] for rule in rules]


# --- the pairing --------------------------------------------------------------


def test_pair_defaults_of_a_complete_list() -> None:
    pairing = pair_defaults(present(*DEFAULT_NAMES))
    assert (pairing.missing, pairing.unknown, pairing.clash) == ((), (), ())


def test_pair_defaults_of_no_list_misses_all_four() -> None:
    for defaults in (MISSING, None, []):
        pairing = pair_defaults(defaults)
        assert pairing.missing == DEFAULT_NAMES
        assert (pairing.unknown, pairing.clash) == ((), ())


def test_pair_defaults_pairs_a_shared_match_one_to_one() -> None:
    pairing = pair_defaults(present(ANY_UDP))
    assert pairing.missing == (INTERNET_UDP, INTERNET_OTHER, ANY_OTHER)


def test_insert_defaults_places_each_after_its_nearest_earlier_pair() -> None:
    items: list[object] = [*present(INTERNET_OTHER, ANY_OTHER)]
    added = [copy.deepcopy(TEMPLATE[name]) for name in (INTERNET_UDP, ANY_UDP)]
    placed = insert_defaults(items, added)
    assert names_of(cast("list[dict[str, Any]]", placed)) == [
        INTERNET_UDP,
        INTERNET_OTHER,
        ANY_UDP,
        ANY_OTHER,
    ]
    assert placed[1] is items[0]
    assert placed[3] is items[1]
    assert len(items) == 2  # the list given is unchanged


def test_insert_defaults_into_no_list_gives_the_entries() -> None:
    entries = [copy.deepcopy(TEMPLATE[name]) for name in DEFAULT_NAMES]
    assert insert_defaults(None, entries) == entries


def test_repaired_entry_is_the_template_entry_with_an_id_and_the_flag() -> None:
    entry = repaired_entry(ANY_UDP, "abc", conditional_backhaul=None)
    assert entry == {**TEMPLATE[ANY_UDP], "ruleLogicalId": "abc"}
    assert "allowConditionalBh" not in entry["action"]
    flagged = repaired_entry(ANY_UDP, "abc", conditional_backhaul=False)
    assert flagged["action"]["allowConditionalBh"] is False
    assert TEMPLATE[ANY_UDP]["action"] == entry["action"]  # the template is not changed


# --- the repair ---------------------------------------------------------------


def check_repaired(plan: Any, names: list[str], *, written_as: list[str]) -> None:
    assert names_of(guest_defaults_of(plan.data)) == written_as
    (warning,) = plan.warnings
    assert warning.code == "defaults_repaired"
    assert warning.path == GUEST_DEFAULTS
    assert "Guest" in warning.message
    assert all(name in warning.message for name in names)
    assert plan.missing_defaults == (
        MissingDefaults("Guest", GUEST_ID, tuple(names), repaired=True),
    )
    assert f"  repaired defaults: Guest: {', '.join(names)}" in plan.render()


def test_a_segment_with_no_defaults_is_repaired_with_the_four() -> None:
    module = qos_module(MISSING)
    edit_global(module)
    plan = plan_module(module)
    assert plan.operation == "update"
    check_repaired(plan, list(DEFAULT_NAMES), written_as=list(DEFAULT_NAMES))
    for rule in guest_defaults_of(plan.data):
        template = TEMPLATE[rule["name"]]
        assert rule["match"] == template["match"]
        assert rule["action"] == template["action"]
    ids = [rule["ruleLogicalId"] for rule in guest_defaults_of(plan.data)]
    assert len(set(ids)) == 4
    assert (
        plan.data["segments"][0]["defaults"]
        == raw_of(module)["segments"][0]["defaults"]
    )
    (change,) = plan.changes  # the edit; the repaired entries are no change
    assert str(change.path).endswith(".name")


def test_an_empty_list_is_repaired_with_the_four() -> None:
    module = qos_module([])
    edit_global(module)
    plan = plan_module(module)
    check_repaired(plan, list(DEFAULT_NAMES), written_as=list(DEFAULT_NAMES))


def test_three_of_four_get_the_one_missing_in_its_place() -> None:
    kept = present(INTERNET_UDP, INTERNET_OTHER, ANY_OTHER)
    module = qos_module(kept)
    edit_global(module)
    plan = plan_module(module)
    check_repaired(plan, [ANY_UDP], written_as=[*DEFAULT_NAMES])
    written = guest_defaults_of(plan.data)
    assert [written[0], written[1], written[3]] == kept  # raw equal, untouched
    new = written[2]
    assert new["match"] == TEMPLATE[ANY_UDP]["match"]
    assert new["ruleLogicalId"] not in {rule["ruleLogicalId"] for rule in kept}


def test_a_shared_match_pairs_one_to_one() -> None:
    kept = present(ANY_UDP)
    module = qos_module(kept)
    edit_global(module)
    plan = plan_module(module)
    check_repaired(
        plan,
        [INTERNET_UDP, INTERNET_OTHER, ANY_OTHER],
        written_as=[*DEFAULT_NAMES],
    )
    assert guest_defaults_of(plan.data)[2] == kept[0]


def test_four_matches_under_other_names_are_not_repaired() -> None:
    others = present(INTERNET_UDP, INTERNET_OTHER, ANY_UDP, ANY_OTHER)
    for rule, name in zip(others, ("a", "b", "c", "d"), strict=True):
        rule["name"] = name
    module = qos_module(others)
    edit_global(module)
    plan = plan_module(module)
    assert plan.warnings == ()
    assert plan.missing_defaults == ()
    assert guest_defaults_of(plan.data) == others


def test_an_unknown_item_leaves_the_list_alone() -> None:
    kept = present(INTERNET_UDP, INTERNET_OTHER, ANY_OTHER)
    extra = present(ANY_UDP)[0]
    extra["name"] = "Custom"
    extra["match"] = {**extra["match"], "proto": 6}
    kept.append(extra)
    module = qos_module(kept)
    edit_global(module)
    plan = plan_module(module)
    assert guest_defaults_of(plan.data) == kept
    (warning,) = plan.warnings
    assert (warning.code, warning.path) == ("defaults_not_template", GUEST_DEFAULTS)
    assert "Guest" in warning.message
    assert "Custom" in warning.message
    assert plan.missing_defaults == ()


def test_a_repair_that_would_repeat_a_name_leaves_the_list_alone() -> None:
    # Named like Any-Other, but it matches the UDP entries: it pairs with
    # Internet-UDP, and adding Any-Other would repeat its name.
    odd = present(ANY_OTHER)[0]
    odd["match"] = copy.deepcopy(TEMPLATE[ANY_UDP]["match"])
    module = qos_module([odd])
    edit_global(module)
    plan = plan_module(module)
    assert guest_defaults_of(plan.data) == [odd]
    (warning,) = plan.warnings
    assert (warning.code, warning.path) == ("defaults_not_template", GUEST_DEFAULTS)
    assert ANY_OTHER in warning.message
    assert plan.missing_defaults == ()


def test_a_module_with_nothing_else_to_write_is_not_written() -> None:
    module = qos_module(MISSING)
    plan = plan_module(module)
    assert plan.operation == "none"
    assert plan.data == module.raw_data
    (warning,) = plan.warnings
    assert (warning.code, warning.path) == ("defaults_repaired", GUEST_DEFAULTS)
    assert "later write" in warning.message
    assert plan.missing_defaults == (
        MissingDefaults("Guest", GUEST_ID, DEFAULT_NAMES, repaired=False),
    )
    lines = plan.render().splitlines()
    assert f"  missing defaults: Guest: {', '.join(DEFAULT_NAMES)}" in lines
    assert not any(line.startswith("  repaired defaults") for line in lines)


def test_the_repaired_ids_are_the_same_on_a_re_plan_and_on_a_copy() -> None:
    module = qos_module(MISSING)
    edit_global(module)
    first = plan_module(module)
    assert plan_module(module).data == first.data
    assert plan_module(copy.deepcopy(module)).data == first.data


def test_with_data_parsed_from_does_not_share_the_repaired_ids() -> None:
    module = qos_module(MISSING)
    edit_global(module)
    first = plan_module(module)
    other = module.with_data_parsed_from(copy.deepcopy(module.raw_data))
    assert module.defaults_rule_id(GUEST_ID, ANY_UDP) == module.defaults_rule_id(
        GUEST_ID, ANY_UDP
    )
    assert other.defaults_rule_id(GUEST_ID, ANY_UDP) != module.defaults_rule_id(
        GUEST_ID, ANY_UDP
    )
    assert first.missing_defaults  # the plan made before the copy is unchanged


def test_allow_conditional_bh_follows_the_mapping() -> None:
    def repaired(mapping: dict[int, bool] | None) -> list[dict[str, Any]]:
        module = qos_module(MISSING)
        edit_global(module)
        return guest_defaults_of(plan_module(module, conditional_backhaul=mapping).data)

    for rule in repaired({GUEST_ID: True}):
        assert rule["action"]["allowConditionalBh"] is True
    for rule in repaired({GUEST_ID: False}):
        assert rule["action"]["allowConditionalBh"] is False
    for rule in repaired({0: True}):  # no value for this segment
        assert "allowConditionalBh" not in rule["action"]
    for rule in repaired(None):
        assert "allowConditionalBh" not in rule["action"]


def test_repair_defaults_false_turns_the_repair_off_and_keeps_the_warning() -> None:
    module = qos_module(MISSING)
    edit_global(module)
    plan = plan_module(module, repair_defaults=False)
    assert plan.operation == "update"
    assert "defaults" not in plan.data["segments"][1]
    (warning,) = plan.warnings
    assert warning.code == "defaults_repaired"
    assert "repair_defaults" in warning.message
    assert plan.missing_defaults == (
        MissingDefaults("Guest", GUEST_ID, DEFAULT_NAMES, repaired=False),
    )
    assert "  missing defaults: Guest" in plan.render()


def test_carried_repairs_are_used_as_given() -> None:
    module = qos_module(MISSING)
    edit_global(module)
    carried = {
        (str(GUEST_ID), name): {**TEMPLATE[name], "ruleLogicalId": f"carried-{name}"}
        for name in DEFAULT_NAMES
    }
    plan = plan_module(module, carried_repairs=carried)
    assert guest_defaults_of(plan.data) == [
        carried[(str(GUEST_ID), n)] for n in DEFAULT_NAMES
    ]


def test_the_edge_level_repairs_nothing() -> None:
    entry = qos_entry(GUEST, defaults=MISSING, rules=rules_of_template())
    del entry["cosMapping"]  # the template's is stored with its flag off
    row = make_module_json(
        "QOS",
        {"segments": [entry]},
        module_id=QOS_ID,
        configuration_id=200,
    )
    edge = QosConfigurationModule[Edge].model_validate(row)
    data = copy.deepcopy(raw_of(edge))
    data["segments"][0]["rules"][0]["name"] = "Edited"
    plan = plan_module(edge, data, level=ConfigContext.EDGE)
    assert plan.operation == "update"
    assert "defaults" not in plan.data["segments"][0]
    assert plan.warnings == ()
    assert plan.missing_defaults == ()


async def test_update_module_repairs_by_default_and_not_with_repair_defaults_false(
    mock_vco_client: MockVcoClientFactory,
) -> None:
    for repair in (True, False):
        module = qos_module(MISSING)
        store = store_of(module)
        client = await mock_vco_client(store)
        data = module.mutable_data()
        data.segments[0].rules[0].name = "Edited"
        _ = await client.update_module(module, data, repair_defaults=repair)
        (sent,) = [
            p for m, p in store.requests if m.endswith("updateConfigurationModule")
        ]
        guest = sent["_update"]["data"]["segments"][1]
        if repair:
            assert names_of(guest["defaults"]) == list(DEFAULT_NAMES)
        else:
            assert "defaults" not in guest


def test_added_defaults_are_placed_in_template_order_in_the_data_and_the_baseline() -> (
    None
):
    module = qos_module(present(INTERNET_OTHER, ANY_OTHER))
    added = [copy.deepcopy(TEMPLATE[name]) for name in (INTERNET_UDP, ANY_UDP)]
    for rule in added:
        rule["ruleLogicalId"] = str(uuid.uuid4())
    module.add_default_entries(GUEST_ID, added)
    expected = [INTERNET_UDP, INTERNET_OTHER, ANY_UDP, ANY_OTHER]
    parsed = cast("list[QosRule]", module.data.segments[1].defaults)
    assert [rule.name for rule in parsed] == expected
    plan = plan_module(module, via_pipeline=True)
    assert plan.changes == ()
    assert names_of(guest_defaults_of(plan.data)) == expected
