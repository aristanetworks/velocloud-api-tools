"""What a refusal says: the JSON form of each operation it names."""

import pytest
from pyvelo.core import PatchError, add, at, delete, move

from .patch_models import Hop, Link, Root, Route, Rule, Sub
from .test_patch_lists import ById

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
):
    with pytest.raises(PatchError) as exc:
        _ = base().merge_patch(patch)
    message = exc.value.message
    assert helper in message
    for name, form in FORMS.items():
        if name in message:
            assert form in message


def test_a_built_map_refusal_names_the_json_form():
    with pytest.raises(PatchError) as exc:
        _ = ById().merge_patch({"m": {"1": {"v": 1}}})
    assert '{"_op": "add", ...}' in exc.value.message


def test_the_null_refusal_does_not_speak_of_missing():
    with pytest.raises(PatchError) as exc:
        _ = base().merge_patch({"flag": None})
    assert exc.value.message == (
        "null is not a value of flag, and a change cannot unset it: "
        "give a value, or leave the key out"
    )
