"""``patch_of``: the patch that makes a list of changes."""

import copy
import json
import random
from typing import Annotated, Any

import pytest
from pydantic import MISSING, Field
from pyvelo.client.models.configuration.device_settings.segment import SegmentDns
from pyvelo.core import KeyField, VcoModel
from pyvelo.core.changes import patch_of
from pyvelo.core.rebase import ItemMove, ItemPlace, Key, Path, RawChange, raw_changes

from .patch_models import Root


def dump(model: Root) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def doc(data: dict[str, Any]) -> dict[str, Any]:
    """*data* as ``Root`` dumps it, so that it compares with a dump."""
    return dump(Root.model_validate(data))


def rule(name: str, rule_id: str | None = None, **rest: object) -> dict[str, Any]:
    return {"name": name, **({} if rule_id is None else {"ruleId": rule_id}), **rest}


def route(dest: str, **rest: object) -> dict[str, Any]:
    return {"dest": dest, "prefix": 24, "gw": "10.0.0.1", "intf": "GE3", **rest}


def patched(old: dict[str, Any], patch: dict[str, object]) -> dict[str, Any]:
    """The dump of the model parsed from *old*, after *patch*."""
    model = Root.model_validate(old)
    _ = model.merge_patch(patch)
    return dump(model)


A, B, C = rule("a", "1"), rule("b", "2"), rule("c", "3")

ROUND_TRIPS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "scalars": ({"count": 1, "title": "t"}, {"count": 2, "title": None, "flag": True}),
    "leaf of a sub-model, and a key unset": (
        {"sub": {"v": 1, "opt": 3}},
        {"sub": {"v": 2}},
    ),
    "sub-model built": ({}, {"maybe": {"v": 4, "note": "n"}}),
    "sub-model unset": ({"maybe": {"v": 4}}, {}),
    "item edited": ({"rules": [A, B]}, {"rules": [{**A, "priority": 5}, B]}),
    "item added first": ({"rules": [A]}, {"rules": [rule("x"), A]}),
    "two items added in a row": (
        {"rules": [A, B]},
        {"rules": [A, rule("x"), rule("y"), B]},
    ),
    "item removed": ({"rules": [A, B]}, {"rules": [B]}),
    "item moved and edited": (
        {"rules": [A, B, C]},
        {"rules": [{**C, "priority": 2}, A, B]},
    ),
    "item added after a moved one": (
        {"rules": [A, B, C]},
        {"rules": [C, rule("x"), A, B]},
    ),
    "item renamed": ({"rules": [A, B]}, {"rules": [rule("z", "1"), B]}),
    "items that share a name": (
        {"rules": [rule("d", "1"), rule("d", "2"), rule("x", "3")]},
        {
            "rules": [
                rule("n"),
                rule("d", "2", priority=7),
                rule("d", "1"),
                rule("x", "3"),
            ]
        },
    ),
    "composite key": (
        {"routes": [route("10.1.0.0"), route("10.2.0.0")]},
        {"routes": [route("10.9.0.0"), route("10.2.0.0", cost=5), route("10.1.0.0")]},
    ),
    "map of models": (
        {"byName": {"a": {"v": 1}, "b": {"v": 2}}},
        {"byName": {"a": {"v": 9}, "c": {"v": 3}}},
    ),
    "lists without keys": (
        {"names": ["a", "b"], "seq": ["x"], "ports": [1], "hops": [{"ip": "10.0.0.1"}]},
        {"names": ["b", "c"], "seq": ["y", "x"], "ports": [], "hops": []},
    ),
}


@pytest.mark.parametrize("case", ROUND_TRIPS)
def test_the_patch_of_the_changes_makes_the_new_document(case: str) -> None:
    old, new = (doc(data) for data in ROUND_TRIPS[case])
    patch, left_out = patch_of(raw_changes(old, new, Root), Root)
    assert left_out == ()
    assert patched(old, patch) == new
    assert json.loads(json.dumps(patch)) == patch  # plain JSON, string keys


def test_fields_are_written_under_python_names_and_values_as_they_are() -> None:
    old = doc({"rules": [A], "hopsSafe": []})
    new = doc({"rules": [{**A, "ruleId": "9"}], "hopsSafe": [{"ip": "10.0.0.1"}]})
    patch, _ = patch_of(raw_changes(old, new, Root), Root)
    assert patch == {
        "hops_safe": [{"ip": "10.0.0.1", "cost": 0}],
        "rules": {"a": {"rule_id": "9"}},
    }


def test_adds_removes_and_moves_are_item_operations() -> None:
    old, new = doc({"rules": [A, B, C]}), doc({"rules": [C, rule("x"), A]})
    patch, _ = patch_of(raw_changes(old, new, Root), Root)
    assert patch == {
        "rules": {
            "b": {"_op": "delete"},
            "x": {
                "_op": "add",
                "name": "x",
                "priority": 0,
                "tags": [],
                "_position": "first",
            },
            "c": {"_position": "first"},
        }
    }


def test_a_run_of_placed_items_is_written_backwards_after_the_item_it_follows() -> None:
    old, new = doc({"rules": [A, B]}), doc({"rules": [A, rule("x"), rule("y"), B]})
    patch, _ = patch_of(raw_changes(old, new, Root), Root)
    ops = patch["rules"]
    assert isinstance(ops, dict)
    assert [(selector, op["_position"]) for selector, op in ops.items()] == [
        ("y", {"after": "a"}),
        ("x", {"after": "a"}),
    ]


def test_an_item_without_a_key_is_added_under_plus() -> None:
    link = {"internalId": "u1", "family": "IPv4", "name": "x"}
    old = doc({"links": [link]})
    new = doc({"links": [{"name": "n1"}, {"name": "n2"}, link]})
    patch, left_out = patch_of(raw_changes(old, new, Root), Root)
    assert left_out == ()
    ops = patch["links"]
    assert isinstance(ops, dict)
    assert [
        (selector, op["name"], op["_position"]) for selector, op in ops.items()
    ] == [
        ("+2", "n2", "first"),
        ("+", "n1", "first"),
    ]
    assert patched(old, patch) == new


@pytest.mark.parametrize(
    "change",
    [
        RawChange("futureKey", 1, 2),  # a key the model has no field for
        RawChange("title", "t", MISSING),  # the field cannot be unset
        RawChange("sub.opt", 1, None),  # a patch's null would unset it
        RawChange("labels.a", "x", "y"),  # below a value a patch replaces whole
        RawChange("hops[#0].cost", 0, 1),  # an item of a list without keys
        RawChange("rules[a]", {"name": "a"}, 5),  # an item replaced by a scalar
        RawChange(
            "rules", [], [rule("a"), rule("a")]
        ),  # a list value that repeats a key
        RawChange(Path(()), {}, {"count": 1}),  # the whole document
        # A map entry whose key reads as a new item or as a position.
        RawChange(Path(("byName", "+x")), MISSING, {"v": 1}),
        RawChange(Path(("byName", "#1")), MISSING, {"v": 1}),
    ],
)
def test_a_change_no_patch_can_make_is_left_out_and_writes_nothing(
    change: RawChange,
) -> None:
    kept = RawChange("count", 1, 2)
    assert patch_of([change, kept], Root) == ({"count": 2}, (change,))


def test_with_stored_parts_an_item_is_named_as_the_stored_list_names_it() -> None:
    changes = [
        RawChange("rules[#1].priority", 0, 7),
        RawChange("rules[x]", {"name": "x"}, MISSING),
        RawChange("rules[new]", MISSING, {"name": "new"}, place=ItemPlace(Key("a"))),
    ]
    stored_parts = {
        Path.parse("rules[#1]"): 2,
        Path.parse("rules[x]"): Key("x"),
        Path.parse("rules[a]"): Key("renamed"),
    }
    patch, left_out = patch_of(changes, Root, stored_parts=stored_parts)
    assert left_out == ()
    assert patch == {
        "rules": {
            "#2": {"priority": 7},
            "x": {"_op": "delete"},
            "new": {"_op": "add", "name": "new", "_position": {"after": "renamed"}},
        }
    }


def test_with_stored_parts_a_change_whose_item_is_gone_is_left_out() -> None:
    inside = RawChange("rules[gone].priority", 0, 7)
    after_it = RawChange(
        "rules[new]", MISSING, {"name": "new"}, place=ItemPlace(Key("gone"))
    )
    chained = RawChange(
        "rules[next]", MISSING, {"name": "next"}, place=ItemPlace(Key("new"))
    )
    order = RawChange(
        "rules",
        ["a", "b"],
        ["b", "a"],
        moves=(ItemMove(Key("b"), ItemPlace(Key("gone"))),),
    )
    stored_parts = {Path.parse("rules[a]"): Key("a"), Path.parse("rules[b]"): Key("b")}
    changes = [inside, after_it, chained, order, RawChange("count", 1, 2)]
    patch, left_out = patch_of(changes, Root, stored_parts=stored_parts)
    assert patch == {"count": 2}
    assert set(map(id, left_out)) == set(map(id, [inside, after_it, chained, order]))


def test_fields_of_an_item_that_is_there_are_set_and_the_item_placed() -> None:
    change = RawChange(
        "rules[a]",
        {"name": "a", "priority": 1},
        {"name": "a", "priority": 2},
        place=ItemPlace(),
    )
    stored_parts = {Path.parse("rules[a]"): 1}
    assert patch_of([change], Root, stored_parts=stored_parts) == (
        {"rules": {"#1": {"name": "a", "priority": 2, "_position": "first"}}},
        (),
    )


def _random_rule(rng: random.Random, name: str, rule_id: str | None) -> dict[str, Any]:
    out = rule(name, rule_id, priority=rng.randrange(3))
    if rng.random() < 0.3:
        out["sub"] = {"v": rng.randrange(3), "note": rng.choice([None, "n"])}
    return out


def _edited(rng: random.Random, old: dict[str, Any]) -> dict[str, Any]:
    """*old* with a few random edits to its rules.

    Fields, adds, removes, moves, renames.
    """
    new = copy.deepcopy(old)
    rules: list[dict[str, Any]] = new["rules"]
    names = {r["name"] for r in rules}
    for _ in range(rng.randrange(1, 6)):
        op = rng.choice(["edit", "add", "remove", "move", "rename"])
        if op == "add":
            name = f"n{rng.randrange(1000)}"
            if name not in names:
                names.add(name)
                rule_id = f"id-{name}" if rng.random() < 0.5 else None
                added = _random_rule(rng, name, rule_id)
                rules.insert(rng.randrange(len(rules) + 1), added)
        elif not rules:
            continue
        elif op == "edit":
            rng.choice(rules)["priority"] = rng.randrange(5, 9)
        elif op == "remove":
            _ = rules.pop(rng.randrange(len(rules)))
        elif op == "move":
            moved = rules.pop(rng.randrange(len(rules)))
            rules.insert(rng.randrange(len(rules) + 1), moved)
        else:
            renamed = rng.choice(rules)
            name = f"r{rng.randrange(1000)}"
            if "ruleId" in renamed and name not in names:
                names.add(name)
                renamed["name"] = name
    return new


@pytest.mark.parametrize("seed", range(4))
def test_random_edits_to_a_keyed_list_round_trip(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(300):
        names = [f"b{n}" for n in range(rng.randrange(7))]
        if seed % 2 and len(names) > 2:
            names[1] = names[0]  # two rules of one name, told apart by their IDs
        rules = [_random_rule(rng, name, f"id-{n}") for n, name in enumerate(names)]
        old = doc({"rules": rules})
        new = doc(_edited(rng, old))
        patch, left_out = patch_of(raw_changes(old, new, Root), Root)
        assert left_out == ()
        assert patched(old, patch) == new


def test_an_added_item_whose_key_reads_as_a_position_or_a_new_item_is_added_by_its_body() -> (  # noqa: E501 (a test's name)
    None
):
    old = doc({"rules": [A, B]})
    new = doc({"rules": [rule("#0"), A, rule("+"), rule("+2"), B, rule("#7")]})
    patch, left_out = patch_of(raw_changes(old, new, Root), Root)
    assert left_out == ()
    ops = patch["rules"]
    assert isinstance(ops, dict)
    assert sorted(ops) == ["+", "+2", "+3", "+4"]
    assert patched(old, patch) == new


def test_patch_of_steps_into_a_field_that_takes_a_sub_model_or_a_list() -> None:
    old = {
        "primaryProvider": {"ref": "deviceSettings:dns:primaryProvider"},
        "backupProvider": {"ref": "deviceSettings:dns:backupProvider"},
        "privateProviders": {"ref": "deviceSettings:dns:privateProviders"},
        "override": True,
        "localDNS": [],
    }
    new = {**old, "primaryProvider": {"ref": "deviceSettings:dns:backupProvider"}}
    patch, left_out = patch_of(raw_changes(old, new, SegmentDns), SegmentDns)
    assert left_out == ()
    assert patch == {"primary_provider": {"ref": "deviceSettings:dns:backupProvider"}}
    dns = SegmentDns.model_validate(old)
    _ = dns.merge_patch(patch)
    assert dns.model_dump(mode="json", by_alias=True)["primaryProvider"] == {
        "ref": "deviceSettings:dns:backupProvider"
    }


class Both(VcoModel):
    name: str
    both: int | None | MISSING = MISSING


class Holder(VcoModel):
    items: Annotated[list[Both], KeyField("name")] = Field(default_factory=list)


def test_a_set_that_gives_none_to_a_field_that_takes_none_and_missing_is_left_out() -> (
    None
):
    # A patch's null would unset the field: the change is left out whole.
    stored_parts = {
        Path.parse("items[a]"): Key("a"),
        Path.parse("items[b]"): Key("b"),
    }
    none = RawChange("items[a]", {"name": "a"}, {"name": "a", "both": None})
    kept = RawChange("items[b].both", MISSING, 3)
    assert patch_of([none, kept], Holder, stored_parts=stored_parts) == (
        {"items": {"b": {"both": 3}}},
        (none,),
    )


def test_a_set_that_gives_a_value_to_such_a_field_is_written() -> None:
    stored_parts = {Path.parse("items[a]"): Key("a")}
    some = RawChange("items[a]", {"name": "a"}, {"name": "a", "both": 2})
    assert patch_of([some], Holder, stored_parts=stored_parts) == (
        {"items": {"a": {"name": "a", "both": 2}}},
        (),
    )


def test_a_map_of_models_that_is_new_is_written_as_one_add_per_entry() -> None:
    old = {key: value for key, value in doc({}).items() if key != "byName"}
    new = doc({"byName": {"a": {"v": 1}, "b": {"v": 2}}})
    patch, left_out = patch_of(raw_changes(old, new, Root), Root)
    assert left_out == ()
    assert patch == {
        "by_name": {
            "a": {"_op": "add", "v": 1, "note": None},
            "b": {"_op": "add", "v": 2, "note": None},
        }
    }
    assert patched(old, patch) == new
    assert json.loads(json.dumps(patch)) == patch


def test_a_new_map_with_an_entry_that_cannot_be_added_is_left_out() -> None:
    change = RawChange("byName", MISSING, {"a": {"v": 1}, "+b": {"v": 2}})
    assert patch_of([change], Root) == ({}, (change,))
