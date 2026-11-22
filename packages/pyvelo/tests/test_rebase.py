"""rebase(raw, before, after): apply the change before->after to raw."""

import copy
from collections.abc import Sequence
from typing import Annotated, cast

from pydantic import MISSING, Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField
from pyvelo.core.rebase import RawChange, raw_changes, rebase

from .patch_models import Route


class Meta(VcoModel):
    segment_id: int = 0
    name: str


class Rule(VcoModel):
    name: str
    port: int = 0


class Seg(VcoModel):
    segment: Meta
    rules: Annotated[list[Rule], KeyField("name")] = Field(default_factory=list)


class Root(VcoModel):
    segments: Annotated[list[Seg], KeyField("segment.name")] = Field(
        default_factory=list
    )
    logging_enabled: bool = (
        False  # dumps "loggingEnabled", also reads "logging_enabled"
    )
    tags: list[str] = Field(default_factory=list)


def seg(
    name: str, sid: int, rules: Sequence[object], **extra: object
) -> dict[str, object]:
    return {"segment": {"segmentId": sid, "name": name}, "rules": rules, **extra}


def first_rules(payload: object) -> object:
    """The rules of the first segment of a rebased payload."""
    segments = cast(
        list[dict[str, object]], cast(dict[str, object], payload)["segments"]
    )
    return segments[0]["rules"]


# --- untyped -----------------------------------------------------------------


def test_no_edit_returns_raw_even_when_parse_was_lossy():
    raw = {"n": "5", "unknown": [1, 2.5, None]}
    before = {"n": 5, "defaulted": True}
    assert rebase(raw, before, before) == raw


def test_leaf_change_keeps_unknown_keys_and_lossy_neighbours():
    raw = {"n": "5", "m": 1, "unknown": {"x": 1}}
    assert rebase(raw, {"n": 5, "m": 1}, {"n": 5, "m": 2}) == {
        "n": "5",
        "m": 2,
        "unknown": {"x": 1},
    }


def test_key_added_and_key_removed():
    assert rebase({"a": 1}, {"a": 1}, {"a": 1, "b": 2}) == {"a": 1, "b": 2}
    assert rebase({"a": 1, "u": 0}, {"a": 1}, {}) == {"u": 0}


def test_default_materialised_by_dump_is_not_written():
    assert rebase({}, {"a": 1}, {"a": 1, "b": 2}) == {"b": 2}


def test_change_inside_container_absent_in_raw_takes_whole_container():
    before = {"s": {"x": 1, "y": 2}}
    after = {"s": {"x": 9, "y": 2}}
    assert rebase({}, before, after) == {"s": {"x": 9, "y": 2}}


def test_unkeyed_list_replaced_whole_when_changed():
    raw = {"tags": ["a", "b"], "u": 1}
    assert rebase(raw, {"tags": ["a", "b"]}, {"tags": ["b"]}) == {"tags": ["b"], "u": 1}


def test_shape_mismatch_takes_after():
    assert rebase({"s": None}, {"s": {}}, {"s": {"x": 1}}) == {"s": {"x": 1}}


# --- typed -------------------------------------------------------------------


def test_keyed_list_order_from_after_and_unknown_keys_kept():
    raw = {"segments": [seg("A", 0, [], future=1), seg("B", 1, [], future=2)]}
    before = {"segments": [seg("A", 0, []), seg("B", 1, [])]}
    after = {
        "segments": [
            seg("B", 1, [{"name": "r1", "port": 22}]),
            seg("C", 2, []),
        ]
    }
    assert rebase(raw, before, after, Root) == {
        "segments": [
            seg("B", 1, [{"name": "r1", "port": 22}], future=2),
            seg("C", 2, []),
        ]
    }


def test_change_is_written_to_the_key_as_read():
    raw = {"logging_enabled": False}
    out = rebase(raw, {"loggingEnabled": False}, {"loggingEnabled": True}, Root)
    assert out == {"logging_enabled": True}


def test_duplicate_keys_fall_back_to_whole_list():
    dup = [{"name": "r", "port": 1, "u": 1}, {"name": "r", "port": 2, "u": 2}]
    raw = {"segments": [seg("A", 0, dup)]}
    before = {
        "segments": [seg("A", 0, [{"name": "r", "port": 1}, {"name": "r", "port": 2}])]
    }
    assert rebase(raw, before, before, Root) == raw
    after = {
        "segments": [seg("A", 0, [{"name": "r", "port": 1}, {"name": "r", "port": 9}])]
    }
    assert first_rules(rebase(raw, before, after, Root)) == [
        {"name": "r", "port": 1},
        {"name": "r", "port": 9},
    ]


def test_length_mismatch_between_raw_and_baseline():
    raw = {"segments": [seg("A", 0, [{"name": "r1"}, "garbage"])]}
    before = {"segments": [seg("A", 0, [{"name": "r1", "port": 0}])]}
    assert rebase(raw, before, before, Root) == raw
    after = {"segments": [seg("A", 0, [{"name": "r1", "port": 5}])]}
    assert first_rules(rebase(raw, before, after, Root)) == [{"name": "r1", "port": 5}]


# --- raw_changes -------------------------------------------------------------


def test_raw_changes_paths():
    old = {
        "a": 1,
        "segments": [seg("A", 0, [{"name": "r1", "port": 1}])],
        "tags": ["x"],
    }
    new = {
        "b": 2,
        "segments": [seg("A", 0, [{"name": "r1", "port": 2}])],
        "tags": ["y"],
    }
    assert raw_changes(old, new, Root) == [
        RawChange("a", 1, MISSING),
        RawChange("segments[A].rules[r1].port", 1, 2),
        RawChange("tags", ["x"], ["y"]),
        RawChange("b", MISSING, 2),
    ]
    assert raw_changes(old, old, Root) == []


# --- behaviour the cases above do not pin ----------------------------------


class Pol(VcoModel):
    rule_name: str
    hits: int = 0


class Pols(VcoModel):
    pols: Annotated[list[Pol], KeyField("rule_name")] = Field(default_factory=list)


def test_items_without_a_key_fall_back_to_whole_list():
    raw = {"segments": [seg("A", 0, [{"port": 1, "u": 1}])]}
    before = {"segments": [seg("A", 0, [{"port": 1}])]}
    after = {"segments": [seg("A", 0, [{"port": 2}])]}
    assert rebase(raw, before, before, Root) == raw
    assert first_rules(rebase(raw, before, after, Root)) == [{"port": 2}]


def test_raw_changes_reads_keys_under_any_name_the_model_reads():
    old = {"pols": [{"rule_name": "a", "hits": 1}]}  # model dumps "ruleName"
    new = {"pols": [{"rule_name": "a", "hits": 2}]}
    assert raw_changes(old, new, Pols) == [RawChange("pols[a].hits", 1, 2)]


def test_raw_changes_items_on_one_side_only_are_whole_changes():
    old = {"pols": [{"rule_name": "a"}, {"rule_name": "b"}]}
    new = {"pols": [{"rule_name": "b"}, {"rule_name": "c"}]}
    assert raw_changes(old, new, Pols) == [
        RawChange("pols[a]", {"rule_name": "a"}, MISSING),
        RawChange("pols[c]", MISSING, {"rule_name": "c"}),
    ]


def test_raw_changes_reorder_of_a_keyed_list_is_reported_on_the_list():
    old = {"pols": [{"rule_name": "a"}, {"rule_name": "b"}]}
    new = {"pols": [{"rule_name": "b"}, {"rule_name": "a"}]}
    assert raw_changes(old, new, Pols) == [RawChange("pols", old["pols"], new["pols"])]


def test_raw_changes_untyped_lists_are_whole_changes():
    assert raw_changes({"l": [{"name": "a"}]}, {"l": [{"name": "b"}]}) == [
        RawChange("l", [{"name": "a"}], [{"name": "b"}])
    ]


class Vlan(VcoModel):
    vlan_id: int
    label: str = ""


class Vlans(VcoModel):
    vlans: Annotated[list[Vlan], KeyField("vlan_id")] = Field(default_factory=list)


def test_reordered_raw_list_is_replaced_whole_not_edited_in_the_wrong_item():
    # Parsing put the items in a different order than raw has them, so pairing
    # raw to before by position would give b's raw item a's edit.
    raw = {
        "pols": [
            {"ruleName": "b", "x": "B"},
            {"ruleName": "a", "x": "A"},
        ]
    }
    before = {"pols": [{"ruleName": "a", "hits": 1}, {"ruleName": "b", "hits": 2}]}
    after = {"pols": [{"ruleName": "a", "hits": 9}, {"ruleName": "b", "hits": 2}]}
    assert rebase(raw, before, before, Pols) == raw
    assert rebase(raw, before, after, Pols) == after


def test_coerced_key_still_pairs_and_keeps_unknown_keys():
    raw = {"vlans": [{"vlanId": "0", "label": "x", "future": 1}]}
    before = {"vlans": [{"vlanId": 0, "label": "x"}]}
    after = {"vlans": [{"vlanId": 0, "label": "y"}]}
    assert rebase(raw, before, after, Vlans) == {
        "vlans": [{"vlanId": "0", "label": "y", "future": 1}]
    }


def test_unreadable_raw_key_keeps_positional_pairing():
    raw = {"pols": [{"hits": 1, "x": 1}]}  # no key in raw; the parse supplied one
    before = {"pols": [{"ruleName": "a", "hits": 1}]}
    after = {"pols": [{"ruleName": "a", "hits": 2}]}
    assert rebase(raw, before, after, Pols) == {"pols": [{"hits": 2, "x": 1}]}


# --- items paired by alt key, then by key --------------------------------------


class R(VcoModel):
    name: str | None = None
    rid: str | MISSING = MISSING
    port: int = 0


class Rs(VcoModel):
    rules: Annotated[list[R], KeyField("name", alt="rid")] = Field(default_factory=list)


def item(name: str | None, rid: str | None = None, **rest: object) -> dict[str, object]:
    """A rule as JSON; with no *rid* it has no ``rid`` key."""
    return {"name": name, **({} if rid is None else {"rid": rid}), **rest}


def test_rename_keeps_unknown_raw_keys():
    raw = {"rules": [item("a", "1", x=1)]}
    before = {"rules": [item("a", "1", port=0)]}
    after = {"rules": [item("b", "1", port=0)]}
    assert rebase(raw, before, after, Rs) == {"rules": [item("b", "1", x=1)]}


def test_repeated_names_with_distinct_alt_keys_rebase_item_by_item():
    raw = {"rules": [item("a", "1", x=1), item("a", "2", x=2)]}
    before = {"rules": [item("a", "1", port=0), item("a", "2", port=0)]}
    after = {"rules": [item("a", "1", port=0), item("a", "2", port=5)]}
    assert rebase(raw, before, after, Rs) == {
        "rules": [item("a", "1", x=1), item("a", "2", x=2, port=5)]
    }


def test_new_item_without_alt_key_is_an_addition():
    raw = {"rules": [item("a", "1", x=1), item("b", "2", x=2)]}
    before = {"rules": [item("a", "1", port=0), item("b", "2", port=0)]}
    new = item("new", port=0)
    after = {"rules": [*before["rules"], new]}
    out = rebase(raw, before, after, Rs)
    assert out == {"rules": [*raw["rules"], new]}
    assert raw_changes(raw, out, Rs) == [RawChange("rules[new]", MISSING, new)]


def test_new_item_without_a_key_is_an_addition():
    raw = {"rules": [item("a", "1", x=1), item("b", "2", x=2)]}
    before = {"rules": [item("a", "1", port=0), item("b", "2", port=0)]}
    new = item(None, port=3)
    after = {"rules": [*before["rules"], new]}
    out = rebase(raw, before, after, Rs)
    assert out == {"rules": [*raw["rules"], new]}
    assert raw_changes(raw, out, Rs) == [RawChange("rules[#2]", MISSING, new)]


def test_repeated_key_without_alt_keys_replaces_the_list():
    raw = {"rules": [item("a", x=1), item("a", x=2)]}
    before = {"rules": [item("a", port=0), item("a", port=0)]}
    after = {"rules": [item("a", port=0), item("a", port=7)]}
    assert rebase(raw, before, after, Rs) == after


def test_repeated_key_without_alt_keys_untouched_when_nothing_changed():
    raw = {"rules": [item("a", x=1), item("a", x=2)]}
    before = {"rules": [item("a", port=0), item("a", port=0)]}
    assert rebase(raw, before, copy.deepcopy(before), Rs) == raw
    # A change beside the list: the document is rebased, the list kept as read.
    raw = {**raw, "other": 1}
    before = {**before, "other": 1}
    after = {**copy.deepcopy(before), "other": 2}
    out = cast(dict[str, object], rebase(raw, before, after, Rs))
    assert out == {**raw, "other": 2}
    assert out["rules"] is raw["rules"]


def test_removed_item_without_a_key_replaces_the_list():
    raw = {"rules": [item("a", x=1), item(None, x=2)]}
    before = {"rules": [item("a", port=0), item(None, port=0)]}
    after = {"rules": [item("a", port=0)]}
    assert rebase(raw, before, after, Rs) == after


def test_raw_key_the_baseline_lacks_replaces_the_list():
    # The raw item has a key ("None", a string) where the parse has none, so
    # raw and before cannot be trusted to line up by position.
    raw = {"rules": [item("None", "1", x=1)]}
    before = {"rules": [item(None, "1", port=0)]}
    after = {"rules": [item(None, "1", port=5)]}
    assert rebase(raw, before, after, Rs) == after


def test_raw_key_in_another_case_than_its_parse_still_rebases_item_by_item():
    # The parse coerced the raw key (an upper-case UUID read as a lower-case one).
    raw = {"rules": [item("RULE-A", x=1), item("RULE-B", x=2)]}
    before = {"rules": [item("rule-a", port=0), item("rule-b", port=0)]}
    after = {"rules": [item("rule-a", port=5), item("rule-b", port=0)]}
    assert rebase(raw, before, after, Rs) == {
        "rules": [item("RULE-A", x=1, port=5), item("RULE-B", x=2)]
    }


def test_raw_alt_key_in_another_case_is_trusted():
    raw = {"rules": [item("a", "ABC", x=1), item("b", "DEF", x=2)]}
    before = {"rules": [item("a", "abc", port=0), item("b", "def", port=0)]}
    after = {"rules": [item("c", "abc", port=0), item("b", "def", port=0)]}
    # The rename pairs by the alt key: one item, its unknown raw key kept.
    assert rebase(raw, before, after, Rs) == {
        "rules": [item("c", "ABC", x=1), item("b", "DEF", x=2)]
    }


def test_removed_item_with_a_unique_key_is_removed():
    raw = {"rules": [item("a", x=1), item("b", x=2), item("c", x=3)]}
    before = {"rules": [item("a", port=0), item("b", port=0), item("c", port=0)]}
    after = {"rules": [item("a", port=0), item("c", port=0)]}
    assert rebase(raw, before, after, Rs) == {"rules": [item("a", x=1), item("c", x=3)]}


def test_removed_item_with_a_repeated_key_and_a_unique_alt_key_is_removed():
    raw = {"rules": [item("a", "1", x=1), item("a", "2", x=2), item("b", "3", x=3)]}
    before = {
        "rules": [
            item("a", "1", port=0),
            item("a", "2", port=0),
            item("b", "3", port=0),
        ]
    }
    after = {"rules": [item("a", "1", port=0), item("b", "3", port=0)]}
    out = rebase(raw, before, after, Rs)
    assert out == {"rules": [item("a", "1", x=1), item("b", "3", x=3)]}
    assert raw_changes(raw, out, Rs) == [
        RawChange("rules[#1]", item("a", "2", x=2), MISSING)
    ]


def test_both_items_of_a_repeated_key_removed_by_their_alt_keys():
    raw = {"rules": [item("a", "1", x=1), item("a", "2", x=2), item("b", "3", x=3)]}
    before = {
        "rules": [
            item("a", "1", port=0),
            item("a", "2", port=0),
            item("b", "3", port=0),
        ]
    }
    after = {"rules": [item("b", "3", port=7)]}
    out = rebase(raw, before, after, Rs)
    assert out == {"rules": [item("b", "3", x=3, port=7)]}
    assert raw_changes(raw, out, Rs) == [
        RawChange("rules[#0]", item("a", "1", x=1), MISSING),
        RawChange("rules[#1]", item("a", "2", x=2), MISSING),
        RawChange("rules[b].port", MISSING, 7),
    ]


def test_removed_item_without_a_key_is_removed_by_its_alt_key():
    raw = {"rules": [item("a", "1", x=1), item(None, "2", x=2)]}
    before = {"rules": [item("a", "1", port=0), item(None, "2", port=0)]}
    after = {"rules": [item("a", "1", port=0)]}
    assert rebase(raw, before, after, Rs) == {"rules": [item("a", "1", x=1)]}


def test_removed_item_with_a_repeated_key_and_a_repeated_alt_key_replaces_the_list():
    raw = {"rules": [item("a", "1", x=1), item("a", "1", x=2), item("b", "3", x=3)]}
    before = {
        "rules": [
            item("a", "1", port=0),
            item("a", "1", port=0),
            item("b", "3", port=0),
        ]
    }
    after = {"rules": [item("a", "1", port=0), item("b", "3", port=0)]}
    assert rebase(raw, before, after, Rs) == after


def test_removed_item_whose_alt_key_the_parse_made_up_replaces_the_list():
    # The raw items have no "rid"; the dumps carry one the parse generated.
    raw = {"rules": [item("a", x=1), item("a", x=2), item("b", x=3)]}
    before = {
        "rules": [
            item("a", "g1", port=0),
            item("a", "g2", port=0),
            item("b", "g3", port=0),
        ]
    }
    after = {"rules": [item("a", "g1", port=0), item("b", "g3", port=0)]}
    assert rebase(raw, before, after, Rs) == after


# --- composite keys ------------------------------------------------------------


class Routes(VcoModel):
    routes: Annotated[list[Route], KeyField("dest", "prefix", "gw", "intf")] = Field(
        default_factory=list
    )


def route(dest: str, prefix: object, **rest: object) -> dict[str, object]:
    """A route as JSON, through gateway ``192.168.1.1``."""
    return {"dest": dest, "prefix": prefix, "gw": "192.168.1.1", **rest}


def test_composite_keyed_list_keeps_unknown_raw_keys_on_untouched_items():
    raw = {
        "routes": [
            route("10.0.0.0", 24, intf="GE3", x=1),
            route("10.1.0.0", 16, intf="GE3", x=2),
        ]
    }
    before = {
        "routes": [
            route("10.0.0.0", 24, intf="GE3", cost=0),
            route("10.1.0.0", 16, intf="GE3", cost=0),
        ]
    }
    after = {
        "routes": [
            route("10.0.0.0", 24, intf="GE3", cost=0),
            route("10.1.0.0", 16, intf="GE3", cost=5),
        ]
    }
    out = cast(dict[str, list[object]], rebase(raw, before, after, Routes))
    assert out == {
        "routes": [
            route("10.0.0.0", 24, intf="GE3", x=1),
            route("10.1.0.0", 16, intf="GE3", x=2, cost=5),
        ]
    }
    assert out["routes"][0] is raw["routes"][0]
    assert raw_changes(raw, out, Routes) == [
        RawChange('routes["10.1.0.0",16,"192.168.1.1",GE3].cost', MISSING, 5)
    ]


def test_raw_item_without_every_key_part_pairs_by_position():
    # The raw items have no "intf"; the dumps carry the model default "".
    raw = {"routes": [route("10.0.0.0", 24, x=1), route("10.1.0.0", 16, x=2)]}
    before = {
        "routes": [
            route("10.0.0.0", 24, intf="", cost=0),
            route("10.1.0.0", 16, intf="", cost=0),
        ]
    }
    after = {
        "routes": [
            route("10.0.0.0", 24, intf="", cost=0),
            route("10.1.0.0", 16, intf="", cost=5),
        ]
    }
    assert rebase(raw, before, after, Routes) == {
        "routes": [route("10.0.0.0", 24, x=1), route("10.1.0.0", 16, x=2, cost=5)]
    }


def test_key_parts_pair_in_string_form():
    # One side as read ("24"), the other as dumped (24): the same item.
    old = {"routes": [route("10.0.0.0", "24", intf="GE3", cost=0)]}
    new = {"routes": [route("10.0.0.0", 24, intf="GE3", cost=5)]}
    assert raw_changes(old, new, Routes) == [
        RawChange('routes["10.0.0.0","24","192.168.1.1",GE3].prefix', "24", 24),
        RawChange('routes["10.0.0.0","24","192.168.1.1",GE3].cost', 0, 5),
    ]
