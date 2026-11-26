import copy
from typing import Any

import pytest
from pydantic import MISSING
from pyvelo.client.module_write import plan_module
from pyvelo.client.schemas.configuration import FirewallConfigurationModule
from pyvelo.core import ConfigContext
from pyvelo.core.base import same_json
from pyvelo.core.layers import Edge
from pyvelo.core.rebase import Path, raw_changes, rebase
from pyvelo.core.response_check import PlannedChange, check_response
from pyvelo.testing import make_module_json

RAW: dict[str, Any] = {
    "segments": [],
    "syslog_forwarding": False,
    "firewall_logging_enabled": False,
    "n": 1,
}


def module() -> FirewallConfigurationModule[Edge]:
    row = make_module_json("firewall", copy.deepcopy(RAW))
    return FirewallConfigurationModule[Edge].model_validate(row)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (0, False, False),
        (1, True, False),
        (1.0, True, False),
        (False, False, True),
        (1, 1.0, True),
        (0, -0.0, True),
        (1, 1.5, False),
        ("1", 1, False),
        (None, False, False),
        (None, 0, False),
        ([1, {"y": True}], [1.0, {"y": True}], True),
        ([1, {"y": True}], [1, {"y": 1}], False),
        ({"a": 1}, {"a": 1, "b": None}, False),
        ([1], [1, 1], False),
        (MISSING, MISSING, True),
        (MISSING, None, False),
    ],
)
def test_same_json(a: object, b: object, same: bool) -> None:  # noqa: FBT001 (a table of cases)
    assert same_json(a, b) is same
    assert same_json(b, a) is same


def test_an_object_is_the_same_as_itself() -> None:
    nan = float("nan")
    box = [nan]
    assert same_json(nan, nan)
    assert same_json(box, box)
    assert same_json({"x": box}, {"x": box})
    held = {"x": nan}
    assert same_json(held, held)
    assert not same_json([float("nan")], [float("nan")])


def test_a_boolean_in_place_of_a_number_is_a_change() -> None:
    (change,) = raw_changes({"x": [1, {"y": 0}]}, {"x": [1, {"y": False}]})
    assert str(change.path) == "x"
    assert change.after == [1, {"y": False}]
    out = rebase({"x": 0, "u": 1}, {"x": 0}, {"x": False})
    assert out == {"x": False, "u": 1}
    assert isinstance(out, dict)
    assert out["x"] is False


def test_a_whole_float_in_place_of_its_int_is_no_change() -> None:
    assert raw_changes({"x": 1}, {"x": 1.0}) == []
    out = rebase({"x": 1, "u": 1}, {"x": 1}, {"x": 1.0})
    assert isinstance(out, dict)
    assert type(out["x"]) is int


def test_the_plan_writes_a_number_that_replaces_a_boolean() -> None:
    plan = plan_module(
        module(), {**RAW, "syslog_forwarding": 0}, level=ConfigContext.EDGE
    )
    assert plan.operation == "update"
    assert [str(c.path) for c in plan.changes] == ["syslog_forwarding"]
    assert type(plan.data["syslog_forwarding"]) is int
    same = plan_module(module(), {**RAW, "n": 1.0}, level=ConfigContext.EDGE)
    assert (same.operation, same.changes) == ("none", ())


def test_a_payload_that_gives_a_boolean_over_a_stored_number_writes_it() -> None:
    stored = {**RAW, "syslog_forwarding": 1}
    row = make_module_json("firewall", copy.deepcopy(stored))
    read = FirewallConfigurationModule[Edge].model_validate(row)
    plan = plan_module(
        read, {**RAW, "syslog_forwarding": True}, level=ConfigContext.EDGE
    )
    assert plan.operation == "update"
    assert [(str(c.path), c.before, c.after) for c in plan.changes] == [
        ("syslog_forwarding", 1, True)
    ]
    assert plan.data["syslog_forwarding"] is True
    by_model = FirewallConfigurationModule[Edge].model_validate(copy.deepcopy(row))
    by_model.data.syslog_forwarding.set_local(True)
    same = plan_module(by_model, level=ConfigContext.EDGE)
    assert (same.operation, same.changes) == ("none", ())
    assert type(same.data["syslog_forwarding"]) is int


def test_the_response_check_holds_the_vco_to_the_type() -> None:
    kept = check_response({"x": 0}, {"x": False}, {"x": 0})
    assert [str(c.path) for c in kept.not_applied] == ["x"]
    as_number = check_response({"x": 2}, {"x": True}, {"x": 1})
    assert [str(c.path) for c in as_number.not_applied] == ["x"]
    whole = check_response({"x": 2}, {"x": 1.0}, {"x": 1})
    assert ([str(c.path) for c in whole.applied], whole.not_applied) == (["x"], ())
    beside = check_response({"x": 0}, {"x": 0}, {"x": False})
    assert [(str(c.path), c.stored) for c in beside.server_changes] == [("x", False)]
    assert PlannedChange(Path(("x",)), 0, 1, stored=False).clash is True
