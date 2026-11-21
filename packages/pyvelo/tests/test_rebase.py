"""rebase(raw, before, after): apply the change before->after to raw."""

from collections.abc import Sequence
from typing import Annotated, cast

from pydantic import MISSING, Field
from pyvelo.core import VcoModel
from pyvelo.core.fields import KeyField
from pyvelo.core.rebase import RawChange, raw_changes, rebase


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
