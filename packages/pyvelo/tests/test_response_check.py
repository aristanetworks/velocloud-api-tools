"""The response check: which planned changes the VCO stored, and what else differs."""

from collections.abc import Iterable, Sequence

from pydantic import MISSING
from pyvelo.core.response_check import (
    PlannedChange,
    ResponseCheck,
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


def test_nothing_planned():
    check = chk({"count": 1}, {"count": 1}, {"count": 1})
    assert_paths(check)


def test_scalar_applied():
    check = chk({"count": 1}, {"count": 2}, {"count": 2})
    assert_paths(check, applied=["count"])
    assert check.applied == (PlannedChange(check.applied[0].path, 1, 2, 2),)


def test_scalar_refused():
    check = chk({"count": 1}, {"count": 2}, {"count": 1})
    assert_paths(check, not_applied=["count"])
    change = not_applied_at(check, "count")
    assert change.stored == 1
    assert change.clash is False


def test_scalar_moved():
    check = chk({"count": 1}, {"count": 2}, {"count": 3})
    assert_paths(check, not_applied=["count"])
    change = not_applied_at(check, "count")
    assert change.stored == 3
    assert change.clash is True


def test_object_added_may_hold_more():
    check = chk({}, {"maybe": {"v": 1}}, {"maybe": {"v": 1, "note": "x"}})
    assert_paths(check, applied=["maybe"], server_changes=["maybe.note"])
    change = server_change_at(check, "maybe.note")
    assert change.as_read is MISSING
    assert change.stored == "x"


def test_object_added_lacks_a_leaf():
    check = chk({}, {"maybe": {"v": 1, "note": "n"}}, {"maybe": {"v": 1}})
    assert_paths(check, not_applied=["maybe"])
    change = not_applied_at(check, "maybe")
    assert change.stored == {"v": 1}
    assert change.clash is True


def test_leaf_in_an_object_is_its_own_change():
    check = chk(
        {"sub": {"v": 1, "note": "n"}},
        {"sub": {"v": 2, "note": "n"}},
        {"sub": {"v": 2, "note": "z"}},
    )
    assert_paths(check, applied=["sub.v"], server_changes=["sub.note"])
    change = server_change_at(check, "sub.note")
    assert change.as_read == "n"
    assert change.stored == "z"


def test_key_removed():
    check = chk({"title": "t"}, {}, {})
    assert_paths(check, applied=["title"])
    (change,) = check.applied
    assert change.as_read == "t"
    assert change.intended is MISSING
    assert change.stored is MISSING


def test_key_removed_is_still_there():
    check = chk({"title": "t"}, {}, {"title": "t"})
    assert_paths(check, not_applied=["title"])
    change = not_applied_at(check, "title")
    assert change.stored == "t"
    assert change.clash is False


def test_unplanned_difference_is_a_server_change():
    check = chk(
        {"count": 1, "flag": False},
        {"count": 2, "flag": False},
        {"count": 2, "flag": True},
    )
    assert_paths(check, applied=["count"], server_changes=["flag"])
    change = server_change_at(check, "flag")
    assert change.as_read is False
    assert change.stored is True


def test_stored_object_is_gone():
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


def test_unkeyed_list_replaced_whole():
    check = chk({"hops": [H0]}, {"hops": [H1, H0]}, {"hops": [H1, H0]})
    assert_paths(check, applied=["hops"])


def test_unkeyed_list_in_another_order():
    check = chk({"hops": [H0]}, {"hops": [H1, H0]}, {"hops": [H0, H1]})
    assert_paths(check, not_applied=["hops"])


def test_list_items_may_hold_more():
    check = chk(
        {"hops": []}, {"hops": [{"ip": "1"}]}, {"hops": [{"ip": "1", "cost": 0}]}
    )
    assert_paths(check, applied=["hops"], server_changes=["hops[#0].cost"])


def test_keyed_item_changed():
    check = chk(
        rules(rule("a", p=1), B), rules(rule("a", p=2), B), rules(rule("a", p=2), B)
    )
    assert_paths(check, applied=["rules[a].priority"])


def test_keyed_item_added_and_given_an_id():
    check = chk(rules(A), rules(A, B), rules(A, rule("b", i="i7")))
    assert_paths(check, applied=["rules[b]"], server_changes=["rules[b].ruleId"])


def test_keyed_item_added_is_missing():
    check = chk(rules(A), rules(A, B), rules(A))
    assert_paths(check, not_applied=["rules[b]"])
    change = not_applied_at(check, "rules[b]")
    assert change.stored is MISSING
    assert change.clash is False


def test_keyed_item_removed():
    check = chk(rules(A, B), rules(A), rules(A))
    assert_paths(check, applied=["rules[b]"])


def test_keyed_item_removed_is_still_there():
    check = chk(rules(A, B), rules(A), rules(A, B))
    assert_paths(check, not_applied=["rules[b]"])
    change = not_applied_at(check, "rules[b]")
    assert change.stored == B
    assert change.clash is False


def test_reorder_is_checked_by_keys():
    check = chk(rules(A, B, C), rules(C, A, B), rules(C, A, B))
    assert_paths(check, applied=["rules"])


def test_reorder_refused():
    check = chk(rules(A, B, C), rules(C, A, B), rules(A, B, C))
    assert_paths(check, not_applied=["rules"])
    change = not_applied_at(check, "rules")
    assert change.as_read == ["a", "b", "c"]
    assert change.intended == ["c", "a", "b"]
    assert change.stored == ["a", "b", "c"]


def test_reorder_and_edit_are_two_changes():
    check = chk(
        rules(A, rule("b", p=1)), rules(rule("b", p=2), A), rules(rule("b", p=2), A)
    )
    assert_paths(check, applied=["rules", "rules[b].priority"])


def test_item_another_writer_added():
    check = chk(rules(rule("a", p=1)), rules(rule("a", p=2)), rules(rule("a", p=2), Z))
    assert_paths(check, applied=["rules[a].priority"], server_changes=["rules[z]"])
    change = server_change_at(check, "rules[z]")
    assert change.as_read is MISSING
    assert change.stored == Z


def test_add_and_remove_refused_is_as_read():
    check = chk(rules(A, X), rules(A, Y), rules(A, X))
    assert_paths(check, not_applied=["rules[y]", "rules[x]"])
    added = not_applied_at(check, "rules[y]")
    assert added.stored is MISSING
    assert added.clash is False
    removed = not_applied_at(check, "rules[x]")
    assert removed.stored == X
    assert removed.clash is False


def test_another_writers_item_is_not_mine():
    check = chk(rules(A), rules(A, Y), rules(A, Z))
    assert_paths(check, not_applied=["rules[y]"], server_changes=["rules[z]"])
    change = not_applied_at(check, "rules[y]")
    assert change.stored is MISSING
    assert change.clash is False


def test_repeated_keys_pair_in_order():
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


def test_keyless_added_item_pairs_with_what_is_left():
    check = chk(
        {"links": [L1]},
        {"links": [L1, {"family": "IPv4", "name": "new"}]},
        {"links": [L1, {"internalId": "u2", "family": "IPv4", "name": "new"}]},
    )
    assert_paths(check, applied=["links[#1]"], server_changes=["links[#1].internalId"])


def test_without_a_model_every_list_is_whole():
    check = check_response({"rules": [A]}, {"rules": [A, B]}, {"rules": [A, B]})
    assert_paths(check, applied=["rules"])


def test_unplanned_difference_in_a_keyed_list_is_told_by_item():
    check = chk(
        {"count": 1, "rules": [A]},
        {"count": 2, "rules": [A]},
        {"count": 2, "rules": [A, Z]},
    )
    assert_paths(check, applied=["count"], server_changes=["rules[z]"])
    change = server_change_at(check, "rules[z]")
    assert change.as_read is MISSING
    assert change.stored == Z


def test_order_the_plan_kept_and_the_vco_changed_is_a_server_change():
    check = chk(
        rules(rule("a", p=1), B), rules(rule("a", p=2), B), rules(B, rule("a", p=2))
    )
    assert_paths(check, applied=["rules[a].priority"], server_changes=["rules"])
    change = server_change_at(check, "rules")
    assert change.as_read == ["a", "b"]
    assert change.stored == ["b", "a"]


def test_stored_keyed_list_is_gone():
    check = chk(rules(rule("a", p=1)), rules(rule("a", p=2)), {})
    assert_paths(check, not_applied=["rules[a].priority"], server_changes=["rules"])
    planned = not_applied_at(check, "rules[a].priority")
    assert planned.stored is MISSING
    assert planned.clash is True
    change = server_change_at(check, "rules")
    assert change.as_read == [rule("a", p=1)]
    assert change.stored is MISSING


def test_removal_inside_a_container_the_vco_dropped_is_applied():
    check = chk({"sub": {"v": 1, "note": "n"}}, {"sub": {"v": 1}}, {})
    assert_paths(check, applied=["sub.note"], server_changes=["sub"])
    (removal,) = check.applied
    assert removal.as_read == "n"
    assert removal.intended is MISSING
    assert removal.stored is MISSING
    change = server_change_at(check, "sub")
    assert change.as_read == {"v": 1, "note": "n"}
    assert change.stored is MISSING


def test_removed_and_readded_under_one_name_is_applied():
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


def test_removed_item_found_by_its_alt_key():
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


def test_change_beside_that_removal_is_not_applied():
    check = chk({"sub": {"v": 1, "note": "n"}}, {"sub": {"v": 2}}, {})
    assert_paths(
        check, applied=["sub.note"], not_applied=["sub.v"], server_changes=["sub"]
    )
    planned = not_applied_at(check, "sub.v")
    assert planned.intended == 2
    assert planned.stored is MISSING
    assert planned.clash is True
