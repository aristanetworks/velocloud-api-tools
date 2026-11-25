"""The patch helpers add, delete, move, at and the parsing of item operations."""

import json
import uuid

import pytest
from pyvelo.core import PatchError
from pyvelo.core.patch import (
    ItemOp,
    Place,
    add,
    at,
    delete,
    is_new,
    move,
    parse_item_op,
    position_of,
)
from pyvelo.core.rebase import Path

PATH = Path.parse("rules[a]")


def test_at():
    assert at(0) == "#0"
    with pytest.raises(ValueError):
        at(-1)


def test_delete():
    assert delete() == {"_op": "delete"}


def test_add_shapes():
    assert add() == {"_op": "add"}
    assert add({"a": 1}) == {"_op": "add", "a": 1}
    assert add({"a": 1}, before="x") == {
        "_op": "add",
        "a": 1,
        "_position": {"before": "x"},
    }
    assert add(first=True)["_position"] == "first"
    assert add(after=("a", 1))["_position"] == {"after": ["a", 1]}


def test_move_shapes():
    assert move(last=True) == {"_position": "last"}
    assert move(after=at(0)) == {"_position": {"after": "#0"}}


def test_place_is_exactly_one():
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


def test_helpers_return_json():
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


def test_scalar_anchor_is_written_as_json():
    value = move(before=uuid.UUID(int=1))
    assert value == {"_position": {"before": "00000000-0000-0000-0000-000000000001"}}
    assert json.loads(json.dumps(value)) == value


def test_parse_change():
    assert parse_item_op({"a": 1}, PATH) == ItemOp("change", {"a": 1}, None)
    op = parse_item_op({"a": 1, "_position": "first"}, PATH)
    assert op.kind == "change"
    assert op.body == {"a": 1}
    assert op.place == Place("first")


def test_parse_add_delete_move():
    assert parse_item_op(add({"a": 1}, after="x"), PATH) == ItemOp(
        "add", {"a": 1}, Place("after", "x")
    )
    assert parse_item_op(delete(), PATH).kind == "delete"
    op = parse_item_op(move(first=True), PATH)
    assert op.kind == "move"
    assert op.body == {}
    assert op.place == Place("first")


def test_parse_composite_anchor():
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
        ({}, ""),
    ],
)
def test_parse_refuses(value: object, text: str):
    with pytest.raises(PatchError) as exc:
        parse_item_op(value, PATH)
    assert exc.value.path == PATH
    assert text in exc.value.message


def test_an_explicit_null_op_is_refused():
    with pytest.raises(PatchError) as exc:
        parse_item_op({"_op": None, "a": 1}, PATH)
    assert exc.value.path == PATH
    assert '"add" or "delete"' in exc.value.message


@pytest.mark.parametrize("word", ["before", "after"])
def test_removed_string_position_names_its_own_form(word: str):
    with pytest.raises(PatchError) as exc:
        parse_item_op({"_position": f"{word}:x"}, PATH)
    assert f'"{word}:key" form' in exc.value.message


def test_helpers_do_not_mutate_the_body():
    body: dict[str, object] = {"a": 1, "sub": {"b": 2}}
    value = add(body, before="x")
    assert body == {"a": 1, "sub": {"b": 2}}
    patch = dict(value)
    op = parse_item_op(patch, PATH)
    assert patch == value
    assert op.body == body


def test_a_position_with_too_many_digits_is_no_position():
    assert position_of("#" + "9" * 18) == 10**18 - 1
    assert position_of("#" + "1" * 5000) is None


def test_selector_syntax():
    assert position_of("#12") == 12
    assert position_of("#x") is None
    assert position_of("12") is None
    assert position_of(3) is None
    assert is_new("+")
    assert is_new("+tag")
    assert not is_new("a+")
