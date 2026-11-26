"""The response check: which planned changes the VCO stored, and what else differs."""

from collections.abc import Iterable, Sequence
from typing import Annotated

import pytest
from pydantic import MISSING, Field
from pyvelo.core import KeyField, Positional, VcoModel
from pyvelo.core.rebase import ItemMove, ItemPlace, Key
from pyvelo.core.response_check import (
    PlannedChange,
    ResponseCheck,
    Rewrites,
    ServerChange,
    check_response,
)

from .patch_models import Root

H0 = {"ip": "10.0.0.0"}
H1 = {"ip": "10.0.0.1"}
L1 = {"internalId": "u1", "family": "IPv4", "name": "x"}


def chk(r: object, p: object, d: object) -> ResponseCheck:
    return check_response(r, p, d, Root)


def rule(name: str, *, i: str | None = None, p: int | None = None) -> dict[str, object]:
    """A rule as JSON: ``rule("a", i="i1", p=2)`` is ``a/i1/p2``."""
    out: dict[str, object] = {"name": name}
    if i is not None:
        out["ruleId"] = i
    if p is not None:
        out["priority"] = p
    return out


def rules(*items: dict[str, object]) -> dict[str, object]:
    return {"rules": list(items)}


def paths(xs: Iterable[PlannedChange | ServerChange]) -> list[str]:
    return [str(x.path) for x in xs]


def assert_paths(
    check: ResponseCheck,
    *,
    applied: Sequence[str] = (),
    not_applied: Sequence[str] = (),
    server_changes: Sequence[str] = (),
) -> None:
    """The paths of all four tuples; the order within one tuple is not asserted."""
    assert sorted(paths(check.applied)) == sorted(applied)
    assert sorted(paths(check.not_applied)) == sorted(not_applied)
    assert check.left_out == ()
    assert sorted(paths(check.server_changes)) == sorted(server_changes)


def not_applied_at(check: ResponseCheck, path: str) -> PlannedChange:
    (change,) = [c for c in check.not_applied if c.path == path]
    return change


def server_change_at(check: ResponseCheck, path: str) -> ServerChange:
    (change,) = [c for c in check.server_changes if c.path == path]
    return change


A, B, C = rule("a"), rule("b"), rule("c")
X, Y, Z = rule("x"), rule("y"), rule("z")


def test_nothing_planned() -> None:
    check = chk({"count": 1}, {"count": 1}, {"count": 1})
    assert_paths(check)


def test_scalar_applied() -> None:
    check = chk({"count": 1}, {"count": 2}, {"count": 2})
    assert_paths(check, applied=["count"])
    assert check.applied == (PlannedChange(check.applied[0].path, 1, 2, 2),)


def test_scalar_refused() -> None:
    check = chk({"count": 1}, {"count": 2}, {"count": 1})
    assert_paths(check, not_applied=["count"])
    change = not_applied_at(check, "count")
    assert change.stored == 1
    assert change.clash is False


def test_scalar_moved() -> None:
    check = chk({"count": 1}, {"count": 2}, {"count": 3})
    assert_paths(check, not_applied=["count"])
    change = not_applied_at(check, "count")
    assert change.stored == 3
    assert change.clash is True


def test_object_added_may_hold_more() -> None:
    check = chk({}, {"maybe": {"v": 1}}, {"maybe": {"v": 1, "note": "x"}})
    assert_paths(check, applied=["maybe"], server_changes=["maybe.note"])
    change = server_change_at(check, "maybe.note")
    assert change.as_read is MISSING
    assert change.stored == "x"


def test_object_added_lacks_a_leaf() -> None:
    check = chk({}, {"maybe": {"v": 1, "note": "n"}}, {"maybe": {"v": 1}})
    assert_paths(check, not_applied=["maybe"])
    change = not_applied_at(check, "maybe")
    assert change.stored == {"v": 1}
    assert change.clash is True


def test_leaf_in_an_object_is_its_own_change() -> None:
    check = chk(
        {"sub": {"v": 1, "note": "n"}},
        {"sub": {"v": 2, "note": "n"}},
        {"sub": {"v": 2, "note": "z"}},
    )
    assert_paths(check, applied=["sub.v"], server_changes=["sub.note"])
    change = server_change_at(check, "sub.note")
    assert change.as_read == "n"
    assert change.stored == "z"


def test_key_removed() -> None:
    check = chk({"title": "t"}, {}, {})
    assert_paths(check, applied=["title"])
    (change,) = check.applied
    assert change.as_read == "t"
    assert change.intended is MISSING
    assert change.stored is MISSING


def test_key_removed_is_still_there() -> None:
    check = chk({"title": "t"}, {}, {"title": "t"})
    assert_paths(check, not_applied=["title"])
    change = not_applied_at(check, "title")
    assert change.stored == "t"
    assert change.clash is False


def test_unplanned_difference_is_a_server_change() -> None:
    check = chk(
        {"count": 1, "flag": False},
        {"count": 2, "flag": False},
        {"count": 2, "flag": True},
    )
    assert_paths(check, applied=["count"], server_changes=["flag"])
    change = server_change_at(check, "flag")
    assert change.as_read is False
    assert change.stored is True


def test_stored_object_is_gone() -> None:
    check = chk({"sub": {"v": 1}}, {"sub": {"v": 2}}, {})
    assert_paths(check, not_applied=["sub.v"], server_changes=["sub"])
    planned = not_applied_at(check, "sub.v")
    assert planned.as_read == 1
    assert planned.intended == 2
    assert planned.stored is MISSING
    assert planned.clash is True
    change = server_change_at(check, "sub")
    assert change.as_read == {"v": 1}
    assert change.stored is MISSING


def test_unkeyed_list_replaced_whole() -> None:
    check = chk({"hops": [H0]}, {"hops": [H1, H0]}, {"hops": [H1, H0]})
    assert_paths(check, applied=["hops"])


def test_unkeyed_list_in_another_order() -> None:
    check = chk({"hops": [H0]}, {"hops": [H1, H0]}, {"hops": [H0, H1]})
    assert_paths(check, not_applied=["hops"])


def test_list_items_may_hold_more() -> None:
    check = chk(
        {"hops": []}, {"hops": [{"ip": "1"}]}, {"hops": [{"ip": "1", "cost": 0}]}
    )
    assert_paths(check, applied=["hops"], server_changes=["hops[#0].cost"])


def test_keyed_item_changed() -> None:
    check = chk(
        rules(rule("a", p=1), B), rules(rule("a", p=2), B), rules(rule("a", p=2), B)
    )
    assert_paths(check, applied=["rules[a].priority"])


def test_keyed_item_added_and_given_an_id() -> None:
    check = chk(rules(A), rules(A, B), rules(A, rule("b", i="i7")))
    assert_paths(check, applied=["rules[b]"], server_changes=["rules[b].ruleId"])


def test_keyed_item_added_is_missing() -> None:
    check = chk(rules(A), rules(A, B), rules(A))
    assert_paths(check, not_applied=["rules[b]"])
    change = not_applied_at(check, "rules[b]")
    assert change.stored is MISSING
    assert change.clash is False


def test_keyed_item_removed() -> None:
    check = chk(rules(A, B), rules(A), rules(A))
    assert_paths(check, applied=["rules[b]"])


def test_keyed_item_removed_is_still_there() -> None:
    check = chk(rules(A, B), rules(A), rules(A, B))
    assert_paths(check, not_applied=["rules[b]"])
    change = not_applied_at(check, "rules[b]")
    assert change.stored == B
    assert change.clash is False


def test_reorder_is_checked_by_keys() -> None:
    check = chk(rules(A, B, C), rules(C, A, B), rules(C, A, B))
    assert_paths(check, applied=["rules"])
    (order,) = check.applied
    assert order.is_order
    assert order.moves == ()


def test_reorder_refused() -> None:
    check = chk(rules(A, B, C), rules(C, A, B), rules(A, B, C))
    assert_paths(check, not_applied=["rules"])
    change = not_applied_at(check, "rules")
    assert change.as_read == ["a", "b", "c"]
    assert change.intended == ["c", "a", "b"]
    assert change.stored == ["a", "b", "c"]
    assert change.moves == (ItemMove(Key("c"), ItemPlace()),)
    assert change.clash is False


def test_reorder_refused_where_the_stored_order_changed_too_is_a_clash() -> None:
    check = chk(rules(A, B, C), rules(C, A, B), rules(B, A, C))
    change = not_applied_at(check, "rules")
    assert change.stored == ["b", "a", "c"]
    assert change.clash is True
    assert change.moves is not None
    assert [str(move) for move in change.moves] == ["c first", "a after c"]


def test_added_item_carries_its_place() -> None:
    first = not_applied_at(chk(rules(A), rules(B, A), rules(A)), "rules[b]")
    last = not_applied_at(chk(rules(A), rules(A, B), rules(A)), "rules[b]")
    assert (first.place, last.place) == (ItemPlace(), ItemPlace(Key("a")))
    assert first.is_order is False


def test_added_item_stored_in_another_place_is_a_lost_order() -> None:
    check = chk(rules(A), rules(B, A), rules(A, B))
    assert_paths(check, applied=["rules[b]"], not_applied=["rules"])
    order = not_applied_at(check, "rules")
    assert order.is_order
    assert (order.as_read, order.intended, order.stored) == (
        ["a"],
        ["b", "a"],
        ["a", "b"],
    )
    assert order.moves == (ItemMove(Key("b"), ItemPlace()),)
    assert order.clash is False


def test_added_item_stored_in_its_place_has_no_order_entry() -> None:
    check = chk(rules(A), rules(B, A), rules(B, A))
    assert_paths(check, applied=["rules[b]"])
    assert check.applied[0].place == ItemPlace()


def test_an_item_is_named_as_it_was_read() -> None:
    renamed = chk(
        rules(rule("a", i="i1")), rules(rule("z", i="i1")), rules(rule("a", i="i1"))
    )
    assert_paths(renamed, not_applied=["rules[a].name"])
    shifted = chk(
        rules(rule("d", i="i1"), rule("d", i="i2")),
        rules(rule("new"), rule("d", i="i1"), rule("d", i="i2", p=7)),
        rules(rule("d", i="i1"), rule("d", i="i2")),
    )
    assert_paths(shifted, not_applied=["rules[new]", "rules[#1].priority"])


def test_added_item_named_as_a_read_one_compares_the_list_whole() -> None:
    r, p = rules(rule("a", i="i1")), rules(rule("b", i="i1"), A)
    assert_paths(chk(r, p, p), applied=["rules"])
    refused = chk(r, p, r)
    assert_paths(refused, not_applied=["rules"])
    assert not_applied_at(refused, "rules").is_order is False


def test_a_list_the_vco_orders_itself_has_no_planned_order() -> None:
    own = Rewrites(own_order=(("rules",),))
    added = check_response(rules(A), rules(B, A), rules(A, B), Root, rewrites=own)
    assert (paths(added.applied), added.not_applied) == (["rules[b]"], ())
    (change,) = added.server_changes
    assert (str(change.path), change.as_read, change.stored) == (
        "rules",
        ["b", "a"],
        ["a", "b"],
    )
    moved = check_response(rules(A, B), rules(B, A), rules(A, B), Root, rewrites=own)
    assert (moved.applied, moved.not_applied) == ((), ())
    assert paths(moved.left_out) == ["rules"]
    assert moved.left_out[0].moves == (ItemMove(Key("b"), ItemPlace()),)
    assert paths(moved.server_changes) == ["rules"]


def test_reorder_and_edit_are_two_changes() -> None:
    check = chk(
        rules(A, rule("b", p=1)), rules(rule("b", p=2), A), rules(rule("b", p=2), A)
    )
    assert_paths(check, applied=["rules", "rules[b].priority"])


def test_item_another_writer_added() -> None:
    check = chk(rules(rule("a", p=1)), rules(rule("a", p=2)), rules(rule("a", p=2), Z))
    assert_paths(check, applied=["rules[a].priority"], server_changes=["rules[z]"])
    change = server_change_at(check, "rules[z]")
    assert change.as_read is MISSING
    assert change.stored == Z


def test_add_and_remove_refused_is_as_read() -> None:
    check = chk(rules(A, X), rules(A, Y), rules(A, X))
    assert_paths(check, not_applied=["rules[y]", "rules[x]"])
    added = not_applied_at(check, "rules[y]")
    assert added.stored is MISSING
    assert added.clash is False
    removed = not_applied_at(check, "rules[x]")
    assert removed.stored == X
    assert removed.clash is False


def test_another_writers_item_is_not_mine() -> None:
    check = chk(rules(A), rules(A, Y), rules(A, Z))
    assert_paths(check, not_applied=["rules[y]"], server_changes=["rules[z]"])
    change = not_applied_at(check, "rules[y]")
    assert change.stored is MISSING
    assert change.clash is False


def test_repeated_keys_pair_in_order() -> None:
    check = chk(
        rules(rule("d", i="i1"), rule("d", i="i2"), rule("d", i="i3")),
        rules(rule("d", i="i1"), rule("d", i="i2", p=2), rule("d", i="i3", p=2)),
        rules(rule("d", i="i1"), rule("d", i="i8", p=2), rule("d", i="i9", p=2)),
    )
    assert_paths(
        check,
        applied=["rules[#1].priority", "rules[#2].priority"],
        server_changes=["rules[#1].ruleId", "rules[#2].ruleId"],
    )


def test_keyless_added_item_pairs_with_what_is_left() -> None:
    check = chk(
        {"links": [L1]},
        {"links": [L1, {"family": "IPv4", "name": "new"}]},
        {"links": [L1, {"internalId": "u2", "family": "IPv4", "name": "new"}]},
    )
    assert_paths(check, applied=["links[#1]"], server_changes=["links[#1].internalId"])


def test_without_a_model_every_list_is_whole() -> None:
    check = check_response({"rules": [A]}, {"rules": [A, B]}, {"rules": [A, B]})
    assert_paths(check, applied=["rules"])


def test_unplanned_difference_in_a_keyed_list_is_told_by_item() -> None:
    check = chk(
        {"count": 1, "rules": [A]},
        {"count": 2, "rules": [A]},
        {"count": 2, "rules": [A, Z]},
    )
    assert_paths(check, applied=["count"], server_changes=["rules[z]"])
    change = server_change_at(check, "rules[z]")
    assert change.as_read is MISSING
    assert change.stored == Z


def test_order_the_plan_kept_and_the_vco_changed_is_a_server_change() -> None:
    check = chk(
        rules(rule("a", p=1), B), rules(rule("a", p=2), B), rules(B, rule("a", p=2))
    )
    assert_paths(check, applied=["rules[a].priority"], server_changes=["rules"])
    change = server_change_at(check, "rules")
    assert change.as_read == ["a", "b"]
    assert change.stored == ["b", "a"]


def test_stored_keyed_list_is_gone() -> None:
    check = chk(rules(rule("a", p=1)), rules(rule("a", p=2)), {})
    assert_paths(check, not_applied=["rules[a].priority"], server_changes=["rules"])
    planned = not_applied_at(check, "rules[a].priority")
    assert planned.stored is MISSING
    assert planned.clash is True
    change = server_change_at(check, "rules")
    assert change.as_read == [rule("a", p=1)]
    assert change.stored is MISSING


def test_removal_inside_a_container_the_vco_dropped_is_applied() -> None:
    check = chk({"sub": {"v": 1, "note": "n"}}, {"sub": {"v": 1}}, {})
    assert_paths(check, applied=["sub.note"], server_changes=["sub"])
    (removal,) = check.applied
    assert removal.as_read == "n"
    assert removal.intended is MISSING
    assert removal.stored is MISSING
    change = server_change_at(check, "sub")
    assert change.as_read == {"v": 1, "note": "n"}
    assert change.stored is MISSING


def test_removed_and_readded_under_one_name_is_applied() -> None:
    check = chk(
        rules(A, rule("x", i="i1")),
        rules(A, X, X),
        rules(A, rule("x", i="i8"), rule("x", i="i9")),
    )
    assert_paths(
        check,
        applied=["rules[x]", "rules[#1]", "rules[#2]"],
        server_changes=["rules[#1].ruleId", "rules[#2].ruleId"],
    )
    assert check.not_applied == ()
    assert server_change_at(check, "rules[#1].ruleId").stored == "i8"
    assert server_change_at(check, "rules[#2].ruleId").stored == "i9"


def test_removed_item_found_by_its_alt_key() -> None:
    check = chk(
        rules(A, rule("x", i="i1")),
        rules(A),
        rules(A, rule("x", i="i9"), rule("x", i="i1")),
    )
    assert_paths(check, not_applied=["rules[x]"], server_changes=["rules[#1]"])
    removal = not_applied_at(check, "rules[x]")
    assert removal.stored == rule("x", i="i1")
    assert removal.clash is False
    change = server_change_at(check, "rules[#1]")
    assert change.as_read is MISSING
    assert change.stored == rule("x", i="i9")


def test_change_beside_that_removal_is_not_applied() -> None:
    check = chk({"sub": {"v": 1, "note": "n"}}, {"sub": {"v": 2}}, {})
    assert_paths(
        check, applied=["sub.note"], not_applied=["sub.v"], server_changes=["sub"]
    )
    planned = not_applied_at(check, "sub.v")
    assert planned.intended == 2
    assert planned.stored is MISSING
    assert planned.clash is True


class Port(VcoModel):
    name: str
    speed: int = 0


class Box(VcoModel):
    ports: Annotated[list[Port], KeyField("name")] = Field(default_factory=list)


class Boxes(VcoModel):
    boxes: dict[str, Box] = Field(default_factory=dict)


def box(*ports: dict[str, object]) -> dict[str, object]:
    return {"ports": list(ports)}


def test_an_edit_below_a_map_entry_is_applied_and_a_sibling_key_is_a_server_change() -> (  # noqa: E501 (a test's name)
    None
):
    p1: dict[str, object] = {"name": "p1", "speed": 1}
    p2: dict[str, object] = {"name": "p2", "speed": 5}
    read = {"boxes": {"a": box(p1), "b": box(p2)}}
    sent = {"boxes": {"a": box({**p1, "speed": 2}), "b": box(p2)}}
    stored = {"boxes": {"a": box({**p1, "speed": 2}), "b": box({**p2, "note": "n"})}}
    check = check_response(read, sent, stored, Boxes)
    assert_paths(
        check,
        applied=["boxes.a.ports[p1].speed"],
        server_changes=["boxes.b.ports[p2].note"],
    )
    assert server_change_at(check, "boxes.b.ports[p2].note").stored == "n"


def test_an_edit_below_a_map_entry_the_stored_data_lacks_is_not_applied() -> None:
    p1: dict[str, object] = {"name": "p1", "speed": 1}
    p2: dict[str, object] = {"name": "p2", "speed": 5}
    read = {"boxes": {"a": box(p1), "b": box(p2)}}
    sent = {"boxes": {"a": box({**p1, "speed": 2}), "b": box(p2)}}
    check = check_response(read, sent, read, Boxes)
    assert_paths(check, not_applied=["boxes.a.ports[p1].speed"])
    assert not_applied_at(check, "boxes.a.ports[p1].speed").stored == 1


class Step(VcoModel):
    ip: str
    note: str | MISSING = MISSING


class Steps(VcoModel):
    steps: Annotated[list[Step], Positional()] = Field(default_factory=list)


WHOLE_LISTS = [
    pytest.param(
        Root,
        "hops",
        [{"ip": "10.0.0.1", "cost": 5, "note": "x"}],
        [{"ip": "10.0.0.1", "cost": 5}],
        id="model list without a key",
    ),
    pytest.param(
        Steps,
        "steps",
        [{"ip": "10.0.0.1", "note": "x"}],
        [{"ip": "10.0.0.1"}],
        id="positional model list",
    ),
    pytest.param(
        Root,
        "blobs",
        [{"a": 1, "nested": {"b": 2, "c": 3}}],
        [{"a": 1, "nested": {"b": 2}}],
        id="plain objects, a nested key",
    ),
    pytest.param(
        Root,
        "links",
        [{"name": "l", "mtu": 1400}],
        [{"name": "l"}],
        id="keyed list read with an item without its key",
    ),
    pytest.param(
        None,
        "rules",
        [{"name": "a", "priority": 1}],
        [{"name": "a"}],
        id="no model",
    ),
]
"""A model, a list field of it, the list as read and as sent: one key of one item is
removed.
"""


@pytest.mark.parametrize(("model", "name", "read", "sent"), WHOLE_LISTS)
def test_key_removed_in_a_list_compared_whole_and_still_there_is_not_applied(
    model: type[VcoModel] | None, name: str, read: list[object], sent: list[object]
) -> None:
    check = check_response({name: read}, {name: sent}, {name: read}, model)
    assert_paths(check, not_applied=[name])
    change = not_applied_at(check, name)
    assert change.stored == read
    assert change.clash is False


@pytest.mark.parametrize(("model", "name", "read", "sent"), WHOLE_LISTS)
def test_key_removed_in_a_list_compared_whole_and_gone_is_applied(
    model: type[VcoModel] | None, name: str, read: list[object], sent: list[object]
) -> None:
    check = check_response({name: read}, {name: sent}, {name: sent}, model)
    assert_paths(check, applied=[name])


def test_key_removed_in_a_list_item_and_stored_with_another_value_is_not_applied() -> (
    None
):
    check = chk(
        {"hops": [{"ip": "10.0.0.1", "cost": 5}]},
        {"hops": [{"ip": "10.0.0.1"}]},
        {"hops": [{"ip": "10.0.0.1", "cost": 9}]},
    )
    assert_paths(check, not_applied=["hops"])
    assert not_applied_at(check, "hops").clash is True


def test_key_removed_beside_a_change_that_is_stored_is_not_applied() -> None:
    # Another writer stored the same cost; the write that removed the note was not.
    check = chk(
        {"hops": [{"ip": "10.0.0.1", "cost": 5, "note": "x"}]},
        {"hops": [{"ip": "10.0.0.1", "cost": 6}]},
        {"hops": [{"ip": "10.0.0.1", "cost": 6, "note": "x"}]},
    )
    assert_paths(check, not_applied=["hops"])


def test_key_the_vco_adds_where_none_was_read_is_a_server_change() -> None:
    check = chk(
        {"hops": [{"ip": "10.0.0.1"}]},
        {"hops": [{"ip": "10.0.0.2"}]},
        {"hops": [{"ip": "10.0.0.2", "cost": 0}]},
    )
    assert_paths(check, applied=["hops"], server_changes=["hops[#0].cost"])
    change = server_change_at(check, "hops[#0].cost")
    assert (change.as_read, change.stored) == (MISSING, 0)


def test_the_item_as_read_is_the_one_at_the_same_position() -> None:
    # The plan appends an item and removes a key of the first. The stored list
    # has the key, and an item another writer appended that holds the new one.
    check = chk(
        {"hops": [{"ip": "10.0.0.1", "cost": 5}]},
        {"hops": [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]},
        {"hops": [{"ip": "10.0.0.1", "cost": 5}, {"ip": "10.0.0.2"}]},
    )
    assert_paths(check, not_applied=["hops"])


def test_an_item_past_the_end_of_the_list_as_read_may_hold_more() -> None:
    check = chk(
        {"hops": [{"ip": "10.0.0.1", "cost": 5}]},
        {"hops": [{"ip": "10.0.0.1", "cost": 5}, {"ip": "10.0.0.2"}]},
        {"hops": [{"ip": "10.0.0.1", "cost": 5}, {"ip": "10.0.0.2", "cost": 0}]},
    )
    assert_paths(check, applied=["hops"], server_changes=["hops[#1].cost"])


def test_item_pairs_by_its_alt_key_before_its_key() -> None:
    # The write that raised rule a (i1) to priority 2 was not stored. Another
    # writer renamed that rule to b and made a new rule a (i9) with priority 2.
    check = chk(
        rules(rule("a", i="i1", p=1)),
        rules(rule("a", i="i1", p=2)),
        rules(rule("a", i="i9", p=2), rule("b", i="i1", p=1)),
    )
    assert paths(check.not_applied) == ["rules[a].priority"]
    assert check.applied == ()
    assert not_applied_at(check, "rules[a].priority").stored == 1


def test_removed_item_without_an_alt_key_does_not_take_another_writers_item() -> None:
    check = chk(rules(A, X), rules(A), rules(A, Z))
    assert_paths(check, applied=["rules[x]"], server_changes=["rules[z]"])


def test_item_taken_for_a_removal_is_not_paired_again() -> None:
    # Nothing was stored: the item the plan removed is still there, and the
    # item it added without a key is not.
    two = {"internalId": "u2", "family": "IPv4", "name": "two"}
    new = {"family": "IPv4", "name": "new"}
    check = chk({"links": [L1, two]}, {"links": [L1, new]}, {"links": [L1, two]})
    assert sorted(paths(check.not_applied)) == ["links[#1]", "links[u2,IPv4]"]
    added = not_applied_at(check, "links[#1]")
    assert added.stored is MISSING
    assert added.clash is False
    assert not_applied_at(check, "links[u2,IPv4]").stored == two


def test_a_change_gives_its_path_in_python_names() -> None:
    check = chk(
        {"sub": {"v": 1}, **rules(rule("a", i="i1"))},
        {"sub": {"v": 1}, **rules(rule("a", i="i2"))},
        {"sub": {"v": 1, "note": "n"}, **rules(rule("a", i="i1"))},
    )
    (lost,) = check.not_applied
    assert (lost.path, lost.python_path(Root)) == (
        "rules[a].ruleId",
        "rules[a].rule_id",
    )
    (extra,) = check.server_changes
    assert (extra.path, extra.python_path(Root)) == ("sub.note", "sub.note")
    row = PlannedChange(lost.path, MISSING, {}, MISSING, part="refs")
    assert row.python_path(Root) == "rules[a].ruleId"


def test_an_item_no_selector_names_has_no_stored_part() -> None:
    # Item 1 is named by its position, and "#1" is the name of item 0.
    r = rules(rule("#1"), rule("x", i="i1"), rule("x", i="i2"))
    p = rules(rule("#1"), rule("x", i="i1", p=5), rule("x", i="i2", p=6))
    check = chk(r, p, r)
    assert_paths(check, not_applied=["rules[#1].priority", "rules[#2].priority"])
    assert {str(path): part for path, part in check.stored_parts.items()} == {
        'rules["#1"]': Key("#1"),
        "rules[#2]": 2,
    }


class Lid(VcoModel):
    name: str
    lid: str | None = None
    port: int = 0


class Lids(VcoModel):
    rules: Annotated[list[Lid], KeyField("name", alt="lid")] = Field(
        default_factory=list
    )


def x(lid: str, port: int) -> dict[str, object]:
    return {"name": "x", "lid": lid, "port": port}


READ_TWO_X = {"rules": [x("a", 1), x("b", 2)]}


def test_an_added_item_the_vco_dropped_does_not_unpair_the_items_after_it() -> None:
    """The VCO revised both IDs and dropped the new item between the two x."""
    payload = {"rules": [x("a", 1), {"name": "n", "port": 7}, x("b", 3)]}
    stored = {"rules": [x("A2", 1), x("B2", 3)]}
    check = check_response(READ_TWO_X, payload, stored, Lids)
    assert paths(check.not_applied) == ["rules[n]"]
    assert "rules[#1].port" in paths(check.applied)


def test_another_writers_item_does_not_unpair_the_items_after_it() -> None:
    payload = {"rules": [x("a", 1), x("b", 3)]}
    stored = {"rules": [x("A2", 1), {"name": "w", "lid": "W", "port": 9}, x("B2", 3)]}
    check = check_response(READ_TWO_X, payload, stored, Lids)
    assert check.not_applied == ()
    assert paths(check.applied) == ["rules[#1].port"]
    assert "rules[w]" in paths(check.server_changes)


def test_another_writers_item_of_the_same_name_cannot_be_told_apart() -> None:
    """A guard: items of one name pair in order, so the edit meets the other item."""
    payload = {"rules": [x("a", 1), x("b", 3)]}
    stored = {"rules": [x("A2", 1), x("W", 9), x("B2", 3)]}
    check = check_response(READ_TWO_X, payload, stored, Lids)
    assert paths(check.not_applied) == ["rules[#1].port"]
