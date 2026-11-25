"""The patch helpers add, delete, move, at and the parsing of item operations."""

import json
import uuid
from typing import cast

import pytest
from pyvelo.core import PatchError
from pyvelo.core.patch import (
    MAX_PATCH_DEPTH,
    ItemOp,
    Place,
    add,
    at,
    checked_patch,
    delete,
    is_new,
    move,
    parse_item_op,
    position_of,
)
from pyvelo.core.rebase import Path

PATH = Path.parse("rules[a]")


def test_at() -> None:
    assert at(0) == "#0"
    with pytest.raises(ValueError):
        at(-1)


def test_delete() -> None:
    assert delete() == {"_op": "delete"}


def test_add_shapes() -> None:
    assert add() == {"_op": "add"}
    assert add({"a": 1}) == {"_op": "add", "a": 1}
    assert add({"a": 1}, before="x") == {
        "_op": "add",
        "a": 1,
        "_position": {"before": "x"},
    }
    assert add(first=True)["_position"] == "first"
    assert add(after=("a", 1))["_position"] == {"after": ["a", 1]}


def test_move_shapes() -> None:
    assert move(last=True) == {"_position": "last"}
    assert move(after=at(0)) == {"_position": {"after": "#0"}}


def test_place_is_exactly_one() -> None:
    with pytest.raises(TypeError):
        move()
    with pytest.raises(TypeError):
        move(first=True, last=True)
    with pytest.raises(TypeError):
        add(first=True, before="x")
    with pytest.raises(TypeError):
        add({"_op": "x"})
    with pytest.raises(TypeError):
        add({"_position": "first"})


def test_helpers_return_json() -> None:
    values = [
        at(0),
        delete(),
        add(),
        add({"a": 1}),
        add({"a": 1}, before="x"),
        add(first=True),
        add(after=("a", 1)),
        move(last=True),
        move(after=at(0)),
    ]
    for v in values:
        assert json.loads(json.dumps(v)) == v


def test_scalar_anchor_is_written_as_json() -> None:
    value = move(before=uuid.UUID(int=1))
    assert value == {"_position": {"before": "00000000-0000-0000-0000-000000000001"}}
    assert json.loads(json.dumps(value)) == value


def test_parse_change() -> None:
    assert parse_item_op({"a": 1}, PATH) == ItemOp("change", {"a": 1}, None)
    op = parse_item_op({"a": 1, "_position": "first"}, PATH)
    assert op.kind == "change"
    assert op.body == {"a": 1}
    assert op.place == Place("first")


def test_an_empty_object_is_a_change_of_nothing() -> None:
    assert parse_item_op({}, PATH) == ItemOp("change", {}, None)


def test_parse_add_delete_move() -> None:
    assert parse_item_op(add({"a": 1}, after="x"), PATH) == ItemOp(
        "add", {"a": 1}, Place("after", "x")
    )
    assert parse_item_op(delete(), PATH).kind == "delete"
    op = parse_item_op(move(first=True), PATH)
    assert op.kind == "move"
    assert op.body == {}
    assert op.place == Place("first")


def test_parse_composite_anchor() -> None:
    op = parse_item_op(add(after=("a", 1)), PATH)
    assert op.place == Place("after", ("a", 1))


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, "delete()"),
        (3, ""),
        ([1], ""),
        ({"_op": "replace"}, "replace"),
        ({"_op": "delete", "a": 1}, ""),
        ({"_position": 0}, "removed"),
        ({"_position": "before:x"}, "removed"),
        ({"_position": {"before": "a", "after": "b"}}, ""),
    ],
)
def test_parse_refuses(value: object, text: str) -> None:
    with pytest.raises(PatchError) as exc:
        parse_item_op(value, PATH)
    assert exc.value.path == PATH
    assert text in exc.value.message


def test_an_explicit_null_op_is_refused() -> None:
    with pytest.raises(PatchError) as exc:
        parse_item_op({"_op": None, "a": 1}, PATH)
    assert exc.value.path == PATH
    assert '"add" or "delete"' in exc.value.message


@pytest.mark.parametrize("word", ["before", "after"])
def test_removed_string_position_names_its_own_form(word: str) -> None:
    with pytest.raises(PatchError) as exc:
        parse_item_op({"_position": f"{word}:x"}, PATH)
    assert f'"{word}:key" form' in exc.value.message


def test_helpers_do_not_mutate_the_body() -> None:
    body: dict[str, object] = {"a": 1, "sub": {"b": 2}}
    value = add(body, before="x")
    assert body == {"a": 1, "sub": {"b": 2}}
    patch = dict(value)
    op = parse_item_op(patch, PATH)
    assert patch == value
    assert op.body == body


def test_a_position_with_too_many_digits_is_no_position() -> None:
    assert position_of("#" + "9" * 18) == 10**18 - 1
    assert position_of("#" + "1" * 5000) is None


def test_selector_syntax() -> None:
    assert position_of("#12") == 12
    assert position_of("#x") is None
    assert position_of("12") is None
    assert position_of(3) is None
    assert is_new("+")
    assert is_new("+tag")
    assert not is_new("a+")


def test_what_the_helpers_return_passes_the_check() -> None:
    values: list[object] = [
        at(0),
        delete(),
        add(),
        add({"a": 1}, before="x"),
        add(first=True),
        add(after=("a", 1)),
        move(last=True),
        move(after=at(0)),
        move(before=uuid.UUID(int=1)),
    ]
    for value in values:
        assert checked_patch(value) == value


def test_the_checked_patch_is_a_copy_with_the_selector_keys_kept() -> None:
    key = ("10.0.0.0", 24, uuid.UUID(int=1))
    patch = {"routes": {key: {"cost": 1}, 10: {"tags": ["t"]}}}
    checked = cast("dict[str, dict[object, dict[str, object]]]", checked_patch(patch))
    assert checked == patch
    assert checked is not patch
    assert checked["routes"] is not patch["routes"]
    assert checked["routes"][10]["tags"] is not patch["routes"][10]["tags"]
    assert list(checked["routes"]) == [key, 10]


def test_the_check_reports_and_counts_from_the_path_given() -> None:
    with pytest.raises(PatchError) as exc:
        _ = checked_patch([1, {2}], Path.parse("segments[Guest].outbound"))
    assert exc.value.path == Path.parse("segments[Guest].outbound[#1]")

    assert MAX_PATCH_DEPTH == 64
    start = Path(["a"] * 63)
    assert checked_patch({"x": 1}, start) == {"x": 1}
    with pytest.raises(PatchError) as exc:
        _ = checked_patch({"x": {}}, start)
    assert exc.value.path == start.child("x")
    assert "64" in exc.value.message
