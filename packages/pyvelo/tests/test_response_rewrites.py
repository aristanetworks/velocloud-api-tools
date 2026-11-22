"""The response check's rewrites: values the VCO owns, defaults it adds back, IPv6 forms."""

from collections.abc import Iterable, Sequence
from typing import Annotated

import pytest
from pydantic import MISSING, Field
from pyvelo.core import KeyField, ServerOwned, VcoModel
from pyvelo.core.rebase import Path
from pyvelo.core.response_check import (
    PathPattern,
    PlannedChange,
    ResponseCheck,
    Rewrites,
    ServerChange,
    check_response,
    matches,
)

from .patch_models import Root


class Stamped(VcoModel):
    name: str
    stamp: Annotated[str | MISSING, ServerOwned()] = MISSING


class Box(VcoModel):
    stamp: Annotated[str | MISSING, ServerOwned()] = MISSING
    items: Annotated[list[Stamped], KeyField("name")] = Field(default_factory=list)
    plain: list[Stamped] = Field(default_factory=list)


class Unstamped(VcoModel):
    name: str
    stamp: str | MISSING = MISSING


class PlainBox(VcoModel):
    plain: list[Unstamped] = Field(default_factory=list)


def paths(xs: Iterable[PlannedChange | ServerChange]) -> list[str]:
    return [str(x.path) for x in xs]


def assert_paths(
    check: ResponseCheck,
    *,
    applied: Sequence[str] = (),
    not_applied: Sequence[str] = (),
    left_out: Sequence[str] = (),
    server_changes: Sequence[str] = (),
) -> None:
    """The paths of all four tuples; the order within one tuple is not asserted."""
    assert sorted(paths(check.applied)) == sorted(applied)
    assert sorted(paths(check.not_applied)) == sorted(not_applied)
    assert sorted(paths(check.left_out)) == sorted(left_out)
    assert sorted(paths(check.server_changes)) == sorted(server_changes)


def server_change_at(check: ResponseCheck, path: str) -> ServerChange:
    (change,) = [c for c in check.server_changes if c.path == path]
    return change


IPV6 = Rewrites(ipv6=True)
NOTE_READDED = Rewrites(readded=(("sub", "note"),))


def test_server_owned_field_is_left_out():
    check = check_response({"stamp": "s1"}, {"stamp": "s2"}, {"stamp": "s9"}, Box)
    assert check.applied == ()
    assert check.not_applied == ()
    assert paths(check.left_out) == ["stamp"]
    (change,) = check.server_changes
    assert change.path == "stamp"
    assert change.as_read == "s1"
    assert change.stored == "s9"


def test_server_owned_inside_an_added_item():
    check = check_response(
        {"items": []},
        {"items": [{"name": "a", "stamp": "s1"}]},
        {"items": [{"name": "a", "stamp": "s9"}]},
        Box,
    )
    assert_paths(check, applied=["items[a]"], server_changes=["items[a].stamp"])
    change = server_change_at(check, "items[a].stamp")
    assert change.as_read is MISSING
    assert change.stored == "s9"


def test_owned_key_kept_as_read_is_left_out_with_no_server_change():
    check = check_response({"stamp": "s1"}, {"stamp": "s2"}, {"stamp": "s1"}, Box)
    assert_paths(check, left_out=["stamp"])
    (change,) = check.left_out
    assert change.stored == "s1"


def test_owned_key_missing_from_an_added_item_is_no_server_change():
    check = check_response(
        {"items": []},
        {"items": [{"name": "a", "stamp": "s1"}]},
        {"items": [{"name": "a"}]},
        Box,
    )
    assert_paths(check, applied=["items[a]"])
    assert check.server_changes == ()


def test_server_owned_inside_an_unkeyed_model_list():
    sent = {"plain": [{"name": "a", "stamp": "s1"}]}
    stored = {"plain": [{"name": "a", "stamp": "s9"}]}

    check = check_response({"plain": []}, sent, stored, Box)
    assert_paths(check, applied=["plain"], server_changes=["plain[#0].stamp"])
    assert server_change_at(check, "plain[#0].stamp").stored == "s9"

    unmarked = check_response({"plain": []}, sent, stored, PlainBox)
    assert_paths(unmarked, not_applied=["plain"])


def test_server_owned_under_a_dropped_container_is_left_out():
    check = check_response(
        {"items": [{"name": "a", "stamp": "s1"}]},
        {"items": [{"name": "a", "stamp": "s2"}]},
        {},
        Box,
    )
    assert_paths(check, left_out=["items[a].stamp"], server_changes=["items"])
    (change,) = check.left_out
    assert change.intended == "s2"
    assert change.stored is MISSING


def test_the_same_on_an_unmarked_field_is_not_applied():
    check = check_response(
        {"rules": []},
        {"rules": [{"name": "a", "ruleId": "i1"}]},
        {"rules": [{"name": "a", "ruleId": "i9"}]},
        Root,
    )
    assert_paths(check, not_applied=["rules[a]"])


def test_server_owned_by_pattern():
    check = check_response(
        {"x": {"conn": {"name": "a"}}},
        {"x": {"conn": {"name": "b"}}},
        {"x": {"conn": {"name": "c"}}},
        rewrites=Rewrites(server_owned=(("**", "conn", "name"),)),
    )
    assert_paths(check, left_out=["x.conn.name"], server_changes=["x.conn.name"])


def test_ipv6_in_canonical_form_is_the_same_value():
    check = check_response(
        {"addr": "::"},
        {"addr": "2001:DB8::1/64"},
        {"addr": "2001:db8::1/64"},
        rewrites=IPV6,
    )
    assert_paths(check, applied=["addr"], server_changes=["addr"])
    (change,) = check.server_changes
    assert change.as_read == "::"
    assert change.stored == "2001:db8::1/64"


def test_without_the_flag_it_is_not_applied():
    check = check_response(
        {"addr": "::"}, {"addr": "2001:DB8::1/64"}, {"addr": "2001:db8::1/64"}
    )
    assert_paths(check, not_applied=["addr"])
    (change,) = check.not_applied
    assert change.clash is True


@pytest.mark.parametrize(("sent", "stored"), [("A", "a"), ("10.0.0.2", "10.0.0.02")])
def test_ipv6_flag_equates_nothing_else(sent: str, stored: str):
    check = check_response(
        {"addr": "::"}, {"addr": sent}, {"addr": stored}, rewrites=IPV6
    )
    assert_paths(check, not_applied=["addr"])


V6_ROUTE: dict[str, object] = {
    "dest": "2001:DB8:1::",
    "prefix": 64,
    "gw": "fe80::1",
    "intf": "GE3",
}
"""An item of ``Root.routes`` whose key holds an IPv6 address the VCO would lower-case."""
V6_ROUTE_PATH = 'routes["2001:DB8:1::",64,"fe80::1",GE3]'


def canonical(item: dict[str, object]) -> dict[str, object]:
    """*item* with its ``dest`` lower-cased, as the VCO stores an IPv6 address."""
    return {**item, "dest": str(item["dest"]).lower()}


def test_ipv6_key_in_canonical_form_pairs():
    check = check_response(
        {"routes": []},
        {"routes": [V6_ROUTE]},
        {"routes": [canonical(V6_ROUTE)]},
        Root,
        rewrites=IPV6,
    )
    assert_paths(
        check, applied=[V6_ROUTE_PATH], server_changes=[f"{V6_ROUTE_PATH}.dest"]
    )
    change = server_change_at(check, f"{V6_ROUTE_PATH}.dest")
    assert (change.as_read, change.stored) == (MISSING, "2001:db8:1::")


def test_ipv6_key_without_the_flag_does_not_pair():
    check = check_response(
        {"routes": []},
        {"routes": [V6_ROUTE]},
        {"routes": [canonical(V6_ROUTE)]},
        Root,
    )
    assert_paths(
        check,
        not_applied=[V6_ROUTE_PATH],
        server_changes=['routes["2001:db8:1::",64,"fe80::1",GE3]'],
    )


def test_ipv6_keys_pair_by_key_in_canonical_form_in_any_order():
    other = {**V6_ROUTE, "dest": "2001:DB8:2::"}
    other_path = 'routes["2001:DB8:2::",64,"fe80::1",GE3]'
    check = check_response(
        {"routes": []},
        {"routes": [V6_ROUTE, other]},
        {"routes": [canonical(other), canonical(V6_ROUTE)]},
        Root,
        rewrites=IPV6,
    )
    assert_paths(
        check,
        applied=[V6_ROUTE_PATH, other_path],
        server_changes=[f"{V6_ROUTE_PATH}.dest", f"{other_path}.dest", "routes"],
    )


def test_ipv6_keys_that_repeat_pair_in_order_in_canonical_form():
    sent = [{**V6_ROUTE, "cost": 1}, {**V6_ROUTE, "cost": 2}]
    check = check_response(
        {"routes": []},
        {"routes": sent},
        {"routes": [canonical(item) for item in sent]},
        Root,
        rewrites=IPV6,
    )
    assert_paths(
        check,
        applied=["routes[#0]", "routes[#1]"],
        server_changes=["routes[#0].dest", "routes[#1].dest"],
    )


def test_ipv6_alt_key_in_canonical_form_pairs():
    # The two rules share a name, so only the alt key tells which is which.
    sent = [
        {"name": "a", "ruleId": "2001:DB8::1"},
        {"name": "a", "ruleId": "2001:DB8::2"},
    ]
    stored = [
        {"name": "a", "ruleId": "2001:db8::2"},
        {"name": "a", "ruleId": "2001:db8::1"},
    ]
    check = check_response(
        {"rules": []}, {"rules": sent}, {"rules": stored}, Root, rewrites=IPV6
    )
    assert_paths(
        check,
        applied=["rules[#0]", "rules[#1]"],
        server_changes=["rules[#0].ruleId", "rules[#1].ruleId"],
    )
    assert server_change_at(check, "rules[#0].ruleId").stored == "2001:db8::1"


def test_refused_removal_of_an_ipv6_keyed_item_pairs_in_canonical_form():
    check = check_response(
        {"routes": [V6_ROUTE]},
        {"routes": []},
        {"routes": [canonical(V6_ROUTE)]},
        Root,
        rewrites=IPV6,
    )
    assert_paths(check, not_applied=[V6_ROUTE_PATH])


def test_ipv6_flag_pairs_no_other_key_text():
    check = check_response(
        {"routes": []},
        {"routes": [V6_ROUTE]},
        {"routes": [{**V6_ROUTE, "intf": "ge3"}]},
        Root,
        rewrites=IPV6,
    )
    assert_paths(
        check,
        not_applied=[V6_ROUTE_PATH],
        server_changes=['routes["2001:DB8:1::",64,"fe80::1",ge3]'],
    )


def test_removed_key_the_vco_adds_back():
    check = check_response(
        {"sub": {"v": 1, "note": "n"}},
        {"sub": {"v": 1}},
        {"sub": {"v": 1, "note": "d"}},
        Root,
        rewrites=NOTE_READDED,
    )
    assert_paths(check, left_out=["sub.note"], server_changes=["sub.note"])
    change = server_change_at(check, "sub.note")
    assert change.as_read == "n"
    assert change.stored == "d"


def test_the_same_without_the_pattern_is_not_applied():
    check = check_response(
        {"sub": {"v": 1, "note": "n"}},
        {"sub": {"v": 1}},
        {"sub": {"v": 1, "note": "d"}},
        Root,
    )
    assert_paths(check, not_applied=["sub.note"])


def test_readded_under_a_keyed_item():
    check = check_response(
        {"rules": [{"name": "a", "sub": {"v": 1}}]},
        {"rules": [{"name": "a"}]},
        {"rules": [{"name": "a", "sub": {"v": 0}}]},
        Root,
        rewrites=Rewrites(readded=(("rules", "*", "sub"),)),
    )
    assert_paths(check, left_out=["rules[a].sub"], server_changes=["rules[a].sub"])


def test_removed_and_gone_is_applied_all_the_same():
    check = check_response(
        {"sub": {"v": 1, "note": "n"}},
        {"sub": {"v": 1}},
        {"sub": {"v": 1}},
        Root,
        rewrites=NOTE_READDED,
    )
    assert_paths(check, applied=["sub.note"])


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        (("segments", "*", "ecmp"), "segments[Guest].ecmp", True),
        (("segments", "*", "ecmp"), "segments[#0].ecmp", True),
        (("segments", "*", "ecmp"), "segments.ecmp", False),
        (("segments", "*", "ecmp"), "segments[Guest].x.ecmp", False),
        (("**", "fips"), "fips", True),
        (("**", "fips"), "a.b[#1].fips", True),
        (("**", "fips"), "fips.x", False),
    ],
)
def test_matches(pattern: PathPattern, path: str, expected: bool):
    assert matches(Path.parse(path), pattern) is expected


def test_removed_key_the_vco_adds_back_inside_a_list_compared_whole():
    check = check_response(
        {"hops": [{"ip": "10.0.0.1", "cost": 5}]},
        {"hops": [{"ip": "10.0.0.1"}]},
        {"hops": [{"ip": "10.0.0.1", "cost": 0}]},
        Root,
        rewrites=Rewrites(readded=(("hops", "*", "cost"),)),
    )
    assert check.not_applied == ()
    assert paths(check.applied) == ["hops"]
    change = server_change_at(check, "hops[#0].cost")
    assert (change.as_read, change.stored) == (MISSING, 0)


def test_owned_key_removed_inside_a_list_compared_whole_and_kept_by_the_vco():
    check = check_response(
        {"plain": [{"name": "a", "stamp": "s1"}]},
        {"plain": [{"name": "b"}]},
        {"plain": [{"name": "b", "stamp": "s1"}]},
        Box,
    )
    assert check.not_applied == ()
    assert paths(check.applied) == ["plain"]
    assert paths(check.server_changes) == ["plain[#0].stamp"]


def test_server_change_below_a_list_compared_whole_has_no_value_read():
    check = check_response(
        {"hops": [{"ip": "2001:DB8::1"}]},
        {"hops": [{"ip": "2001:DB8::2"}]},
        {"hops": [{"ip": "2001:db8::2"}]},
        Root,
        rewrites=IPV6,
    )
    assert_paths(check, applied=["hops"], server_changes=["hops[#0].ip"])
    change = server_change_at(check, "hops[#0].ip")
    assert (change.as_read, change.stored) == (MISSING, "2001:db8::2")
