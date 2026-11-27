"""``apply(path=...)`` takes a path as pyvelo shows it.

A path's parts map one for one onto patch keys: a field is a key, and the part
after a list or a map is the selector of one of its items. So a path a plan or
an error prints, given back as ``path``, reaches the place it names.
"""

from typing import Annotated, Any

import pytest
from hub_state import rule_uses_hub
from pydantic import Field
from pyvelo.client.models.configuration.profile_segments import segment_template
from pyvelo.config import ConfigPipeline, PatchError
from pyvelo.core import KeyField, VcoModel, add, named_path
from pyvelo.core.merge import nest_patch
from pyvelo.core.rebase import Key, Path
from shown_paths import reached, round_trips

apply = ConfigPipeline(client=None).apply

RULE = "segments[Guest].rules[Web]"
GUEST = 1
"""The position of the segment ``Guest`` in each module of ``rule_uses_hub``."""


async def test_parts_map_one_for_one_onto_patch_keys() -> None:
    state = await rule_uses_hub()
    cls = type(state.profile.qos)
    assert nest_patch(cls, 443, path=f"{RULE}.match.destination_port_low") == {
        "segments": {
            "Guest": {"rules": {"Web": {"match": {"destination_port_low": 443}}}}
        }
    }
    assert nest_patch(cls, "x", path="segments[Guest].rules[#0].name") == {
        "segments": {"Guest": {"rules": {"#0": {"name": "x"}}}}
    }
    assert nest_patch(cls, 1, segment="Guest", path="rules.Web.match.dport_low") == {
        "segments": {
            "Guest": {"rules": {"Web": {"match": {"destination_port_low": 1}}}}
        }
    }


async def test_a_shown_path_reaches_the_rule() -> None:
    state = await rule_uses_hub()
    _ = await apply(
        state,
        module="business_policy",
        path=Path.parse(f"{RULE}.match"),
        patch={"destination_port_low": 443},
    )
    assert state.profile.qos.segments[1].rules[0].match.destination_port_low == 443


async def test_an_item_path_below_a_segment_reaches_the_rule() -> None:
    state = await rule_uses_hub()
    _ = await apply(
        state,
        module="business_policy",
        segment="Guest",
        path="rules[Web].match",
        patch={"destination_port_low": 443},
    )
    assert state.profile.qos.segments[1].rules[0].match.destination_port_low == 443


async def test_an_item_path_with_a_delete_removes_the_item() -> None:
    state = await rule_uses_hub()
    hub = f"{RULE}.action.edge_to_edge_route_action.route_cfg.backhaul_edges[Hub-A]"
    _ = await apply(state, module="business_policy", path=hub, patch={"_op": "delete"})
    rule = state.profile.qos.segments[1].rules[0]
    assert rule.action.edge_to_edge_route_action.route_cfg.backhaul_edges == []


async def test_a_missing_item_is_the_patchs_no_item() -> None:
    state = await rule_uses_hub()
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            path="segments[Guest].rules[Nope]",
            patch={},
        )
    assert (error.value.kind, error.value.path) == (
        "no_item",
        "segments[Guest].rules[Nope]",
    )


async def test_a_path_that_does_not_parse_is_refused_with_the_form() -> None:
    state = await rule_uses_hub()
    with pytest.raises(PatchError) as error:
        _ = await apply(state, module="business_policy", path="rules[Web", patch={})
    assert error.value.kind == "address"
    assert "segments[Guest].rules[Web].match" in str(error.value)


async def test_an_item_part_where_no_list_is_is_refused() -> None:
    state = await rule_uses_hub()
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state, module="device_settings", path="segments[Guest].vpn[Web]", patch={}
        )
    assert (error.value.kind, error.value.path) == (
        "address",
        "segments[Guest].vpn[Web]",
    )
    assert "is not a list" in error.value.message


def _rule(name: str) -> dict[str, Any]:
    """The body of an ``add`` of a QoS rule named *name*."""
    body = segment_template("QOS")["rules"][0]
    body["name"] = name
    return body


async def test_a_quoted_item_name_reaches_its_item() -> None:
    """A rule named ``Corp.Guest v2``, then one named ``a]b``, then ``say "x"``."""
    state = await rule_uses_hub()
    names = ("Corp.Guest v2", "a]b", 'say "x"')
    for name in names:
        _ = await apply(
            state,
            module="business_policy",
            segment="Guest",
            path="rules",
            patch={"+": add(_rule(name))},
        )
    data = state.profile.qos
    before = [rule.match.destination_port_low for rule in data.segments[GUEST].rules]
    for position, name in enumerate(names, start=1):
        shown = named_path(Path(("segments", GUEST, "rules", position, "match")), data)
        assert shown.parts[:4] == ("segments", Key("Guest"), "rules", Key(name))
        round_trips(shown)
        port = 8000 + position
        _ = await apply(
            state,
            module="business_policy",
            path=str(shown),
            patch={"destination_port_low": port},
        )
        assert reached(data, shown.child("destination_port_low")) == port
    after = [rule.match.destination_port_low for rule in data.segments[GUEST].rules]
    assert after == [before[0], 8001, 8002, 8003]
    assert [rule.name for rule in data.segments[GUEST].rules] == ["Web", *names]


async def test_a_composite_key_path_reaches_its_item() -> None:
    """A syslog collector (a number part), then a static route (text parts)."""
    state = await rule_uses_hub()
    for host in ("10.0.0.1", "10.0.0.2"):
        _ = await apply(
            state,
            module="device_settings",
            segment="Guest",
            path="syslog.collectors",
            patch={"+": add({"host": host, "protocol": "UDP", "port": 514})},
        )
    for destination in ("10.0.0.0", "10.9.0.0"):
        route = (destination, "24", "192.168.1.1", "GE3")
        _ = await apply(
            state,
            module="device_settings",
            segment="Guest",
            path="routes.static",
            patch={route: add({"netmask": "255.255.255.0"})},
        )
    data = state.profile.device_settings

    collector = named_path(Path(("segments", GUEST, "syslog", "collectors", 0)), data)
    assert collector == 'segments[Guest].syslog.collectors["10.0.0.1",UDP,514]'
    round_trips(collector)
    _ = await apply(
        state, module="device_settings", path=str(collector), patch={"tag": "branch"}
    )
    assert reached(data, collector.child("tag")) == "branch"
    assert reached(data, collector.child("port")) == 514
    other = 'segments[Guest].syslog.collectors["10.0.0.2",UDP,514].tag'
    assert reached(data, Path.parse(other)) == ""

    route = named_path(Path(("segments", GUEST, "routes", "static", 0)), data)
    assert route == 'segments[Guest].routes.static["10.0.0.0","24","192.168.1.1",GE3]'
    round_trips(route)
    _ = await apply(state, module="device_settings", path=str(route), patch={"cost": 5})
    assert reached(data, route.child("cost")) == 5
    other = 'segments[Guest].routes.static["10.9.0.0","24","192.168.1.1",GE3].cost'
    assert reached(data, Path.parse(other)) == 0


# --- reached: the walk the round trips of the shown paths are checked with ---


class _Item(VcoModel):
    name: str
    size: int = 0


class _Shelf(VcoModel):
    items: Annotated[list[_Item], KeyField("name")] = Field(default_factory=list)
    spare: _Item | None = None


def _shelf(*names: str) -> _Shelf:
    return _Shelf(
        items=[_Item(name=name, size=len(name)) for name in names],
        spare=_Item(name="spare", size=5),
    )


def test_reached_gives_the_value_at_a_path() -> None:
    shelf = _shelf("a", "bcd")
    assert reached(shelf, Path.parse("items[bcd].size")) == 3
    assert reached(shelf, Path.parse("items[#0]")) is shelf.items[0]
    assert reached(shelf, Path.parse("items.bcd")) is shelf.items[1]
    assert reached(shelf, Path.parse("items")) is shelf.items


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("items[nope].size", r"\[nope\] of items\[nope\]\.size selects 0 items"),
        ("items[twin].size", r"\[twin\] of items\[twin\]\.size selects 2 items"),
        ("spare[a]", r"\[a\] of spare\[a\]: an item part that follows no list"),
        ("nope", "nope of nope: _Shelf has no such attribute"),
        ("spare.size.x", r"x of spare\.size\.x: there is no model there, but 5"),
    ],
)
def test_reached_fails_where_a_part_does_not_name_one_thing(
    path: str, why: str
) -> None:
    shelf = _shelf("a", "twin", "twin")
    with pytest.raises(AssertionError, match=why):
        _ = reached(shelf, Path.parse(path))
