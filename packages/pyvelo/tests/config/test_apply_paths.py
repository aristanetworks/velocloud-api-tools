"""``apply(path=...)`` takes a path as pyvelo shows it.

A path's parts map one for one onto patch keys: a field is a key, and the part
after a list or a map is the selector of one of its items. So a path a plan or
an error prints, given back as ``path``, reaches the place it names.
"""

from typing import Annotated, Any

import pytest
from hub_state import rule_uses_hub
from profile_state import profile_state
from pydantic import Field
from pyvelo.client.models.configuration.profile_segments import segment_template
from pyvelo.client.schemas.configuration import EnterpriseConfigurationProfile
from pyvelo.config import ConfigPipeline, ConfigState, PatchError
from pyvelo.config.resolver import ResolverContext
from pyvelo.core import KeyField, VcoModel, add, address_path, named_path
from pyvelo.core.base import is_list
from pyvelo.core.layers import Profile
from pyvelo.core.merge import nest_patch
from pyvelo.core.rebase import Key, Path
from pyvelo.testing import make_firewall_module, make_firewall_segment
from segment_helpers import GLOBAL, ds_entry, firewall_entry, qos_entry
from segment_helpers import GUEST as GUEST_SEGMENT
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


async def test_a_segment_at_the_head_of_a_path_stands_for_segment() -> None:
    by_path = await rule_uses_hub()
    by_segment = await rule_uses_hub()
    body = {"destination_port_low": 443}
    _ = await apply(by_path, module="business_policy", path=f"{RULE}.match", patch=body)
    _ = await apply(
        by_segment,
        module="business_policy",
        segment="Guest",
        path="rules[Web].match",
        patch=body,
    )
    assert by_path.profile.qos == by_segment.profile.qos
    assert by_path.profile.qos.segments[GUEST].rules[0].match.destination_port_low == (
        443
    )
    cls = type(by_path.profile.qos)
    assert address_path(None, f"{RULE}.match") == address_path(
        "Guest", "rules[Web].match"
    )
    assert nest_patch(cls, body, path=f"{RULE}.match") == nest_patch(
        cls, body, segment="Guest", path="rules[Web].match"
    )


async def test_a_segment_given_twice_is_refused() -> None:
    state = await rule_uses_hub()
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            segment="Guest",
            path="segments[Guest].rules",
            patch={},
        )
    assert error.value.kind == "address"
    assert str(error.value) == (
        "the segment is given twice: by segment='Guest' and by the path, which "
        "starts at segments[Guest]. One of the two would be dropped without a "
        "word. Give it once: leave out segment, or start the path below the "
        "segment (path='rules')"
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


def _firewall_only() -> ConfigState:
    """A profile that has a firewall module and neither of the other two."""
    segment = make_firewall_segment("Global Segment", 0, layer=Profile)
    firewall = make_firewall_module(
        mod_id=1, config_id=20, segments=[segment], layer=Profile
    )
    return ConfigState(
        edge_config=None,
        profile_config=EnterpriseConfigurationProfile(
            name="Default Profile", id_=20, modules=[firewall]
        ),
        target_type="profile",
        resolver_context=ResolverContext(),
    )


@pytest.mark.parametrize(
    ("segment", "path", "patch"),
    [
        ("Guest", None, add({"rules": []})),
        (None, "segments[Guest]", add({"rules": []})),
        (None, "segments.Guest", add({"rules": []})),
        (None, Path(("segments", Key("Guest"))), {"_op": "delete"}),
        (None, "segments", {"Guest": add({"rules": []})}),
        (None, "segments", []),
    ],
)
async def test_segment_ops_by_path_to_a_missing_module_are_refused(
    segment: str | None, path: str | None, patch: object
) -> None:
    """As ``segment="Guest"`` with the same body is: build the state that test uses."""
    state = _firewall_only()
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state, module="business_policy", segment=segment, path=path, patch=patch
        )
    assert (error.value.kind, error.value.path) == ("segment_rule", "segments")
    assert error.value.message.startswith("the profile has no")
    assert error.value.message.startswith("the profile has no business_policy module")
    assert state.find_module("QOS") is None


@pytest.mark.parametrize("path", ["segments[Guest].rules", "service_rate_limit"])
async def test_a_patch_by_path_with_no_segment_op_is_left_to_the_missing_module(
    path: str,
) -> None:
    state = _firewall_only()
    with pytest.raises(ValueError, match="QOS") as error:
        _ = await apply(state, module="business_policy", path=path, patch={})
    assert not isinstance(error.value, PatchError)


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


# --- a position part, where the text ``#n`` is also a key or a value ---------

TWIN = "f6f6f6f6-f6f6-4f6f-8f6f-f6f6f6f6f6f6"
DNS_SERVICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
ADDRESSES = 'segments[Guest].dns.local_dns["a.example"].ipv4_address'


async def _twins(first: str, second: str = "dup") -> ConfigState:
    """Guest has a rule and a local DNS address *first*, then two nothing tells apart.

    The rules are *first*, *second* and ``dup``, the last two with one
    logical ID; the addresses of ``a.example`` are *first* and two ``a``. So
    unless *second* is given, position 1 of each list has no key, name or
    value of its own. Guest's primary DNS provider is one object, its private
    providers a list of one.
    """
    twin = _rule("dup") | {"ruleLogicalId": TWIN}
    rules = [_rule(first), twin | {"name": second}, twin]
    guest = ds_entry(GUEST_SEGMENT)
    guest["dns"] = {
        "primaryProvider": {
            "logicalId": DNS_SERVICE,
            "ref": "deviceSettings:dns:primaryProvider",
        },
        "backupProvider": [],
        "privateProviders": [{"logicalId": DNS_SERVICE}],
        "localDNS": [{"fqdn": "a.example", "ipv4Address": [first, "a", "a"]}],
    }
    return await profile_state(
        [ds_entry(GLOBAL), guest],
        [qos_entry(GLOBAL), qos_entry(GUEST_SEGMENT, rules=rules)],
        [firewall_entry(GLOBAL), firewall_entry(GUEST_SEGMENT)],
        segments=(GLOBAL, GUEST_SEGMENT),
    )


async def test_a_position_whose_text_is_the_key_of_another_item_is_refused() -> None:
    state = await _twins("#1")
    data = state.profile.qos
    shown = named_path(Path(("segments", GUEST, "rules", 1, "match")), data)
    assert shown == "segments[Guest].rules[#1].match"
    before = data.model_dump()
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            path=str(shown),
            patch={"destination_port_low": 443},
        )
    assert (error.value.kind, error.value.path) == (
        "address",
        "segments[Guest].rules[#1]",
    )
    assert error.value.message == (
        "[#1] is position 1 of QosSegment[Profile].rules, but as a patch key "
        "the text '#1' is read first as the key of an item, and the list has "
        "that key at position 0: the patch would go there, not to position 1, "
        "without a word. Nothing names the item at position 1 alone, so change "
        "it in a patch of the whole list: end the path at rules and give the "
        "list as the patch. To reach the item whose key is '#1', write it in "
        'quotes: rules["#1"]'
    )
    assert data.model_dump() == before
    assert state.get_field_changes() == []


async def test_a_position_whose_text_is_the_value_of_another_entry_is_refused() -> None:
    state = await _twins("#1")
    data = state.profile.device_settings
    place = Path(("segments", GUEST, "dns", "local_dns", 0, "ipv4_address", 1))
    shown = named_path(place, data)
    assert shown == f"{ADDRESSES}[#1]"
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state, module="device_settings", path=str(shown), patch={"_op": "delete"}
        )
    assert (error.value.kind, error.value.path) == ("address", shown)
    assert error.value.message == (
        "[#1] is position 1 of LocalDomainItem.ipv4_address, but as a patch key "
        "the text '#1' is read first as the value of an entry, and the list has "
        "that value at position 0: the patch would go there, not to position 1, "
        "without a word. Nothing names the entry at position 1 alone, so change "
        "it in a patch of the whole list: end the path at ipv4_address and give "
        "the list as the patch. To reach the entry whose value is '#1', write "
        'it in quotes: ipv4_address["#1"]'
    )
    assert reached(data, Path.parse(ADDRESSES)) == ["#1", "a", "a"]
    assert state.get_field_changes() == []


async def test_a_refused_position_says_how_to_reach_each_item() -> None:
    state = await _twins("#1", second="Web")
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            path="segments[Guest].rules[#1].match",
            patch={"destination_port_low": 443},
        )
    assert (error.value.kind, error.value.path) == (
        "address",
        "segments[Guest].rules[#1]",
    )
    assert error.value.message.endswith(
        "without a word. Name the item at position 1 by what names it alone: "
        "rules[Web]. To reach the item whose key is '#1', write it in quotes: "
        'rules["#1"]'
    )

    # The way to the refused position is named as pyvelo shows it.
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            path=f"segments[#{GUEST}].rules[#1].match",
            patch={"destination_port_low": 443},
        )
    assert (error.value.kind, error.value.path) == (
        "address",
        "segments[Guest].rules[#1]",
    )

    state = await _twins("#9")
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state, module="business_policy", path="segments[Guest].rules[#9]", patch={}
        )
    assert error.value.kind == "address"
    assert error.value.message.endswith(
        "the list has that key at position 0: the patch would go there, not to "
        "position 9, without a word. The list has no position 9. To reach the "
        "item whose key is '#9', write it in quotes: rules[\"#9\"]"
    )


async def test_a_refused_position_is_at_its_path_with_fields_by_attribute() -> None:
    state = await _twins("#1")
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="device_settings",
            path="segments[#1].dns.localDNS[#0].ipv4Address[#1]",
            patch={"_op": "delete"},
        )
    assert error.value.path == (
        'segments[Guest].dns.local_dns["a.example"].ipv4_address[#1]'
    )


async def test_a_position_no_other_item_has_as_its_text_reaches_that_position() -> None:
    state = await _twins("first")
    rules = named_path(
        Path(("segments", GUEST, "rules", 1, "match")), state.profile.qos
    )
    assert rules == "segments[Guest].rules[#1].match"
    _ = await apply(
        state,
        module="business_policy",
        path=str(rules),
        patch={"destination_port_low": 443},
    )
    ports = [
        rule.match.destination_port_low
        for rule in state.profile.qos.segments[GUEST].rules
    ]
    assert ports == [-1, 443, -1]
    assert reached(state.profile.qos, rules.child("destination_port_low")) == 443

    data = state.profile.device_settings
    _ = await apply(
        state,
        module="device_settings",
        path=f"{ADDRESSES}[#1]",
        patch={"_op": "delete"},
    )
    assert reached(data, Path.parse(ADDRESSES)) == ["first", "a"]


async def test_a_quoted_position_text_is_read_as_a_patch_key_is() -> None:
    state = await _twins("#1")
    for path, port in (
        ('segments[Guest].rules["#1"].match', 1001),  # the rule named ``#1``
        ('segments[Guest].rules["#2"].match', 1002),  # no rule is named ``#2``
    ):
        _ = await apply(
            state,
            module="business_policy",
            path=path,
            patch={"destination_port_low": port},
        )
    ports = [
        rule.match.destination_port_low
        for rule in state.profile.qos.segments[GUEST].rules
    ]
    assert ports == [1001, -1, 1002]


async def test_a_position_the_data_lacks_is_left_to_the_merge() -> None:
    state = await _twins("#1")
    for path in ("segments[Guest].rules[#9].match", "segments[#9].rules[#1].match"):
        with pytest.raises(PatchError) as error:
            _ = await apply(state, module="business_policy", path=path, patch={})
        assert error.value.kind == "no_item"


# --- a field that holds one model or a list of them ---------------------------


async def test_a_provider_held_as_one_object_is_reached_through_its_fields() -> None:
    state = await _twins("first")
    data = state.profile.device_settings
    provider = Path.parse("segments[Guest].dns.primary_provider")
    assert reached(data, provider.child("ref")) == "deviceSettings:dns:primaryProvider"
    assert named_path(Path.parse("segments[#1].dns.primaryProvider.ref"), data) == (
        provider.child("ref")
    )
    providers = reached(data, Path.parse("segments[Guest].dns.private_providers"))
    assert is_list(providers)
    listed = Path.parse("segments[Guest].dns.private_providers[#0]")
    assert reached(data, listed) is providers[0]
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state, module="device_settings", path=f"{provider}.nope", patch=1
        )
    assert (error.value.kind, error.value.path) == ("unknown_field", f"{provider}.nope")


# --- a patch error names an item as pyvelo shows it ----------------------------


async def test_an_error_below_an_item_names_it_as_pyvelo_shows_it() -> None:
    state = await rule_uses_hub()
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            patch={"segments": {"Guest": {"rules": {"#0": {"bogus": 1}}}}},
        )
    assert str(error.value) == (
        "segments[Guest].rules[Web].bogus: QosRule has no field 'bogus'"
    )
    round_trips(error.value.path)


async def test_an_item_that_does_not_exist_keeps_the_selector_written() -> None:
    """A guard."""
    state = await rule_uses_hub()
    with pytest.raises(PatchError) as error:
        _ = await apply(
            state,
            module="business_policy",
            patch={"segments": {"Guest": {"rules": {"Nope": {"name": "x"}}}}},
        )
    assert str(error.value).startswith("segments[Guest].rules[Nope]: no item has")


@pytest.mark.parametrize(
    ("address", "patch", "shown"),
    [
        (
            {},
            {"segments": {"Guest": {"rules": [{"bogus": 1}]}}},
            "segments[Guest].rules[#0].bogus: QosRule has no field 'bogus'",
        ),
        (
            {"path": "segments[Guest].rules"},
            [float("nan")],
            "segments[Guest].rules[#0]: nan is not a JSON number",
        ),
    ],
    ids=["merge", "address"],
)
async def test_an_item_of_a_list_value_is_not_named_by_the_item_read_there(
    address: dict[str, str], patch: object, shown: str
) -> None:
    """The first item of the value is not the rule ``Web`` the data has there."""
    state = await rule_uses_hub()
    with pytest.raises(PatchError) as error:
        _ = await apply(state, module="business_policy", patch=patch, **address)
    assert str(error.value).startswith(shown)


# --- reached: the walk the round trips of the shown paths are checked with ---


class _Item(VcoModel):
    name: str
    size: int = 0


class _Shelf(VcoModel):
    items: Annotated[list[_Item], KeyField("name")] = Field(default_factory=list)
    spare: _Item | None = None


class _Either(VcoModel):
    either: _Item | list[_Item] = Field(default_factory=list)


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


def test_reached_reads_a_position_as_that_position_whatever_the_keys() -> None:
    shelf = _shelf("#1", "b", "#0")
    assert reached(shelf, Path.parse("items[#1]")) is shelf.items[1]
    assert reached(shelf, Path.parse("items[#0]")) is shelf.items[0]
    # A key that is the text of a position is a key.
    assert reached(shelf, Path.parse('items["#1"]')) is shelf.items[0]
    assert reached(shelf, Path.parse('items["#0"]')) is shelf.items[2]


def test_reached_walks_one_model_or_a_list_by_what_is_held() -> None:
    one = _Either(either=_Item(name="solo", size=4))
    assert reached(one, Path.parse("either.size")) == 4
    many = _Either(either=[_Item(name="a"), _Item(name="b", size=2)])
    assert reached(many, Path.parse("either[#1].size")) == 2
    with pytest.raises(AssertionError, match=r"\[#1\] of either\[#1\]"):
        _ = reached(one, Path.parse("either[#1]"))
    with pytest.raises(AssertionError, match=r"size of either\.size selects 0 items"):
        _ = reached(many, Path.parse("either.size"))


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("items[nope].size", r"\[nope\] of items\[nope\]\.size selects 0 items"),
        ("items[twin].size", r"\[twin\] of items\[twin\]\.size selects 2 items"),
        ("items[#3].size", r"\[#3\] of items\[#3\]\.size: .*has 3 items"),
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
