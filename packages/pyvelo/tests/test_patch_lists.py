"""Lists in a patch (spec 5.10.3): selectors, item operations, positions, list values, maps."""

import copy
import uuid
from typing import Annotated

import pytest
from pydantic import MISSING, Field
from pyvelo.config.resolver import ResolutionError
from pyvelo.core import PatchError, VcoModel, add, at, delete, move
from pyvelo.core.fields import KeyField
from pyvelo.core.merge import select_items
from pyvelo.core.rebase import Path

from .patch_models import Hop, Root, Rule, Sub
from .test_write_safe_resolution import Fake, fresh


def raises_at(model: VcoModel, patch: dict[str, object], path: str) -> PatchError:
    with pytest.raises(PatchError) as exc:
        _ = model.merge_patch(patch)
    assert exc.value.path == Path.parse(path)
    return exc.value


def abc() -> Root:
    return Root(rules=[Rule(name="a"), Rule(name="b"), Rule(name="c")])


def names(root: Root) -> list[str]:
    return [rule.name for rule in root.rules]


def hops() -> Root:
    return Root(hops=[Hop(ip=f"10.0.0.{n}") for n in range(3)], seq=["a", "b"])


# ---------------------------------------------------------------------------
# Keyed lists
# ---------------------------------------------------------------------------


def test_change_by_key():
    root = abc()
    b = root.rules[1]
    _ = root.merge_patch({"rules": {"b": {"priority": 5}}})
    assert b.priority == 5
    assert root.rules[1] is b
    assert names(root) == ["a", "b", "c"]


def test_change_by_position():
    root = abc()
    _ = root.merge_patch({"rules": {at(2): {"priority": 5}}})
    assert root.rules[2].priority == 5


def test_key_wins_over_syntax():
    root = Root(rules=[Rule(name="a"), Rule(name="#0"), Rule(name="+x")])
    _ = root.merge_patch({"rules": {"#0": {"priority": 1}, "+x": {"priority": 2}}})
    assert [r.priority for r in root.rules] == [0, 1, 2]


def test_add_appends():
    root = abc()
    _ = root.merge_patch({"rules": {"z": add({"priority": 1})}})
    assert names(root) == ["a", "b", "c", "z"]
    assert root.rules[3].priority == 1


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        (add(first=True), "z a b c"),
        (add(last=True), "a b c z"),
        (add(before="b"), "a z b c"),
        (add(after="b"), "a b z c"),
        (add(after=at(0)), "a z b c"),
    ],
)
def test_add_with_each_place(op: dict[str, object], expected: str):
    root = abc()
    _ = root.merge_patch({"rules": {"z": op}})
    assert names(root) == expected.split()


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"b": add()}, "rules[b]"),
        ({at(0): add()}, "rules[#0]"),
        ({"z": add({"name": "y"})}, "rules[z]"),
    ],
)
def test_add_refusals(patch: dict[str, object], path: str):
    _ = raises_at(abc(), {"rules": patch}, path)


def test_delete():
    root = abc()
    _ = root.merge_patch({"rules": {"b": delete()}})
    assert names(root) == ["a", "c"]


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"c": move(first=True)}, "c a b"),
        ({"a": move(after="b")}, "b a c"),
    ],
)
def test_move(patch: dict[str, object], expected: str):
    root = abc()
    _ = root.merge_patch({"rules": patch})
    assert names(root) == expected.split()


def test_change_and_move_together():
    root = abc()
    _ = root.merge_patch({"rules": {"c": {"priority": 5, "_position": "first"}}})
    assert names(root) == ["c", "a", "b"]
    assert root.rules[0].priority == 5


def test_rename_to_a_free_key():
    root = abc()
    b = root.rules[1]
    _ = root.merge_patch({"rules": {"b": {"name": "x"}}})
    assert names(root) == ["a", "x", "c"]
    assert root.rules[1] is b


def test_rename_to_a_taken_key_raises():
    root = abc()
    dump = root.model_dump()
    with pytest.raises(PatchError):
        _ = root.merge_patch({"rules": {"b": {"name": "a"}}})
    assert root.model_dump() == dump


def test_unmatched_plain_object_raises():
    error = raises_at(abc(), {"rules": {"zz": {"priority": 1}}}, "rules[zz]")
    assert "add(" in str(error)


@pytest.mark.parametrize("value", [None, 3, [1]])
def test_null_scalar_and_list_item_values_raise(value: object):
    error = raises_at(abc(), {"rules": {"a": value}}, "rules[a]")
    if value is None:
        assert "delete()" in str(error)


@pytest.mark.parametrize(
    "patch",
    [
        {"a": move(before="zz")},
        {"a": move(before="a")},
        {"a": move(before="b"), "b": delete()},
    ],
)
def test_anchor_errors(patch: dict[str, object]):
    with pytest.raises(PatchError):
        _ = abc().merge_patch({"rules": patch})


def test_two_selectors_for_one_item_raise():
    with pytest.raises(PatchError):
        _ = abc().merge_patch({"rules": {"a": {"priority": 1}, at(0): {"priority": 2}}})


def test_duplicate_key_in_the_list_raises_and_lists_the_matches():
    root = Root(rules=[Rule(name="a"), Rule(name="a"), Rule(name="b")])
    error = raises_at(root, {"rules": {"a": {"priority": 1}}}, "rules[a]")
    assert "#0" in str(error)
    assert "#1" in str(error)


def test_duplicate_is_reachable_by_position():
    root = Root(rules=[Rule(name="a"), Rule(name="a"), Rule(name="b")])
    _ = root.merge_patch({"rules": {at(1): {"priority": 1}}})
    assert [r.priority for r in root.rules] == [0, 1, 0]


def test_order_rule():
    root = abc()
    _ = root.merge_patch(
        {
            "rules": {
                "z": add(after="a"),
                "c": move(first=True),
                "b": delete(),
                "a": {"priority": 9},
            }
        }
    )
    assert names(root) == ["c", "a", "z"]
    assert root.rules[1].priority == 9


def test_position_is_counted_in_the_list_as_it_was():
    root = abc()
    _ = root.merge_patch({"rules": {at(0): delete(), at(2): {"priority": 5}}})
    assert names(root) == ["b", "c"]
    assert root.rules[1].priority == 5


def test_int_selector_names_the_item():
    root = Root(rules=[Rule(name="10")])
    _ = root.merge_patch({"rules": {10: {"priority": 1}}})
    assert root.rules[0].priority == 1


class MaybeNamed(VcoModel):
    name: str | None = None
    v: int = 0


class MaybeNamedHolder(VcoModel):
    items: Annotated[list[MaybeNamed], KeyField("name")] = Field(default_factory=list)


def test_item_without_a_key_is_named_by_position_only():
    holder = MaybeNamedHolder(items=[MaybeNamed(), MaybeNamed(name="a")])
    _ = raises_at(holder, {"items": {"None": {"v": 1}}}, "items[None]")
    _ = holder.merge_patch({"items": {at(0): {"v": 1}}})
    assert [i.v for i in holder.items] == [1, 0]


def test_list_value_replaces_a_keyed_list():
    root = abc()
    _ = root.merge_patch({"rules": [{"name": "x"}, {"name": "y"}]})
    assert names(root) == ["x", "y"]


def test_list_value_with_a_repeated_key_raises():
    _ = raises_at(abc(), {"rules": [{"name": "x"}, {"name": "x"}]}, "rules")


def test_one_add_object_serves_two_lists():
    op = add({"priority": 1})
    first, second = abc(), abc()
    _ = first.merge_patch({"rules": {"z": op}})
    _ = second.merge_patch({"rules": {"z": op}})
    assert names(first)[-1] == names(second)[-1] == "z"
    assert op == add({"priority": 1})


@pytest.mark.parametrize(
    ("field", "value", "path"),
    [
        ("rules", [{"name": "a", "nope": 1}], "rules[a].nope"),
        ("hops", [{"ip": "1.1.1.1"}, {"ip": "1.1.1.2", "nope": 1}], "hops[#1].nope"),
    ],
)
def test_list_value_item_with_an_unknown_key_raises(
    field: str, value: list[object], path: str
):
    _ = raises_at(Root(), {field: value}, path)


def test_select_items():
    root = Root(rules=[Rule(name="a"), Rule(name="a"), Rule(name="10")])
    assert select_items(root, "rules", "a") == [0, 1]
    assert select_items(root, "rules", 10) == [2]
    assert select_items(root, "rules", at(2)) == [2]
    assert select_items(root, "rules", at(3)) == []
    assert select_items(root, "rules", "zz") == []
    assert select_items(hops(), "hops", at(1)) == [1]
    assert select_items(hops(), "hops", "10.0.0.1") == []


# ---------------------------------------------------------------------------
# Value lists
# ---------------------------------------------------------------------------


def values() -> Root:
    return Root(names=["a", "b", "c"], ports=[80, 443])


def test_value_add_and_delete():
    root = values()
    _ = root.merge_patch({"names": {"z": add(), "b": delete()}})
    assert root.names == ["a", "c", "z"]


def test_value_add_is_validated_as_the_element():
    root = values()
    _ = root.merge_patch({"ports": {"8080": add()}})
    assert root.ports == [80, 443, 8080]
    assert isinstance(root.ports[2], int)
    _ = raises_at(values(), {"ports": {"x": add()}}, "ports[x]")


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"c": move(first=True)}, ["c", "a", "b"]),
        ({at(0): delete()}, ["b", "c"]),
    ],
)
def test_value_move_and_position(patch: dict[str, object], expected: list[str]):
    root = values()
    _ = root.merge_patch({"names": patch})
    assert root.names == expected


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"a": add()}, "names[a]"),
        ({"z": add({"x": 1})}, "names[z]"),
        ({"a": {"x": 1}}, "names[a]"),
        ({"zz": delete()}, "names[zz]"),
    ],
)
def test_value_refusals(patch: dict[str, object], path: str):
    _ = raises_at(values(), {"names": patch}, path)


def test_list_value_replaces_a_value_list():
    root = values()
    _ = root.merge_patch({"names": ["x", "x"]})
    assert root.names == ["x", "x"]


# ---------------------------------------------------------------------------
# Positional and undeclared lists
# ---------------------------------------------------------------------------


def test_positional_change_delete_add():
    root = hops()
    _ = root.merge_patch(
        {
            "hops": {
                at(0): {"cost": 5},
                at(2): delete(),
                "+": add({"ip": "9.9.9.9"}, after=at(0)),
            }
        }
    )
    assert [h.ip for h in root.hops] == ["10.0.0.0", "9.9.9.9", "10.0.0.1"]
    assert root.hops[0].cost == 5


def test_two_new_items_need_tags():
    root = hops()
    _ = root.merge_patch(
        {
            "hops": {
                "+a": add({"ip": "1.1.1.1"}),
                "+b": add({"ip": "2.2.2.2"}),
            }
        }
    )
    assert [h.ip for h in root.hops][3:] == ["1.1.1.1", "2.2.2.2"]


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"x": {"cost": 1}}, "hops[x]"),
        ({"x": add({"ip": "1.1.1.1"})}, "hops[x]"),
        ({"+": {"cost": 1}}, 'hops["+"]'),
        ({at(9): delete()}, "hops[#9]"),
        ({"+": add({"cost": 1})}, 'hops["+"]'),
    ],
)
def test_positional_refusals(patch: dict[str, object], path: str):
    _ = raises_at(hops(), {"hops": patch}, path)


def test_declared_positional_primitives():
    root = hops()
    _ = root.merge_patch({"seq": {at(0): delete()}})
    assert root.seq == ["b"]
    root = hops()
    _ = root.merge_patch({"seq": {at(1): move(first=True)}})
    assert root.seq == ["b", "a"]
    _ = raises_at(hops(), {"seq": {"a": delete()}}, "seq[a]")
    error = raises_at(hops(), {"seq": {"+": add()}}, 'seq["+"]')
    assert "list value" in str(error)


def test_list_value_replaces_a_positional_list():
    root = hops()
    _ = root.merge_patch({"hops": [{"ip": "1.1.1.1"}]})
    assert root.hops == [Hop(ip="1.1.1.1")]


# ---------------------------------------------------------------------------
# Untyped lists, maps, lists inside built items
# ---------------------------------------------------------------------------


def test_untyped_takes_a_list_only():
    root = Root()
    _ = root.merge_patch({"blobs": [{"a": 1}]})
    assert root.blobs == [{"a": 1}]
    _ = raises_at(Root(), {"blobs": {"a": {"b": 1}}}, "blobs")


def test_map_change_add_delete():
    root = Root(by_name={"x": Sub()})
    x = root.by_name["x"]
    _ = root.merge_patch({"by_name": {"x": {"v": 1}, "y": add({"v": 2}), "x2": add()}})
    assert root.by_name["x"] is x
    assert x.v == 1
    assert root.by_name["y"].v == 2
    assert root.by_name["x2"] == Sub()
    _ = root.merge_patch({"by_name": {"y": delete()}})
    assert "y" not in root.by_name


@pytest.mark.parametrize(
    ("value", "path"),
    [
        ({"x": move(first=True)}, "by_name[x]"),
        ({"x": {"v": 1, "_position": "first"}}, "by_name[x]"),
        ({at(0): {"v": 1}}, 'by_name["#0"]'),
        ({"x": add()}, "by_name[x]"),
        ({"zz": {"v": 1}}, "by_name[zz]"),
        ([{"v": 1}], "by_name"),
        (3, "by_name"),
    ],
)
def test_map_refusals(value: object, path: str):
    _ = raises_at(Root(by_name={"x": Sub()}), {"by_name": value}, path)


def test_plain_dict_is_replaced():
    root = Root(labels={"x": "y"})
    _ = root.merge_patch({"labels": {"a": "b"}})
    assert root.labels == {"a": "b"}


def test_list_inside_a_new_item_as_add_entries():
    root = abc()
    _ = root.merge_patch(
        {"rules": {"z": add({"tags": {"t1": add(), "t2": add(first=True)}})}}
    )
    assert root.rules[3].tags == ["t2", "t1"]


@pytest.mark.parametrize(
    "tags",
    [{"t1": delete()}, {"t1": add(before="t0")}],
)
def test_list_inside_a_new_item_refuses_other_operations(tags: dict[str, object]):
    _ = raises_at(abc(), {"rules": {"z": add({"tags": tags})}}, "rules[z].tags[t1]")


class UnsetRules(VcoModel):
    rules: Annotated[list[Rule] | MISSING, KeyField("name")] = MISSING


def test_unset_keyed_list_is_created_by_an_add():
    model = UnsetRules()
    _ = model.merge_patch({"rules": {"z": add()}})
    assert model.rules == [Rule(name="z")]


# ---------------------------------------------------------------------------
# Names (spec 5.9): a keyed list whose key is a NameOf field
# ---------------------------------------------------------------------------


def test_selector_in_another_case_selects():
    fw = fresh()
    _ = fw.merge_patch(
        {
            "segments": {
                "global segment": {
                    "outbound": {
                        "r1": add({"action": {"allow_or_deny": "deny"}, "match": {}})
                    }
                }
            }
        },
        resolvers=Fake(),
    )
    assert [s.segment.name for s in fw.segments] == ["Global Segment"]
    assert [r.name for r in fw.segments[0].outbound] == ["r1"]


def test_add_by_name_adds_the_resolvers_name():
    fw = fresh()
    _ = fw.merge_patch({"segments": {"guest": add({"outbound": []})}}, resolvers=Fake())
    meta = fw.segments[1].segment
    assert (meta.name, meta.segment_id, meta.segment_logical_id) == (
        "Guest",
        2,
        uuid.UUID(int=3),
    )


@pytest.mark.parametrize("op", [{"outbound": []}, add()])
def test_unknown_name_raises_resolution_error(op: dict[str, object]):
    fw = fresh()
    dump = copy.deepcopy(fw.model_dump())
    with pytest.raises(ResolutionError) as exc:
        _ = fw.merge_patch({"segments": {"gest": op}}, resolvers=Fake())
    assert exc.value.candidates == ["Guest"]
    assert fw.model_dump() == dump
