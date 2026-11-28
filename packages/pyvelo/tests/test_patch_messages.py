"""What a refusal says: the JSON form of each operation it names."""

from typing import get_args

import pytest
from pyvelo.config.resolver import ResolverRegistry
from pyvelo.core import PatchError, add, at, delete, move
from pyvelo.core.merge import nest_patch
from pyvelo.core.patch import PatchErrorKind

from .patch_models import Hop, Link, Root, Route, Rule, Sub
from .test_patch_lists import ById, SubOrHop
from .test_patch_strict import Segmented

FORMS = {
    "add(": '"_op": "add"',
    "delete()": '"_op": "delete"',
    "move(": '"_position"',
    "at(n)": '"#n"',
}


def base() -> Root:
    return Root(
        rules=[Rule(name="a", rule_id="i1"), Rule(name="b", rule_id="i2")],
        names=["a", "b"],
        ports=[80],
        hops=[Hop(ip="10.0.0.1")],
        seq=["s"],
        by_name={"x": Sub()},
        routes=[Route(dest="10.0.0.0", prefix=24, gw="192.168.1.1", intf="GE3")],
        links=[
            Link(internal_id="u1", name="Internet"),
            Link(internal_id="u2", name="Internet"),
        ],
    )


@pytest.mark.parametrize(
    ("patch", "helper"),
    [
        ({"rules": {"a": None}}, "delete()"),
        ({"rules": {"a": {"_op": "delete", "priority": 1}}}, "delete()"),
        ({"rules": {"a": {"_position": {"before": None}}}}, "at(n)"),
        ({"rules": {"a": {"_op": "move"}}}, "move("),
        ({"rules": {"a": {"_op": "update"}}}, "add("),
        ({"rules": {"a": 5}}, "add("),
        ({"rules": {"a": 5}}, "delete()"),
        ({"rules": {"a": add()}}, "plain object"),
        ({"by_name": {"x": add()}}, "plain object"),
        ({"rules": {"zz": {"priority": 1}}}, "add("),
        ({"rules": {"z": add({"tags": {"t": delete()}})}}, "add("),
        ({"rules": {at(0): add()}}, "at(n)"),
        ({"rules": {"+": {"priority": 1}}}, "add("),
        ({"hops": {"x": add({"ip": "10.0.0.2"})}}, "add("),
        ({"hops": {"x": {"cost": 1}}}, "at(n)"),
        ({"hops": {"+": {"cost": 1}}}, "add("),
        ({"names": {"z": add({"x": 1})}}, "add("),
        ({"names": {"a": {"x": 1}}}, "delete()"),
        ({"by_name": {"x": move(first=True)}}, "move("),
        ({"by_name": {"zz": {"v": 1}}}, "add("),
        ({"links": {"Backup": add()}}, "add("),
        ({"routes": {"10.0.0.0/24": {"cost": 5}}}, "add("),
    ],
)
def test_a_refusal_that_names_a_helper_names_its_json_form(
    patch: dict[str, object], helper: str
) -> None:
    with pytest.raises(PatchError) as exc:
        _ = base().merge_patch(patch)
    message = exc.value.message
    assert helper in message
    for name, form in FORMS.items():
        if name in message:
            assert form in message


def test_a_built_map_refusal_names_the_json_form() -> None:
    with pytest.raises(PatchError) as exc:
        _ = ById().merge_patch({"m": {"1": {"v": 1}}})
    assert '{"_op": "add", ...}' in exc.value.message


def test_the_null_refusal_does_not_speak_of_missing() -> None:
    with pytest.raises(PatchError) as exc:
        _ = base().merge_patch({"flag": None})
    assert exc.value.message == (
        "null is not a value of flag, and a change cannot unset it: "
        "give a value, or leave the key out"
    )


KINDS: list[tuple[dict[str, object], str]] = [
    ({"names": {"a", "b"}}, "not_json"),
    ({"blobs": [{"a": float("nan")}]}, "not_json"),
    ({"nope": 1}, "unknown_field"),
    ({"sub": {"nope": 1}}, "unknown_field"),
    ({"rules": {"a": {"rule_id": "x", "ruleId": "y"}}}, "duplicate"),
    ({"rules": [{"name": "x"}, {"name": "x"}]}, "duplicate"),
    (
        {"rules": [{"name": "x", "rule_id": "i"}, {"name": "y", "rule_id": "i"}]},
        "duplicate",
    ),
    ({"rules": {"a": {"priority": 1}, at(0): {"priority": 2}}}, "duplicate"),
    (
        {
            "links": {
                "+a": add({"name": "x", "internal_id": "u9"}),
                "+b": add({"name": "y", "internal_id": "u9"}),
            }
        },
        "duplicate",
    ),
    ({"flag": None}, "null"),
    ({"rules": {"z": add({"priority": None})}}, "null"),
    ({"rules": {"a": None}}, "null"),
    ({"count": "abc"}, "invalid_value"),
    ({"count": True}, "invalid_value"),
    ({"rules": {"z": add({"priority": "abc"})}}, "invalid_value"),
    ({"ports": {"x": add()}}, "invalid_value"),
    ({"_op": "add"}, "operation"),
    ({"_custom": 1}, "operation"),
    ({"by_name": [1]}, "operation"),
    ({"blobs": {"a": {"b": 1}}}, "operation"),
    ({"rules": {"z": add({"tags": {"t": delete()}})}}, "operation"),
    ({"rules": {"a": move(before="b"), "b": delete()}}, "operation"),
    ({"rules": {"a": move(before="a")}}, "operation"),
    ({"seq": {"+": add()}}, "operation"),
    ({"names": {"z": add({"x": 1})}}, "operation"),
    ({"names": {"a": {"x": 1}}}, "operation"),
    ({"rules": {"z": add({"name": "y"})}}, "operation"),
    ({"rules": {"+": {"priority": 1}}}, "operation"),
    ({"by_name": {"x": move(first=True)}}, "operation"),
    ({"rules": {"a": 3}}, "operation"),
    ({"rules": {"a": {"_op": "replace"}}}, "operation"),
    ({"rules": {"a": {"_op": "delete", "priority": 1}}}, "operation"),
    ({"rules": {"a": {"_position": 0}}}, "operation"),
    ({"rules": {"a": {"_position": "middle"}}}, "operation"),
    ({"rules": {"a": {"_position": {"before": None}}}}, "selector"),
    ({"rules": {at(0): add()}}, "selector"),
    ({"hops": {"x": add({"ip": "10.0.0.2"})}}, "selector"),
    ({"hops": {"x": {"cost": 1}}}, "selector"),
    ({"hops": {"+": {"cost": 1}}}, "selector"),
    ({"links": {"Backup": add()}}, "selector"),
    ({"routes": {"10.0.0.0/24": {"cost": 5}}}, "selector"),
    ({"by_name": {at(0): {"v": 1}}}, "selector"),
    ({"by_name": {"+": add()}}, "selector"),
    ({"names": {"+": add()}}, "selector"),
    ({"rules": {"zz": {"priority": 1}}}, "no_item"),
    ({"names": {"zz": delete()}}, "no_item"),
    ({"hops": {at(9): delete()}}, "no_item"),
    ({"routes": {"10.9.0.0,24,192.168.1.1,GE3": {"cost": 5}}}, "no_item"),
    ({"rules": {"a": move(before="zz")}}, "no_item"),
    ({"by_name": {"zz": {"v": 1}}}, "no_item"),
    ({"links": {"Internet": {"family": "IPv6"}}}, "several_items"),
    ({"links": {("u1", "IPv4"): move(after="Internet")}}, "several_items"),
    ({"rules": {"b": add()}}, "exists"),
    ({"rules": {"i1": add()}}, "exists"),
    ({"rules": {"b": {"name": "a"}}}, "exists"),
    ({"rules": {"b": {"rule_id": "i1"}}}, "exists"),
    ({"rules": {"c": add({"rule_id": "i1"})}}, "exists"),
    ({"rules": {"b": {"name": "c"}, "c": add()}}, "exists"),
    ({"names": {"a": add()}}, "exists"),
    ({"links": {"+": add({"name": "x", "internal_id": "u1"})}}, "exists"),
    ({"by_name": {"x": add()}}, "exists"),
]


@pytest.mark.parametrize(("patch", "kind"), KINDS)
def test_each_refusal_has_its_kind(patch: dict[str, object], kind: str) -> None:
    with pytest.raises(PatchError) as exc:
        _ = base().merge_patch(patch)
    assert exc.value.kind == kind


def test_kinds_of_refusals_that_need_another_model_or_call() -> None:
    calls = [
        (
            lambda: ById(m={1: Sub()}).merge_patch({"m": {"1": {"v": 1}, 1: {"v": 2}}}),
            "duplicate",
        ),
        (
            lambda: SubOrHop(m={"x": Sub()}).merge_patch({"m": {"y": add()}}),
            "operation",
        ),
        (lambda: ById().merge_patch({"m": {"x": add()}}), "invalid_value"),
        (
            lambda: Segmented().merge_patch(
                {"segment_id": "Guest"}, resolvers=ResolverRegistry()
            ),
            "invalid_value",
        ),
        (lambda: nest_patch(Root, 1, path="nope"), "address"),
        (lambda: nest_patch(Root, 1, path="sub..v"), "address"),
        (lambda: nest_patch(Root, 1, path="count.x"), "address"),
        (lambda: nest_patch(Root, 1, segment="S"), "address"),
        (lambda: nest_patch(Root, {1, 2}, path="names"), "not_json"),
    ]
    for call, kind in calls:
        with pytest.raises(PatchError) as exc:
            _ = call()
        assert exc.value.kind == kind


def test_the_kinds_are_these_fourteen() -> None:
    assert set(get_args(PatchErrorKind.__value__)) == {kind for _, kind in KINDS} | {
        "address",
        "segment_rule",
        "fixed_list",
        "masked_secret",
    }
    assert len(get_args(PatchErrorKind.__value__)) == 14
